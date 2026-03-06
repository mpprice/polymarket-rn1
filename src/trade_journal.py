"""Detailed trade journal for recording everything about each trade.

Append-only, crash-safe JSON journal. Each entry captures the full context
at the time of the trade: entry conditions, market context, resolution,
CLV, and the learning agent's state.

File: data/trade_journal.json (one JSON object per line, JSONL format
for crash safety -- each line is independently parseable).
"""
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


class TradeJournal:
    """Append-only trade journal with crash-safe persistence.

    Uses JSON Lines (JSONL) format: one JSON object per line. This ensures
    that even if the process crashes mid-write, at most one entry is lost.

    Parameters
    ----------
    data_dir : str
        Directory for the journal file. Defaults to "data".
    """

    def __init__(self, data_dir: str = "data"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.journal_path = self.data_dir / "trade_journal.jsonl"

    # ------------------------------------------------------------------
    # Writing entries
    # ------------------------------------------------------------------

    def record_entry(
        self,
        *,
        # Trade identification
        token_id: str,
        slug: str,
        outcome: str,
        trade_id: str = "",
        # Entry conditions
        entry_price: float,
        fair_prob: float,
        edge_pct: float,
        bookmaker: str,
        bookmaker_odds: float = 0.0,
        bookmaker_prob: float = 0.0,
        # Sizing
        shares: float,
        cost_usdc: float,
        kelly_fraction_used: float = 0.0,
        # Market context
        sport: str = "",
        market_type: str = "h2h",
        commence_time: str = "",
        time_to_event_hours: float = 0.0,
        neg_risk: bool = False,
        line: float = 0.0,
        # Orderbook
        orderbook_best_bid: float = 0.0,
        orderbook_best_ask: float = 0.0,
        orderbook_spread: float = 0.0,
        orderbook_depth_1pct: float = 0.0,
        # Learning state at entry
        learning_adjusted_edge: float = 0.0,
        learning_sport_score: float = 0.0,
        learning_bucket_win_rate: float = 0.0,
        learning_total_trades: int = 0,
        # Merge-specific
        is_merge: bool = False,
        merge_yes_price: float = 0.0,
        merge_no_price: float = 0.0,
        merge_profit_per_pair: float = 0.0,
    ) -> dict:
        """Record a new trade entry to the journal.

        All parameters with defaults are optional -- provide what's available.

        Returns
        -------
        dict
            The full journal entry that was written.
        """
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "journal_type": "ENTRY",
            "timestamp": now,
            "trade_id": trade_id or f"{token_id}_{now}",
            "token_id": token_id,
            "slug": slug,
            "outcome": outcome,
            # Entry conditions
            "entry_price": entry_price,
            "fair_prob": fair_prob,
            "edge_pct": edge_pct,
            "bookmaker": bookmaker,
            "bookmaker_odds": bookmaker_odds,
            "bookmaker_prob": bookmaker_prob,
            # Sizing
            "shares": shares,
            "cost_usdc": cost_usdc,
            "kelly_fraction_used": kelly_fraction_used,
            # Market context
            "sport": sport,
            "market_type": market_type,
            "commence_time": commence_time,
            "time_to_event_hours": time_to_event_hours,
            "neg_risk": neg_risk,
            "line": line,
            # Orderbook snapshot
            "orderbook_best_bid": orderbook_best_bid,
            "orderbook_best_ask": orderbook_best_ask,
            "orderbook_spread": orderbook_spread,
            "orderbook_depth_1pct": orderbook_depth_1pct,
            # Learning agent state at time of trade
            "learning_adjusted_edge": learning_adjusted_edge,
            "learning_sport_score": learning_sport_score,
            "learning_bucket_win_rate": learning_bucket_win_rate,
            "learning_total_trades": learning_total_trades,
            # Merge fields
            "is_merge": is_merge,
            "merge_yes_price": merge_yes_price,
            "merge_no_price": merge_no_price,
            "merge_profit_per_pair": merge_profit_per_pair,
        }

        self._append(entry)
        log.info("Journal ENTRY: %s [%s] @ %.3f edge=%.1f%%",
                 slug, outcome, entry_price, edge_pct)
        return entry

    def record_resolution(
        self,
        *,
        token_id: str,
        slug: str,
        outcome: str,
        won: bool,
        pnl: float,
        resolution_price: float,
        entry_price: float,
        shares: float,
        cost_usdc: float,
        opened_at: str = "",
        # CLV tracking
        closing_price: float = 0.0,
        closing_fair_prob: float = 0.0,
        # Time held
        hold_time_hours: float = 0.0,
    ) -> dict:
        """Record a trade resolution to the journal.

        Returns
        -------
        dict
            The full journal entry that was written.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Calculate CLV if closing price is available
        clv = 0.0
        if closing_price > 0 and entry_price > 0:
            clv = (closing_price - entry_price) / entry_price * 100

        entry = {
            "journal_type": "RESOLUTION",
            "timestamp": now,
            "token_id": token_id,
            "slug": slug,
            "outcome": outcome,
            "won": won,
            "pnl": pnl,
            "resolution_price": resolution_price,
            "entry_price": entry_price,
            "shares": shares,
            "cost_usdc": cost_usdc,
            "opened_at": opened_at,
            "resolved_at": now,
            # CLV
            "closing_price": closing_price,
            "closing_fair_prob": closing_fair_prob,
            "clv_pct": clv,
            # Hold time
            "hold_time_hours": hold_time_hours,
        }

        self._append(entry)
        log.info("Journal RESOLVE: %s [%s] won=%s pnl=$%.2f clv=%.1f%%",
                 slug, outcome, won, pnl, clv)
        return entry

    def record_skip(
        self,
        *,
        slug: str,
        outcome: str,
        reason: str,
        entry_price: float = 0.0,
        edge_pct: float = 0.0,
        sport: str = "",
    ) -> dict:
        """Record a trade that was skipped (for analyzing missed opportunities).

        Returns
        -------
        dict
            The full journal entry that was written.
        """
        now = datetime.now(timezone.utc).isoformat()
        entry = {
            "journal_type": "SKIP",
            "timestamp": now,
            "slug": slug,
            "outcome": outcome,
            "reason": reason,
            "entry_price": entry_price,
            "edge_pct": edge_pct,
            "sport": sport,
        }
        self._append(entry)
        return entry

    # ------------------------------------------------------------------
    # Reading entries
    # ------------------------------------------------------------------

    def read_all(self) -> list[dict]:
        """Read all journal entries.

        Returns
        -------
        list[dict]
            All entries in chronological order.
        """
        if not self.journal_path.exists():
            return []

        entries = []
        with open(self.journal_path, "r", encoding="utf-8") as f:
            for line_no, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning("Corrupt journal line %d, skipping", line_no)
        return entries

    def read_entries(self) -> list[dict]:
        """Read only ENTRY-type journal records.

        Returns
        -------
        list[dict]
        """
        return [e for e in self.read_all() if e.get("journal_type") == "ENTRY"]

    def read_resolutions(self) -> list[dict]:
        """Read only RESOLUTION-type journal records.

        Returns
        -------
        list[dict]
        """
        return [e for e in self.read_all()
                if e.get("journal_type") == "RESOLUTION"]

    def read_skips(self) -> list[dict]:
        """Read only SKIP-type journal records.

        Returns
        -------
        list[dict]
        """
        return [e for e in self.read_all() if e.get("journal_type") == "SKIP"]

    def get_entry_for_token(self, token_id: str) -> Optional[dict]:
        """Find the most recent ENTRY record for a given token_id.

        Returns
        -------
        dict or None
        """
        entries = [e for e in self.read_all()
                   if e.get("journal_type") == "ENTRY"
                   and e.get("token_id") == token_id]
        return entries[-1] if entries else None

    def clv_summary(self) -> dict:
        """Summary of CLV across all resolved trades.

        Returns
        -------
        dict
            {"avg_clv": float, "positive_pct": float, "count": int,
             "by_sport": dict}
        """
        resolutions = self.read_resolutions()
        clvs = [r["clv_pct"] for r in resolutions
                if r.get("clv_pct", 0) != 0]
        if not clvs:
            return {"avg_clv": 0.0, "positive_pct": 0.0, "count": 0,
                    "by_sport": {}}

        from collections import defaultdict
        by_sport: dict[str, list[float]] = defaultdict(list)
        for r in resolutions:
            if r.get("clv_pct", 0) != 0:
                # Look up sport from matching entry
                sport = r.get("sport", "unknown")
                by_sport[sport].append(r["clv_pct"])

        sport_summary = {}
        for sport, vals in by_sport.items():
            sport_summary[sport] = {
                "avg_clv": sum(vals) / len(vals),
                "positive_pct": sum(1 for v in vals if v > 0) / len(vals) * 100,
                "count": len(vals),
            }

        return {
            "avg_clv": sum(clvs) / len(clvs),
            "positive_pct": sum(1 for v in clvs if v > 0) / len(clvs) * 100,
            "count": len(clvs),
            "by_sport": sport_summary,
        }

    def trade_count(self) -> dict:
        """Count of each journal type.

        Returns
        -------
        dict
            {"entries": int, "resolutions": int, "skips": int, "total": int}
        """
        all_records = self.read_all()
        entries = sum(1 for e in all_records if e.get("journal_type") == "ENTRY")
        resolutions = sum(1 for e in all_records
                          if e.get("journal_type") == "RESOLUTION")
        skips = sum(1 for e in all_records if e.get("journal_type") == "SKIP")
        return {
            "entries": entries,
            "resolutions": resolutions,
            "skips": skips,
            "total": len(all_records),
        }

    # ------------------------------------------------------------------
    # Crash-safe append
    # ------------------------------------------------------------------

    def _append(self, record: dict):
        """Append a single JSON record as a new line (JSONL format).

        Uses flush + fsync for crash safety.
        """
        line = json.dumps(record, separators=(",", ":")) + "\n"
        with open(self.journal_path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
