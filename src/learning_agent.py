"""Learning agent for Polymarket sports arbitrage.

Continuously monitors trade outcomes and learns over time to improve
edge estimation, market type profitability, sport selection, and timing.
Persists all history to data/learning_history.json.
"""
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, stdev

log = logging.getLogger(__name__)


@dataclass
class TradeOutcome:
    """Record of a single resolved trade."""
    token_id: str
    slug: str
    sport: str
    market_type: str          # h2h, spreads, totals
    outcome: str              # e.g. "Team A" or "Over 2.5"
    entry_price: float        # price paid on Polymarket (0-1)
    fair_prob_at_entry: float  # sharp book implied probability at entry
    edge_pct_at_entry: float  # edge % at time of entry
    shares: float
    cost_usdc: float
    bookmaker: str            # e.g. "pinnacle", "betfair_exchange"
    opened_at: str            # ISO-8601 UTC
    resolved_at: str          # ISO-8601 UTC
    won: bool
    pnl: float               # net P&L in USDC
    resolution_price: float   # 1.0 if won, 0.0 if lost


# ---------------------------------------------------------------------------
# Edge buckets and price buckets used for calibration
# ---------------------------------------------------------------------------
_EDGE_BUCKETS = [
    ("3-5%",  3.0,  5.0),
    ("5-8%",  5.0,  8.0),
    ("8-15%", 8.0, 15.0),
    ("15%+", 15.0, 999.0),
]

_PRICE_BUCKETS = [
    ("0-10c",  0.00, 0.10),
    ("10-20c", 0.10, 0.20),
    ("20-40c", 0.20, 0.40),
    ("40-60c", 0.40, 0.60),
]


def _bucket_label(value: float, buckets: list[tuple]) -> str:
    """Return the label for the bucket that contains *value*."""
    for label, lo, hi in buckets:
        if lo <= value < hi:
            return label
    return buckets[-1][0]  # fallback to last bucket


def _bucket_stats(trades: list[dict]) -> dict:
    """Compute win rate, count, and avg_pnl for a list of trade dicts."""
    if not trades:
        return {"win_rate": 0.0, "count": 0, "avg_pnl": 0.0, "total_pnl": 0.0}
    wins = sum(1 for t in trades if t["won"])
    pnls = [t["pnl"] for t in trades]
    return {
        "win_rate": wins / len(trades),
        "count": len(trades),
        "avg_pnl": mean(pnls),
        "total_pnl": sum(pnls),
    }


