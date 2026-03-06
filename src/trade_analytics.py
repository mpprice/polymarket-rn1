"""Comprehensive trade analytics for evaluating true edge.

Answers the question: "Is our edge real?" by computing calibration metrics,
performance statistics, edge stability, and closing line value tracking.

All calculations use only the standard library (no numpy/scipy/pandas).
All public methods return dicts/lists suitable for dashboard consumption.
"""
import math
import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_stdev(values: list[float]) -> float:
    """Standard deviation with Bessel's correction, returns 0.0 if < 2 values."""
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    var = sum((x - m) ** 2 for x in values) / (n - 1)
    return math.sqrt(var)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _linear_regression(xs: list[float], ys: list[float]) -> dict:
    """Simple OLS: y = a + b*x. Returns slope, intercept, r_squared."""
    n = len(xs)
    if n < 2:
        return {"slope": 0.0, "intercept": 0.0, "r_squared": 0.0}
    mx = sum(xs) / n
    my = sum(ys) / n
    ss_xx = sum((x - mx) ** 2 for x in xs)
    ss_xy = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    ss_yy = sum((y - my) ** 2 for y in ys)
    if ss_xx == 0:
        return {"slope": 0.0, "intercept": my, "r_squared": 0.0}
    b = ss_xy / ss_xx
    a = my - b * mx
    r_sq = (ss_xy ** 2) / (ss_xx * ss_yy) if ss_yy != 0 else 0.0
    return {"slope": b, "intercept": a, "r_squared": r_sq}


def _parse_dt(iso_str: str) -> Optional[datetime]:
    """Parse ISO-8601 string to datetime, returns None on failure."""
    try:
        return datetime.fromisoformat(iso_str)
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Main analytics class
# ---------------------------------------------------------------------------

