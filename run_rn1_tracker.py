#!/usr/bin/env python3
"""RN1 Activity Tracker — Standalone Daemon.

Polls Polymarket every 1 second to detect RN1's live trading activity.
This is a MARKET DISCOVERY signal — we track which markets RN1 is active
in, NOT which direction they're trading.

Usage:
    python run_rn1_tracker.py
    python run_rn1_tracker.py --interval 1 --limit 20

Writes:
    data/rn1_live_trades.json   — rolling buffer of last 500 trades (every 30s)
    data/rn1_live_summary.json  — current activity summary (every 30s)
    rn1_tracker.log             — activity log
"""
import argparse
import logging
import signal
import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from src.rn1_live_tracker import RN1LiveTracker

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = ROOT / "rn1_tracker.log"

log = logging.getLogger("rn1_tracker")
log.setLevel(logging.INFO)

# File handler
fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
fh.setLevel(logging.INFO)
fh.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
)
log.addHandler(fh)

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.INFO)
ch.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
)
log.addHandler(ch)

# Also configure the tracker module's logger
tracker_logger = logging.getLogger("src.rn1_live_tracker")
tracker_logger.setLevel(logging.INFO)
tracker_logger.addHandler(fh)
tracker_logger.addHandler(ch)

# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.info("Received signal %d, shutting down gracefully...", signum)
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="RN1 Live Activity Tracker")
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Poll interval in seconds (default: 1.0)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of activity records per poll (default: 20)",
    )
    parser.add_argument(
        "--persist-interval",
        type=int,
        default=30,
        help="Seconds between disk writes (default: 30)",
    )
    args = parser.parse_args()

    log.info("=" * 60)
    log.info("RN1 Activity Tracker starting")
    log.info("  Poll interval: %.1fs", args.interval)
    log.info("  Fetch limit: %d", args.limit)
    log.info("  Persist interval: %ds", args.persist_interval)
    log.info("  NOTE: Market discovery signal only — NOT copy-trading")
    log.info("=" * 60)

    tracker = RN1LiveTracker(
        poll_limit=args.limit,
        persist_interval=args.persist_interval,
    )

    log.info(
        "Loaded %d historical trades, last_seen_ts=%d",
        len(tracker.trades),
        tracker.last_seen_ts,
    )

    poll_count = 0
    total_new = 0
    last_summary_log = 0.0

    while not _shutdown:
        loop_start = time.time()
        poll_count += 1

        try:
            new_trades = tracker.poll()

            if new_trades:
                total_new += len(new_trades)
                for t in new_trades:
                    log.info(
                        "RN1 ACTIVITY: %s %s [%s] %.0f shares @ %.3f ($%.0f)",
                        t["type"],
                        t["slug"],
                        t.get("outcome", ""),
                        t.get("size", 0),
                        t.get("price", 0),
                        t.get("usdc_size", 0),
                    )

            # Write summary periodically (handled internally, respects persist_interval)
            tracker.write_summary()

            # Log a status line every 60 seconds
            now = time.time()
            if now - last_summary_log >= 60:
                s = tracker.summary()
                log.info(
                    "STATUS: polls=%d new_total=%d active_15m=%d hot=%d trades_5m=%d buf=%d",
                    poll_count,
                    total_new,
                    len(s["active_markets"]),
                    len(s["hot_markets"]),
                    s["trades_last_5m"],
                    s["total_buffered"],
                )
                last_summary_log = now

        except Exception:
            log.exception("Unexpected error in poll loop")

        # Sleep for remaining interval
        elapsed = time.time() - loop_start
        sleep_time = max(0, args.interval - elapsed)
        if sleep_time > 0 and not _shutdown:
            time.sleep(sleep_time)

    # Shutdown — final persist
    log.info("Shutting down, persisting final state...")
    tracker._persist_trades()
    tracker.write_summary(force=True)
    log.info("RN1 tracker stopped. Total polls: %d, total new trades: %d", poll_count, total_new)


if __name__ == "__main__":
    main()