class LearningAgent:
    """Learns from trade outcomes to improve edge estimation and allocation.

    All history is persisted to ``data/learning_history.json`` so the agent
    retains knowledge across restarts.

    Key features
    -------------
    * **Calibration stats** by sport, market type, edge bucket, price bucket,
      bookmaker, and hour of day.
    * **Adaptive edge adjustment** -- once enough samples accumulate for a
      given sport + market_type + price_bucket, the raw edge from the scanner
      is nudged toward the realised hit rate.
    * **Sport scoring** for capital-allocation weighting.
    * **Merge opportunity** detection (YES + NO < $1 arb).
    """

    MIN_SAMPLES = 20        # minimum trades before adjusting edge
    LEARNING_RATE = 0.3     # how aggressively to adjust (0 = ignore, 1 = full)

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.history_path = self.data_dir / "learning_history.json"

        self.trades: list[dict] = []
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self):
        """Load trade history from disk."""
        if self.history_path.exists():
            try:
                with open(self.history_path, "r") as f:
                    data = json.load(f)
                self.trades = data.get("trades", [])
                log.info("Loaded %d historical trades from %s",
                         len(self.trades), self.history_path)
            except (json.JSONDecodeError, KeyError) as exc:
                log.warning("Corrupted history file, starting fresh: %s", exc)
                self.trades = []
        else:
            log.info("No history file found at %s, starting fresh",
                     self.history_path)

    def save(self):
        """Persist trade history to disk."""
        payload = {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "trade_count": len(self.trades),
            "trades": self.trades,
        }
        tmp = self.history_path.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(payload, f, indent=2)
        tmp.replace(self.history_path)
        log.debug("Saved %d trades to %s", len(self.trades), self.history_path)

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_outcome(self, outcome: TradeOutcome):
        """Record a resolved trade and persist to disk.

        Parameters
        ----------
        outcome : TradeOutcome
            The fully resolved trade outcome.
        """
        rec = asdict(outcome)
        self.trades.append(rec)
        log.info("Recorded outcome: %s [%s] won=%s pnl=$%.2f (total: %d trades)",
                 outcome.slug, outcome.outcome, outcome.won, outcome.pnl,
                 len(self.trades))
        self.save()

    # ------------------------------------------------------------------
    # Calibration statistics
    # ------------------------------------------------------------------

    def _group_by(self, key_fn) -> dict[str, list[dict]]:
        """Group trades by an arbitrary key function."""
        groups: dict[str, list[dict]] = {}
        for t in self.trades:
            k = key_fn(t)
            groups.setdefault(k, []).append(t)
        return groups

    def win_rate_by_sport(self) -> dict[str, dict]:
        """Win rate, count, and avg_pnl broken down by sport."""
        groups = self._group_by(lambda t: t["sport"])
        return {sport: _bucket_stats(trades) for sport, trades in groups.items()}

    def win_rate_by_market_type(self) -> dict[str, dict]:
        """Win rate, count, and avg_pnl broken down by market type (h2h / spreads / totals)."""
        groups = self._group_by(lambda t: t["market_type"])
        return {mt: _bucket_stats(trades) for mt, trades in groups.items()}

    def win_rate_by_edge_bucket(self) -> dict[str, dict]:
        """Win rate, count, and avg_pnl bucketed by edge % at entry."""
        groups = self._group_by(
            lambda t: _bucket_label(t["edge_pct_at_entry"], _EDGE_BUCKETS)
        )
        return {b: _bucket_stats(trades) for b, trades in groups.items()}

    def win_rate_by_price_bucket(self) -> dict[str, dict]:
        """Win rate, count, and avg_pnl bucketed by entry price."""
        groups = self._group_by(
            lambda t: _bucket_label(t["entry_price"], _PRICE_BUCKETS)
        )
        return {b: _bucket_stats(trades) for b, trades in groups.items()}

    def win_rate_by_bookmaker(self) -> dict[str, dict]:
        """Win rate, count, and avg_pnl by sharp bookmaker source."""
        groups = self._group_by(lambda t: t["bookmaker"])
        return {bk: _bucket_stats(trades) for bk, trades in groups.items()}

    def profitable_hours(self) -> dict[int, dict]:
        """P&L statistics by hour of day (UTC) when the trade was opened."""
        def _hour(t: dict) -> int:
            try:
                dt = datetime.fromisoformat(t["opened_at"])
                return dt.hour
            except (ValueError, KeyError):
                return -1

        groups = self._group_by(lambda t: str(_hour(t)))
        return {int(h): _bucket_stats(trades) for h, trades in groups.items() if h != "-1"}

    # ------------------------------------------------------------------
    # Edge adjustment (key learning feature)
    # ------------------------------------------------------------------

    def adjusted_edge(
        self,
        raw_edge_pct: float,
        sport: str,
        market_type: str,
        entry_price: float,
        bookmaker: str,
    ) -> float:
        """Return an edge adjusted by historical calibration.

        If there are fewer than ``MIN_SAMPLES`` trades in the relevant bucket,
        the raw edge is returned unchanged.

        The adjustment compares the *actual* win rate in the bucket to the
        *predicted* win rate (mean entry price for those trades, which
        approximates the implied probability).

        Parameters
        ----------
        raw_edge_pct : float
            Scanner-estimated edge in percent.
        sport : str
            Sport key (e.g. "epl", "nba").
        market_type : str
            Market type ("h2h", "spreads", "totals").
        entry_price : float
            Polymarket price (0-1).
        bookmaker : str
            Sharp bookmaker used for fair value.

        Returns
        -------
        float
            Adjusted edge in percent (never below 0).
        """
        price_bucket = _bucket_label(entry_price, _PRICE_BUCKETS)

        # Find matching trades
        subset = [
            t for t in self.trades
            if t["sport"] == sport
            and t["market_type"] == market_type
            and _bucket_label(t["entry_price"], _PRICE_BUCKETS) == price_bucket
        ]

        if len(subset) < self.MIN_SAMPLES:
            log.debug("adjusted_edge: only %d trades for %s/%s/%s, returning raw %.2f%%",
                      len(subset), sport, market_type, price_bucket, raw_edge_pct)
            return raw_edge_pct

        actual_win_rate = sum(1 for t in subset if t["won"]) / len(subset)
        predicted_win_rate = mean(t["entry_price"] for t in subset)

        if predicted_win_rate <= 0:
            return raw_edge_pct

        adjustment = (actual_win_rate - predicted_win_rate) / predicted_win_rate
        adjusted = raw_edge_pct * (1.0 + adjustment * self.LEARNING_RATE)

        log.debug(
            "adjusted_edge: %s/%s/%s | actual_wr=%.1f%% predicted_wr=%.1f%% "
            "adj=%.3f | raw=%.2f%% -> %.2f%%",
            sport, market_type, price_bucket,
            actual_win_rate * 100, predicted_win_rate * 100,
            adjustment, raw_edge_pct, adjusted,
        )
        return max(adjusted, 0.0)

    # ------------------------------------------------------------------
    # Sport scoring for allocation
    # ------------------------------------------------------------------

    def sport_scores(self) -> dict[str, float]:
        """Composite score per sport for capital allocation weighting.

        Score = win_rate * log2(count + 1) * (1 + avg_edge / 100)

        Higher scores suggest the sport has been reliably profitable.
        Sports with fewer than 5 trades get a neutral score of 1.0.

        Returns
        -------
        dict[str, float]
            Sport -> score mapping (higher is better).
        """
        import math
        stats = self.win_rate_by_sport()
        scores: dict[str, float] = {}
        for sport, s in stats.items():
            if s["count"] < 5:
                scores[sport] = 1.0
                continue
            subset = [t for t in self.trades if t["sport"] == sport]
            avg_edge = mean(t["edge_pct_at_entry"] for t in subset)
            scores[sport] = s["win_rate"] * math.log2(s["count"] + 1) * (1.0 + avg_edge / 100.0)
        return scores

    # ------------------------------------------------------------------
    # Merge opportunity detection
    # ------------------------------------------------------------------

    @staticmethod
    def merge_opportunity(yes_price: float, no_price: float) -> dict:
        """Detect a merge arbitrage when YES + NO < $1.

        On Polymarket neg-risk markets, buying 1 YES share and 1 NO share
        and merging them always pays out exactly $1. If the combined cost
        is below $1, the difference is risk-free profit.

        Parameters
        ----------
        yes_price : float
            Current best-ask price for the YES token (0-1).
        no_price : float
            Current best-ask price for the NO token (0-1).

        Returns
        -------
        dict
            ``{"edge": float, "profit_per_pair": float}`` if an arb exists,
            otherwise an empty dict.
        """
        combined = yes_price + no_price
        if combined >= 1.0 or combined <= 0:
            return {}
        profit_per_pair = 1.0 - combined
        edge_pct = (profit_per_pair / combined) * 100.0
        return {
            "edge": edge_pct,
            "profit_per_pair": profit_per_pair,
        }

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def export_metrics(self) -> dict:
        """Export all learning metrics as a dict for logging/monitoring.

        Returns
        -------
        dict
            Comprehensive metrics dictionary.
        """
        total = len(self.trades)
        wins = sum(1 for t in self.trades if t["won"])
        total_pnl = sum(t["pnl"] for t in self.trades)

        return {
            "total_trades": total,
            "total_wins": wins,
            "total_losses": total - wins,
            "win_rate": wins / total if total else 0.0,
            "total_pnl": total_pnl,
            "avg_pnl": total_pnl / total if total else 0.0,
            "by_sport": self.win_rate_by_sport(),
            "by_market_type": self.win_rate_by_market_type(),
            "by_edge_bucket": self.win_rate_by_edge_bucket(),
            "by_price_bucket": self.win_rate_by_price_bucket(),
            "by_bookmaker": self.win_rate_by_bookmaker(),
            "by_hour": self.profitable_hours(),
            "sport_scores": self.sport_scores(),
        }

    def print_report(self):
        """Print a formatted summary of all learning metrics to the log."""
        metrics = self.export_metrics()

        lines = [
            "",
            "=" * 70,
            "  LEARNING AGENT REPORT",
            "=" * 70,
            f"  Total trades:  {metrics['total_trades']}",
            f"  Win rate:      {metrics['win_rate']:.1%}  "
            f"({metrics['total_wins']}W / {metrics['total_losses']}L)",
            f"  Total PnL:     ${metrics['total_pnl']:,.2f}",
            f"  Avg PnL/trade: ${metrics['avg_pnl']:,.2f}",
            "",
        ]

        # Sport breakdown
        lines.append("  --- By Sport ---")
        for sport, s in sorted(metrics["by_sport"].items(),
                               key=lambda x: -x[1]["total_pnl"]):
            lines.append(
                f"    {sport:<12s}  WR={s['win_rate']:.0%}  "
                f"n={s['count']:>4d}  avg=${s['avg_pnl']:>+7.2f}  "
                f"total=${s['total_pnl']:>+9.2f}"
            )

        # Market type breakdown
        lines.append("")
        lines.append("  --- By Market Type ---")
        for mt, s in sorted(metrics["by_market_type"].items(),
                            key=lambda x: -x[1]["total_pnl"]):
            lines.append(
                f"    {mt:<12s}  WR={s['win_rate']:.0%}  "
                f"n={s['count']:>4d}  avg=${s['avg_pnl']:>+7.2f}"
            )

        # Edge bucket breakdown
        lines.append("")
        lines.append("  --- By Edge Bucket ---")
        for bucket in ["3-5%", "5-8%", "8-15%", "15%+"]:
            s = metrics["by_edge_bucket"].get(bucket, {"win_rate": 0, "count": 0, "avg_pnl": 0})
            if s["count"] > 0:
                lines.append(
                    f"    {bucket:<10s}  WR={s['win_rate']:.0%}  "
                    f"n={s['count']:>4d}  avg=${s['avg_pnl']:>+7.2f}"
                )

        # Price bucket breakdown
        lines.append("")
        lines.append("  --- By Price Bucket ---")
        for bucket in ["0-10c", "10-20c", "20-40c", "40-60c"]:
            s = metrics["by_price_bucket"].get(bucket, {"win_rate": 0, "count": 0, "avg_pnl": 0})
            if s["count"] > 0:
                lines.append(
                    f"    {bucket:<10s}  WR={s['win_rate']:.0%}  "
                    f"n={s['count']:>4d}  avg=${s['avg_pnl']:>+7.2f}"
                )

        # Bookmaker breakdown
        lines.append("")
        lines.append("  --- By Bookmaker ---")
        for bk, s in sorted(metrics["by_bookmaker"].items(),
                            key=lambda x: -x[1]["total_pnl"]):
            lines.append(
                f"    {bk:<20s}  WR={s['win_rate']:.0%}  "
                f"n={s['count']:>4d}  avg=${s['avg_pnl']:>+7.2f}"
            )

        # Hour of day
        lines.append("")
        lines.append("  --- Profitable Hours (UTC) ---")
        hour_stats = metrics["by_hour"]
        for h in range(24):
            if h in hour_stats and hour_stats[h]["count"] > 0:
                s = hour_stats[h]
                bar = "+" * max(1, int(abs(s["total_pnl"]) / 10)) if s["total_pnl"] > 0 else \
                      "-" * max(1, int(abs(s["total_pnl"]) / 10))
                lines.append(
                    f"    {h:02d}:00  n={s['count']:>3d}  "
                    f"pnl=${s['total_pnl']:>+8.2f}  {bar}"
                )

        # Sport scores
        lines.append("")
        lines.append("  --- Sport Scores (allocation weight) ---")
        for sport, score in sorted(metrics["sport_scores"].items(),
                                   key=lambda x: -x[1]):
            lines.append(f"    {sport:<12s}  score={score:.3f}")

        lines.append("")
        lines.append("=" * 70)

        log.info("\n".join(lines))
