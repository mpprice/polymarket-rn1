"""RN1 Live Activity Tracker — Market Discovery Signal.

Polls Polymarket's activity API every 1 second to detect which markets RN1
is currently active in. This is a MARKET DISCOVERY signal, not a copy-trade
system. We use RN1's activity as an attention signal — "this market is
interesting" — and then run our own edge model independently.

What we track:
- Which markets RN1 is active in (slugs)
- Volume of RN1 activity per market (high volume = interesting)
- New markets RN1 just entered
- Hot markets (high trade count in short window)

What we do NOT track/use:
- RN1's trade direction (buy/sell) for our decisions
- RN1's position sizing for our sizing
- Any "follow RN1" logic whatsoever
"""
import json
import logging
import time
from collections import defaultdict, deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"

# RN1 wallet addresses
RN1_MAIN_WALLET = "0xA0Da1B3efbb5efce6Bc40348F498DFa9e93c9bB1"
RN1_PROXY_WALLET = "0x2005d16a84ceefa912d4e380cd32e7ff827875ea"

# The activity API works with the proxy wallet
ACTIVITY_URL = "https://data-api.polymarket.com/activity"

# Persistence
TRADES_FILE = DATA_DIR / "rn1_live_trades.json"
SUMMARY_FILE = DATA_DIR / "rn1_live_summary.json"

# Buffer size
MAX_BUFFER = 500