class TradeAnalytics:
    """Comprehensive analytics for evaluating edge quality.

    Takes a list of trade dicts (same schema as LearningAgent.trades) and
    computes all metrics on demand. Stateless -- create a new instance
    whenever the trade list is updated, or call methods with fresh data.

    Parameters
    ----------
    trades : list[dict]
        List of resolved trade records from the learning agent.
    """

    def __init__(self, trades: list[dict]):
        self.trades = trades
        self._resolved = [t for t in trades if t.get("pnl") is not None]

    @property
    def n(self) -> int:
        return len(self._resolved)

    # ==================================================================
    # TRUE EDGE ANALYSIS
    # ==================================================================

    def calibration_curve(self, n_buckets: int = 10) -> list[dict]:
        """Predicted win rate vs actual win rate in equal-width probability buckets.

        Returns a list of dicts, one per bucket, with:
        - bucket_mid: midpoint of the bucket
        - predicted_wr: mean entry price (implied probability) in that bucket
        - actual_wr: observed win rate in that bucket
        - count: number of trades in bucket
        - edge: actual_wr - predicted_wr (positive = we beat the market)
        """
        if not self._resolved:
            return []

        bucket_width = 1.0 / n_buckets
        buckets: dict[int, list[dict]] = defaultdict(list)

        for t in self._resolved:
            idx = min(int(t["entry_price"] / bucket_width), n_buckets - 1)
            buckets[idx].append(t)

        result = []
        for idx in range(n_buckets):
            trades = buckets.get(idx, [])
            if not trades:
                continue
            predicted = _mean([t["entry_price"] for t in trades])
            actual = sum(1 for t in trades if t["won"]) / len(trades)
            result.append({
                "bucket_mid": (idx + 0.5) * bucket_width,
                "predicted_wr": predicted,
                "actual_wr": actual,
                "count": len(trades),
                "edge": actual - predicted,
            })
        return result

    def brier_score(self, sport: str = None, market_type: str = None) -> float:
        """Brier Score: mean squared error of probability predictions.

        Lower is better. Perfect = 0.0, worst = 1.0.
        For sports betting, < 0.2 is good; < 0.15 is excellent.

        Parameters
        ----------
        sport : str, optional
            Filter to a specific sport.
        market_type : str, optional
            Filter to a specific market type.

        Returns
        -------
        float
            The Brier score, or -1.0 if no trades match.
        """
        subset = self._filter(sport=sport, market_type=market_type)
        if not subset:
            return -1.0
        # Our predicted probability is the fair_prob_at_entry
        # The outcome is 1 (won) or 0 (lost)
        errors = []
        for t in subset:
            predicted = t.get("fair_prob_at_entry", t["entry_price"])
            actual = 1.0 if t["won"] else 0.0
            errors.append((predicted - actual) ** 2)
        return sum(errors) / len(errors)

    def brier_score_breakdown(self) -> dict:
        """Brier score overall and by sport and market_type.

        Returns
        -------
        dict
            {"overall": float, "by_sport": {str: float}, "by_market_type": {str: float}}
        """
        result = {"overall": self.brier_score()}
        sports = set(t["sport"] for t in self._resolved)
        mtypes = set(t["market_type"] for t in self._resolved)
        result["by_sport"] = {s: self.brier_score(sport=s) for s in sports}
        result["by_market_type"] = {m: self.brier_score(market_type=m) for m in mtypes}
        return result

    def log_loss(self, sport: str = None, market_type: str = None) -> float:
        """Log loss (cross-entropy) of probability predictions.

        Lower is better. Perfect = 0.0.

        Returns
        -------
        float
            Log loss, or -1.0 if no trades match.
        """
        subset = self._filter(sport=sport, market_type=market_type)
        if not subset:
            return -1.0
        eps = 1e-15  # clamp to avoid log(0)
        total = 0.0
        for t in subset:
            p = max(eps, min(1 - eps, t.get("fair_prob_at_entry", t["entry_price"])))
            y = 1.0 if t["won"] else 0.0
            total += -(y * math.log(p) + (1 - y) * math.log(1 - p))
        return total / len(subset)

    # ==================================================================
    # PERFORMANCE METRICS
    # ==================================================================

    def sharpe_ratio(self, annualize: bool = True) -> float:
        """Annualized Sharpe ratio of daily PnL.

        Returns
        -------
        float
            Sharpe ratio, or 0.0 if insufficient data.
        """
        daily = self.daily_pnl_series()
        if len(daily) < 2:
            return 0.0
        pnls = [d["pnl"] for d in daily]
        m = _mean(pnls)
        s = _safe_stdev(pnls)
        if s == 0:
            return 0.0 if m == 0 else float("inf")
        ratio = m / s
        if annualize:
            ratio *= math.sqrt(365)
        return ratio

    def max_drawdown(self) -> dict:
        """Largest peak-to-trough decline in cumulative PnL.

        Returns
        -------
        dict
            {"max_drawdown": float, "max_drawdown_pct": float,
             "peak_date": str, "trough_date": str}
        """
        cum = self.cumulative_pnl_series()
        if not cum:
            return {"max_drawdown": 0.0, "max_drawdown_pct": 0.0,
                    "peak_date": "", "trough_date": ""}
        peak = cum[0]["cum_pnl"]
        peak_date = cum[0]["date"]
        max_dd = 0.0
        dd_peak_date = peak_date
        dd_trough_date = peak_date

        for point in cum:
            if point["cum_pnl"] > peak:
                peak = point["cum_pnl"]
                peak_date = point["date"]
            dd = peak - point["cum_pnl"]
            if dd > max_dd:
                max_dd = dd
                dd_peak_date = peak_date
                dd_trough_date = point["date"]

        peak_val = 0.0
        for point in cum:
            if point["date"] == dd_peak_date:
                peak_val = point["cum_pnl"]
                break

        return {
            "max_drawdown": max_dd,
            "max_drawdown_pct": (max_dd / peak_val * 100) if peak_val > 0 else 0.0,
            "peak_date": dd_peak_date,
            "trough_date": dd_trough_date,
        }

    def profit_factor(self) -> float:
        """Gross profit / gross loss. > 1.0 means profitable.

        Returns
        -------
        float
            Profit factor, or float('inf') if no losses, or 0.0 if no trades.
        """
        gross_profit = sum(t["pnl"] for t in self._resolved if t["pnl"] > 0)
        gross_loss = abs(sum(t["pnl"] for t in self._resolved if t["pnl"] < 0))
        if gross_loss == 0:
            return float("inf") if gross_profit > 0 else 0.0
        return gross_profit / gross_loss

    def avg_winner_loser(self) -> dict:
        """Average winning trade size vs average losing trade size.

        Returns
        -------
        dict
            {"avg_winner": float, "avg_loser": float, "ratio": float}
        """
        winners = [t["pnl"] for t in self._resolved if t["pnl"] > 0]
        losers = [t["pnl"] for t in self._resolved if t["pnl"] < 0]
        aw = _mean(winners) if winners else 0.0
        al = _mean(losers) if losers else 0.0
        ratio = abs(aw / al) if al != 0 else float("inf") if aw > 0 else 0.0
        return {"avg_winner": aw, "avg_loser": al, "ratio": ratio,
                "n_winners": len(winners), "n_losers": len(losers)}

    def streaks(self) -> dict:
        """Max consecutive wins and losses, plus current streak.

        Returns
        -------
        dict
            {"max_win_streak": int, "max_loss_streak": int,
             "current_streak_type": str, "current_streak_length": int}
        """
        if not self._resolved:
            return {"max_win_streak": 0, "max_loss_streak": 0,
                    "current_streak_type": "none", "current_streak_length": 0}

        # Sort by resolved_at
        sorted_trades = sorted(self._resolved,
                               key=lambda t: t.get("resolved_at", ""))
        max_w = 0
        max_l = 0
        cur = 0
        cur_type = "none"

        for t in sorted_trades:
            if t["won"]:
                if cur_type == "win":
                    cur += 1
                else:
                    cur_type = "win"
                    cur = 1
                max_w = max(max_w, cur)
            else:
                if cur_type == "loss":
                    cur += 1
                else:
                    cur_type = "loss"
                    cur = 1
                max_l = max(max_l, cur)

        return {
            "max_win_streak": max_w,
            "max_loss_streak": max_l,
            "current_streak_type": cur_type,
            "current_streak_length": cur,
        }

    def return_on_capital(self) -> dict:
        """Total PnL / max exposure used.

        Returns
        -------
        dict
            {"total_pnl": float, "max_exposure": float, "roc_pct": float}
        """
        total_pnl = sum(t["pnl"] for t in self._resolved)
        max_exposure = max((t["cost_usdc"] for t in self._resolved), default=0.0)
        # Better: running exposure over time
        cumulative_exposure = sum(t["cost_usdc"] for t in self._resolved)
        return {
            "total_pnl": total_pnl,
            "max_single_exposure": max_exposure,
            "total_capital_deployed": cumulative_exposure,
            "roc_pct": (total_pnl / cumulative_exposure * 100) if cumulative_exposure > 0 else 0.0,
        }

    def time_weighted_return(self) -> float:
        """Simple IRR approximation: total return / average capital * annualization.

        Returns
        -------
        float
            Annualized return percentage.
        """
        if not self._resolved:
            return 0.0

        dates = []
        for t in self._resolved:
            dt = _parse_dt(t.get("opened_at", ""))
            if dt:
                dates.append(dt)
        if len(dates) < 2:
            return 0.0

        first = min(dates)
        last = max(dates)
        days = (last - first).total_seconds() / 86400
        if days < 1:
            return 0.0

        total_pnl = sum(t["pnl"] for t in self._resolved)
        avg_capital = _mean([t["cost_usdc"] for t in self._resolved])
        if avg_capital <= 0:
            return 0.0

        period_return = total_pnl / avg_capital
        annualized = period_return * (365.0 / days)
        return annualized * 100.0

    # ==================================================================
    # EDGE STABILITY
    # ==================================================================

    def rolling_edge(self, window: int = 20) -> list[dict]:
        """Rolling edge over the last N trades.

        Returns a list of dicts with trade index and rolling edge value.
        Edge is defined as actual_win_rate - predicted_win_rate over the window.

        Parameters
        ----------
        window : int
            Rolling window size.

        Returns
        -------
        list[dict]
            [{"trade_idx": int, "rolling_wr": float, "rolling_predicted": float,
              "rolling_edge": float, "rolling_pnl": float}]
        """
        sorted_trades = sorted(self._resolved,
                               key=lambda t: t.get("resolved_at", ""))
        if len(sorted_trades) < window:
            return []

        result = []
        for i in range(window, len(sorted_trades) + 1):
            chunk = sorted_trades[i - window:i]
            actual_wr = sum(1 for t in chunk if t["won"]) / window
            predicted_wr = _mean([t["entry_price"] for t in chunk])
            pnl = sum(t["pnl"] for t in chunk)
            result.append({
                "trade_idx": i,
                "rolling_wr": actual_wr,
                "rolling_predicted": predicted_wr,
                "rolling_edge": actual_wr - predicted_wr,
                "rolling_pnl": pnl,
            })
        return result

    def edge_trend(self, window: int = 20) -> dict:
        """Linear regression of rolling edge. Is our edge improving?

        Returns
        -------
        dict
            {"slope": float, "r_squared": float, "improving": bool,
             "edge_change_per_100_trades": float}
        """
        rolling = self.rolling_edge(window)
        if len(rolling) < 3:
            return {"slope": 0.0, "r_squared": 0.0, "improving": False,
                    "edge_change_per_100_trades": 0.0}

        xs = [float(r["trade_idx"]) for r in rolling]
        ys = [r["rolling_edge"] for r in rolling]
        reg = _linear_regression(xs, ys)
        return {
            "slope": reg["slope"],
            "r_squared": reg["r_squared"],
            "improving": reg["slope"] > 0,
            "edge_change_per_100_trades": reg["slope"] * 100,
        }

    def edge_by_hour(self) -> dict[int, dict]:
        """PnL and edge statistics by UTC hour of entry.

        Returns
        -------
        dict[int, dict]
            Hour -> {"count": int, "win_rate": float, "avg_pnl": float,
                     "total_pnl": float, "avg_edge": float}
        """
        by_hour: dict[int, list[dict]] = defaultdict(list)
        for t in self._resolved:
            dt = _parse_dt(t.get("opened_at", ""))
            if dt:
                by_hour[dt.hour].append(t)

        result = {}
        for h, trades in sorted(by_hour.items()):
            result[h] = {
                "count": len(trades),
                "win_rate": sum(1 for t in trades if t["won"]) / len(trades),
                "avg_pnl": _mean([t["pnl"] for t in trades]),
                "total_pnl": sum(t["pnl"] for t in trades),
                "avg_edge": _mean([t["edge_pct_at_entry"] for t in trades]),
            }
        return result

    def edge_by_weekday(self) -> dict[str, dict]:
        """PnL and edge statistics by day of week.

        Returns
        -------
        dict[str, dict]
            Day name -> {"count": int, "win_rate": float, "avg_pnl": float,
                        "total_pnl": float}
        """
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday",
                     "Friday", "Saturday", "Sunday"]
        by_day: dict[int, list[dict]] = defaultdict(list)
        for t in self._resolved:
            dt = _parse_dt(t.get("opened_at", ""))
            if dt:
                by_day[dt.weekday()].append(t)

        result = {}
        for d_idx, trades in sorted(by_day.items()):
            result[day_names[d_idx]] = {
                "count": len(trades),
                "win_rate": sum(1 for t in trades if t["won"]) / len(trades),
                "avg_pnl": _mean([t["pnl"] for t in trades]),
                "total_pnl": sum(t["pnl"] for t in trades),
            }
        return result

    def edge_by_sport(self) -> dict[str, dict]:
        """Detailed edge statistics by sport.

        Returns
        -------
        dict[str, dict]
            Sport -> {"count": int, "win_rate": float, "avg_pnl": float,
                     "total_pnl": float, "sharpe": float, "avg_edge": float,
                     "edge_consistency": float}
        """
        by_sport: dict[str, list[dict]] = defaultdict(list)
        for t in self._resolved:
            by_sport[t["sport"]].append(t)

        result = {}
        for sport, trades in sorted(by_sport.items()):
            pnls = [t["pnl"] for t in trades]
            s = _safe_stdev(pnls)
            m = _mean(pnls)
            sharpe = (m / s * math.sqrt(365)) if s > 0 else 0.0

            # Edge consistency: what fraction of rolling windows are positive
            wins_in_windows = 0
            total_windows = 0
            for i in range(5, len(trades) + 1):
                chunk = trades[i - 5:i]
                if sum(t["pnl"] for t in chunk) > 0:
                    wins_in_windows += 1
                total_windows += 1

            result[sport] = {
                "count": len(trades),
                "win_rate": sum(1 for t in trades if t["won"]) / len(trades),
                "avg_pnl": m,
                "total_pnl": sum(pnls),
                "sharpe": sharpe,
                "avg_edge": _mean([t["edge_pct_at_entry"] for t in trades]),
                "edge_consistency": (wins_in_windows / total_windows
                                     if total_windows > 0 else 0.0),
            }
        return result

    # ==================================================================
    # CLV (CLOSING LINE VALUE) TRACKING
    # ==================================================================

    def clv_analysis(self) -> dict:
        """Closing Line Value analysis.

        CLV is computed when we have both entry odds and closing odds.
        Trades must have a 'closing_price' field (from the trade journal).

        CLV > 0 consistently = genuine edge. This is the gold standard
        metric for evaluating sports bettors.

        Returns
        -------
        dict
            {"avg_clv": float, "clv_positive_pct": float, "count": int,
             "clv_by_sport": dict}
        """
        trades_with_clv = [t for t in self._resolved
                           if t.get("closing_price") is not None]
        if not trades_with_clv:
            return {"avg_clv": 0.0, "clv_positive_pct": 0.0, "count": 0,
                    "clv_by_sport": {}, "note": "No closing prices recorded yet"}

        clvs = []
        by_sport: dict[str, list[float]] = defaultdict(list)
        for t in trades_with_clv:
            # CLV = (closing_price - entry_price) / entry_price * 100
            # If we bought at 0.40 and closing line moved to 0.45,
            # CLV = +12.5% -- market agreed with us
            entry = t["entry_price"]
            closing = t["closing_price"]
            if entry > 0:
                clv = (closing - entry) / entry * 100
                clvs.append(clv)
                by_sport[t["sport"]].append(clv)

        clv_by_sport = {}
        for sport, vals in by_sport.items():
            clv_by_sport[sport] = {
                "avg_clv": _mean(vals),
                "positive_pct": sum(1 for v in vals if v > 0) / len(vals) * 100,
                "count": len(vals),
            }

        return {
            "avg_clv": _mean(clvs),
            "clv_positive_pct": sum(1 for v in clvs if v > 0) / len(clvs) * 100,
            "count": len(clvs),
            "clv_by_sport": clv_by_sport,
        }

    # ==================================================================
    # DAILY PnL SERIES
    # ==================================================================

    def daily_pnl_series(self) -> list[dict]:
        """Daily PnL series for charting.

        Returns
        -------
        list[dict]
            [{"date": "YYYY-MM-DD", "pnl": float, "n_trades": int}]
            sorted by date ascending.
        """
        by_date: dict[str, list[float]] = defaultdict(list)
        for t in self._resolved:
            dt = _parse_dt(t.get("resolved_at", ""))
            if dt:
                day = dt.strftime("%Y-%m-%d")
                by_date[day].append(t["pnl"])

        result = []
        for day in sorted(by_date.keys()):
            pnls = by_date[day]
            result.append({
                "date": day,
                "pnl": sum(pnls),
                "n_trades": len(pnls),
            })
        return result

    def cumulative_pnl_series(self) -> list[dict]:
        """Cumulative PnL over time for charting.

        Returns
        -------
        list[dict]
            [{"date": "YYYY-MM-DD", "cum_pnl": float, "daily_pnl": float}]
        """
        daily = self.daily_pnl_series()
        cum = 0.0
        result = []
        for d in daily:
            cum += d["pnl"]
            result.append({
                "date": d["date"],
                "cum_pnl": cum,
                "daily_pnl": d["pnl"],
            })
        return result

    # ==================================================================
    # UTILITY
    # ==================================================================

    def _filter(self, sport: str = None, market_type: str = None) -> list[dict]:
        """Filter resolved trades by sport and/or market_type."""
        subset = self._resolved
        if sport:
            subset = [t for t in subset if t["sport"] == sport]
        if market_type:
            subset = [t for t in subset if t["market_type"] == market_type]
        return subset

    def best_worst_trades(self, n: int = 5) -> dict:
        """Top N best and worst trades by PnL.

        Returns
        -------
        dict
            {"best": list[dict], "worst": list[dict]}
        """
        sorted_by_pnl = sorted(self._resolved, key=lambda t: t["pnl"])
        worst = sorted_by_pnl[:n]
        best = sorted_by_pnl[-n:][::-1]
        # Return summary for each
        def _summary(t: dict) -> dict:
            return {
                "slug": t.get("slug", ""),
                "sport": t["sport"],
                "market_type": t["market_type"],
                "outcome": t.get("outcome", ""),
                "entry_price": t["entry_price"],
                "edge_pct": t["edge_pct_at_entry"],
                "pnl": t["pnl"],
                "won": t["won"],
                "opened_at": t.get("opened_at", ""),
                "resolved_at": t.get("resolved_at", ""),
            }
        return {
            "best": [_summary(t) for t in best],
            "worst": [_summary(t) for t in worst],
        }

    def full_report(self) -> dict:
        """Generate comprehensive analytics report as a dict.

        Suitable for JSON serialization and dashboard consumption.

        Returns
        -------
        dict
            Complete analytics report.
        """
        return {
            "n_trades": self.n,
            "calibration": {
                "curve": self.calibration_curve(),
                "brier_score": self.brier_score_breakdown(),
                "log_loss": self.log_loss(),
            },
            "performance": {
                "sharpe_ratio": self.sharpe_ratio(),
                "max_drawdown": self.max_drawdown(),
                "profit_factor": self.profit_factor(),
                "avg_winner_loser": self.avg_winner_loser(),
                "streaks": self.streaks(),
                "return_on_capital": self.return_on_capital(),
                "time_weighted_return_pct": self.time_weighted_return(),
            },
            "edge_stability": {
                "rolling_edge_20": self.rolling_edge(20),
                "edge_trend": self.edge_trend(),
                "by_hour": self.edge_by_hour(),
                "by_weekday": self.edge_by_weekday(),
                "by_sport": self.edge_by_sport(),
            },
            "clv": self.clv_analysis(),
            "best_worst": self.best_worst_trades(),
            "daily_pnl": self.daily_pnl_series(),
            "cumulative_pnl": self.cumulative_pnl_series(),
        }
