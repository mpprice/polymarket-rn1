"""RN1 Integration — Market Discovery Helpers.

Provides helper functions that the bot's strategy can call each scan cycle
to check which markets RN1 is active in. This is used purely as an attention
signal — it does NOT change edge calculations or position sizing.

RN1's activity tells us "this market is interesting/active right now".
We then run our OWN edge model independently to decide whether to trade.

Usage in strategy:
    from src.rn1_integration import get_rn1_market_attention, rn1_attention_boost

    active = get_rn1_market_attention()
    # Use active slugs to prioritize which markets to scan first

    boost = rn1_attention_boost("nba-dal-bos-2026-03-08")
    # Returns 1.0 (no boost) or 1.5 (RN1 active) — only affects sort order
"""
import json
import logging
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_SUMMARY_PATH = BASE_DIR / "data" / "rn1_live_summary.json"

# Cache to avoid re-reading the file every call within the same scan cycle
_cache: dict = {}
_cache_mtime: float = 0.0
_cache_path: str = ""


def _load_summary(summary_path: str | Path = DEFAULT_SUMMARY_PATH) -> Optional[dict]:
    """Load and cache the RN1 live summary JSON.

    Caches based on file modification time to avoid redundant reads
    within the same scan cycle.
    """
    global _cache, _cache_mtime, _cache_path

    path = Path(summary_path)
    path_str = str(path)

    if not path.exists():
        return None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    # Return cached version if file hasn't changed
    if path_str == _cache_path and mtime == _cache_mtime and _cache:
        return _cache

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache = data
        _cache_mtime = mtime
        _cache_path = path_str
        return data
    except (json.JSONDecodeError, OSError) as e:
        log.debug("Failed to load RN1 summary: %s", e)
        return None


def get_rn1_market_attention(
    summary_path: str | Path = DEFAULT_SUMMARY_PATH,
) -> set[str]:
    """Read RN1 activity summary. Returns set of active market slugs (15min window).

    Returns an empty set if the tracker is not running or summary is stale.
    """
    data = _load_summary(summary_path)
    if data is None:
        return set()

    # Consider summary stale if older than 2 minutes
    last_poll = data.get("last_poll", "")
    if not last_poll:
        return set()

    return set(data.get("active_markets", []))


def is_rn1_interested(
    slug: str, summary_path: str | Path = DEFAULT_SUMMARY_PATH
) -> bool:
    """Check if RN1 has recent activity on this market slug."""
    active = get_rn1_market_attention(summary_path)
    return slug in active


def rn1_attention_boost(
    slug: str, summary_path: str | Path = DEFAULT_SUMMARY_PATH
) -> float:
    """Return a priority boost factor for queue sorting.

    Returns:
        1.0  — no boost (RN1 not active in this market)
        1.5  — RN1 is active in this market (15min window)
        2.0  — RN1 is HOT in this market (high activity)

    This does NOT change the edge or sizing — only affects sort order
    when we have multiple opportunities to choose from. Markets where
    RN1 is active get higher priority in the queue, but still must pass
    our own edge/risk filters independently.
    """
    data = _load_summary(summary_path)
    if data is None:
        return 1.0

    hot_markets = set(data.get("hot_markets", []))
    if slug in hot_markets:
        return 2.0

    active_markets = set(data.get("active_markets", []))
    if slug in active_markets:
        return 1.5

    return 1.0


def get_rn1_new_markets(
    summary_path: str | Path = DEFAULT_SUMMARY_PATH,
) -> set[str]:
    """Return slugs of markets RN1 just started trading (new in last 5min)."""
    data = _load_summary(summary_path)
    if data is None:
        return set()
    return set(data.get("new_markets", []))


def get_rn1_summary(
    summary_path: str | Path = DEFAULT_SUMMARY_PATH,
) -> Optional[dict]:
    """Return the full RN1 live summary dict, or None if unavailable."""
    return _load_summary(summary_path)