class RN1LiveTracker:
    """Tracks RN1's live activity on Polymarket for market discovery."""

    def __init__(
        self,
        wallet: str = RN1_PROXY_WALLET,
        poll_limit: int = 20,
        persist_interval: int = 30,
    ):
        self.wallet = wallet
        self.poll_limit = poll_limit
        self.persist_interval = persist_interval

        # Rolling buffer of last N trades (most recent first)
        self.trades: deque = deque(maxlen=MAX_BUFFER)

        # Last-seen timestamp to detect only NEW activity
        self.last_seen_ts: int = 0

        # Track when we last persisted / wrote summary
        self._last_persist_time: float = 0.0
        self._last_summary_time: float = 0.0

        # Error backoff
        self._consecutive_errors: int = 0
        self._max_backoff: float = 30.0

        # Load any persisted trades from disk
        self._load_persisted()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load_persisted(self) -> None:
        """Load previously persisted trades from disk."""
        if TRADES_FILE.exists():
            try:
                with open(TRADES_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    for t in data[-MAX_BUFFER:]:
                        self.trades.append(t)
                    if self.trades:
                        self.last_seen_ts = max(
                            t.get("timestamp", 0) for t in self.trades
                        )
                    log.info(
                        "Loaded %d persisted trades, last_seen_ts=%d",
                        len(self.trades),
                        self.last_seen_ts,
                    )
            except Exception as e:
                log.warning("Failed to load persisted trades: %s", e)

    def _persist_trades(self) -> None:
        """Write trade buffer to disk (called every persist_interval seconds)."""
        now = time.time()
        if now - self._last_persist_time < self.persist_interval:
            return
        self._last_persist_time = now

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        try:
            with open(TRADES_FILE, "w", encoding="utf-8") as f:
                json.dump(list(self.trades), f, indent=1)
        except Exception as e:
            log.warning("Failed to persist trades: %s", e)

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll(self) -> list[dict]:
        """Poll the activity API and return list of NEW trades since last poll.

        Returns a list of simplified trade dicts (newest first).
        """
        try:
            resp = requests.get(
                ACTIVITY_URL,
                params={"user": self.wallet, "limit": self.poll_limit},
                timeout=5,
            )

            # Handle rate limiting and server errors
            if resp.status_code == 429:
                self._consecutive_errors += 1
                wait = min(2 ** self._consecutive_errors, self._max_backoff)
                log.warning("Rate limited (429), backing off %.1fs", wait)
                time.sleep(wait)
                return []

            if resp.status_code >= 500:
                self._consecutive_errors += 1
                wait = min(2 ** self._consecutive_errors, self._max_backoff)
                log.warning(
                    "Server error %d, backing off %.1fs", resp.status_code, wait
                )
                time.sleep(wait)
                return []

            resp.raise_for_status()
            self._consecutive_errors = 0

            raw = resp.json()
            if not isinstance(raw, list):
                return []

        except requests.RequestException as e:
            self._consecutive_errors += 1
            wait = min(2 ** self._consecutive_errors, self._max_backoff)
            log.warning("Poll error: %s (backoff %.1fs)", e, wait)
            time.sleep(wait)
            return []

        # Filter to only NEW trades (timestamp > last_seen)
        new_trades = []
        max_ts = self.last_seen_ts

        for item in raw:
            ts = item.get("timestamp", 0)
            if ts <= self.last_seen_ts:
                continue

            trade = self._parse_activity(item)
            new_trades.append(trade)
            if ts > max_ts:
                max_ts = ts

        if new_trades:
            self.last_seen_ts = max_ts
            # Add to buffer (newest first in buffer)
            for t in sorted(new_trades, key=lambda x: x["timestamp"]):
                self.trades.append(t)

            log.debug("Found %d new RN1 activities", len(new_trades))

        # Periodic persistence
        self._persist_trades()

        return new_trades

    @staticmethod
    def _parse_activity(item: dict) -> dict:
        """Parse a raw activity record into our simplified format.

        We store slug/type/volume/timestamp for market discovery.
        We log side/price for diagnostics but do NOT use them for trading decisions.
        """
        ts = item.get("timestamp", 0)
        return {
            "timestamp": ts,
            "datetime": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            if ts
            else "",
            "type": item.get("type", "UNKNOWN"),
            "slug": item.get("slug", ""),
            "event_slug": item.get("eventSlug", ""),
            "title": item.get("title", ""),
            "outcome": item.get("outcome", ""),
            "usdc_size": item.get("usdcSize", 0),
            "size": item.get("size", 0),
            "price": item.get("price", 0),
            "side": item.get("side", ""),
            "tx_hash": item.get("transactionHash", ""),
        }

    # ------------------------------------------------------------------
    # Query methods — Market Discovery
    # ------------------------------------------------------------------

    def _trades_in_window(self, minutes: int) -> list[dict]:
        """Return trades within the last N minutes."""
        cutoff = time.time() - minutes * 60
        return [t for t in self.trades if t["timestamp"] >= cutoff]

    def get_active_markets(self, minutes: int = 15) -> set[str]:
        """Return set of slugs where RN1 has had ANY activity in last N minutes."""
        return {
            t["slug"] for t in self._trades_in_window(minutes) if t["slug"]
        }

    def get_new_markets(self, minutes: int = 5) -> set[str]:
        """Return slugs RN1 just started trading (active in last N min but
        NOT seen in the 30 minutes before that)."""
        now = time.time()
        recent_cutoff = now - minutes * 60
        prior_cutoff = now - 30 * 60

        recent_slugs = {
            t["slug"]
            for t in self.trades
            if t["timestamp"] >= recent_cutoff and t["slug"]
        }
        prior_slugs = {
            t["slug"]
            for t in self.trades
            if prior_cutoff <= t["timestamp"] < recent_cutoff and t["slug"]
        }

        return recent_slugs - prior_slugs

    def get_market_activity(self, slug: str) -> dict:
        """Return activity summary for a specific market slug."""
        matching = [t for t in self.trades if t["slug"] == slug]
        if not matching:
            return {
                "trade_count": 0,
                "total_volume": 0.0,
                "first_seen": None,
                "last_seen": None,
            }

        volumes = [t.get("usdc_size", 0) or 0 for t in matching]
        timestamps = [t["timestamp"] for t in matching]

        return {
            "trade_count": len(matching),
            "total_volume": sum(volumes),
            "first_seen": datetime.fromtimestamp(
                min(timestamps), tz=timezone.utc
            ).isoformat(),
            "last_seen": datetime.fromtimestamp(
                max(timestamps), tz=timezone.utc
            ).isoformat(),
        }

    def get_hot_markets(
        self, min_trades: int = 3, minutes: int = 10
    ) -> list[dict]:
        """Return markets with high RN1 activity (multiple trades in window).

        Sorted by trade count descending.
        """
        recent = self._trades_in_window(minutes)
        slug_counts: dict[str, dict] = defaultdict(
            lambda: {"trade_count": 0, "total_volume": 0.0}
        )

        for t in recent:
            slug = t.get("slug", "")
            if not slug:
                continue
            slug_counts[slug]["trade_count"] += 1
            slug_counts[slug]["total_volume"] += t.get("usdc_size", 0) or 0

        hot = []
        for slug, info in slug_counts.items():
            if info["trade_count"] >= min_trades:
                hot.append(
                    {
                        "slug": slug,
                        "trade_count": info["trade_count"],
                        "total_volume": round(info["total_volume"], 2),
                    }
                )

        hot.sort(key=lambda x: x["trade_count"], reverse=True)
        return hot

    def summary(self) -> dict:
        """Dashboard/logging summary of current RN1 activity state."""
        now_utc = datetime.now(timezone.utc).isoformat()
        active_5m = self.get_active_markets(minutes=5)
        active_15m = self.get_active_markets(minutes=15)
        new_5m = self.get_new_markets(minutes=5)
        hot = self.get_hot_markets(min_trades=3, minutes=10)

        trades_5m = self._trades_in_window(5)
        trades_15m = self._trades_in_window(15)

        return {
            "last_poll": now_utc,
            "active_markets": sorted(active_15m),
            "active_markets_5m": sorted(active_5m),
            "hot_markets": [h["slug"] for h in hot],
            "new_markets": sorted(new_5m),
            "trades_last_5m": len(trades_5m),
            "trades_last_15m": len(trades_15m),
            "total_buffered": len(self.trades),
            "last_seen_ts": self.last_seen_ts,
        }

    # ------------------------------------------------------------------
    # Summary file writer
    # ------------------------------------------------------------------

    def write_summary(self, force: bool = False) -> None:
        """Write summary JSON for the bot to read. Called every ~30s."""
        now = time.time()
        if not force and now - self._last_summary_time < self.persist_interval:
            return
        self._last_summary_time = now

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        summary = self.summary()

        try:
            # Write atomically via temp file
            tmp = SUMMARY_FILE.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(summary, f, indent=2)
            tmp.replace(SUMMARY_FILE)
        except Exception as e:
            log.warning("Failed to write summary: %s", e)

    def get_recent_activity(self, n: int = 15) -> list[dict]:
        """Return last N activity events for dashboard display.

        Returns simplified records: type, slug, timestamp, usdc_size.
        Does NOT expose side/direction — this is market discovery only.
        """
        recent = sorted(self.trades, key=lambda t: t["timestamp"], reverse=True)[:n]
        return [
            {
                "type": t["type"],
                "slug": t["slug"],
                "event_slug": t.get("event_slug", ""),
                "title": t.get("title", ""),
                "timestamp": t["timestamp"],
                "datetime": t.get("datetime", ""),
                "usdc_size": round(t.get("usdc_size", 0) or 0, 2),
            }
            for t in recent
        ]
