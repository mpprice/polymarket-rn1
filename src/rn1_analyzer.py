"""RN1 Pattern Analysis Engine.

Loads RN1's 1.1M activity records and extracts actionable trading patterns:
entry price distribution, position sizing, holding periods, sport preferences,
merge patterns, time-of-day patterns, and more.

Caches results to data/rn1_patterns_summary.json for fast loading.
"""
import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
ACTIVITY_FILE = DATA_DIR / "rn1_full_activity.json"
CACHE_FILE = DATA_DIR / "rn1_patterns_summary.json"

# Sport detection from slug prefixes
SPORT_PREFIX_MAP = {
    "nba": "basketball_nba",
    "nhl": "ice_hockey_nhl",
    "nfl": "americanfootball_nfl",
    "mlb": "baseball_mlb",
    "cbb": "basketball_ncaab",
    "cfb": "americanfootball_ncaaf",
    "epl": "soccer_epl",
    "lal": "soccer_spain_la_liga",
    "bun": "soccer_germany_bundesliga",
    "ser": "soccer_italy_serie_a",
    "lig": "soccer_france_ligue_one",
    "ere": "soccer_netherlands_eredivisie",
    "ucl": "soccer_uefa_champs_league",
    "uel": "soccer_uefa_europa_league",
    "mls": "soccer_usa_mls",
    "spl": "soccer_spl",
    "cs2": "esports_csgo",
    "lol": "esports_lol",
    "val": "esports_valorant",
    "atp": "tennis_atp",
    "wta": "tennis_wta",
    "ufc": "mma_ufc",
    "box": "boxing",
    "f1-": "motorsport_f1",
    "den": "soccer_denmark",
    "arg": "soccer_argentina",
    "col": "soccer_colombia",
    "egy": "soccer_egypt",
    "itsb": "soccer_italy_serie_b",
    "bl2": "soccer_germany_2bundesliga",
    "es2": "soccer_spain_segunda",
    "elc": "soccer_efl_championship",
    "fr2": "soccer_france_ligue_two",
}

# Broader sport categories for scoring
SPORT_CATEGORY_MAP = {
    "soccer": [
        "soccer_epl", "soccer_spain_la_liga", "soccer_germany_bundesliga",
        "soccer_italy_serie_a", "soccer_france_ligue_one", "soccer_netherlands_eredivisie",
        "soccer_uefa_champs_league", "soccer_uefa_europa_league", "soccer_usa_mls",
        "soccer_spl", "soccer_denmark", "soccer_argentina", "soccer_colombia",
        "soccer_egypt", "soccer_italy_serie_b", "soccer_germany_2bundesliga",
        "soccer_spain_segunda", "soccer_efl_championship", "soccer_france_ligue_two",
    ],
    "basketball": ["basketball_nba", "basketball_ncaab"],
    "esports": ["esports_csgo", "esports_lol", "esports_valorant"],
    "tennis": ["tennis_atp", "tennis_wta"],
    "american_football": ["americanfootball_nfl", "americanfootball_ncaaf"],
    "hockey": ["ice_hockey_nhl"],
    "baseball": ["baseball_mlb"],
    "combat": ["mma_ufc", "boxing"],
    "motorsport": ["motorsport_f1"],
}


def _detect_sport(slug: str) -> str:
    """Detect sport from slug prefix."""
    if not slug:
        return "unknown"
    for prefix, sport in SPORT_PREFIX_MAP.items():
        if slug.startswith(prefix):
            return sport
    # Fallback: use first dash-separated token
    return slug.split("-")[0] if "-" in slug else "unknown"


def _detect_sport_category(sport: str) -> str:
    """Map specific sport to broad category."""
    for cat, sports in SPORT_CATEGORY_MAP.items():
        if sport in sports:
            return cat
    return "other"


def _detect_market_type(slug: str) -> str:
    """Detect market type from slug."""
    if not slug:
        return "unknown"
    slug_lower = slug.lower()
    if "-spread-" in slug_lower or "spread" in slug_lower:
        return "spread"
    if "-total-" in slug_lower or "total" in slug_lower:
        return "total"
    # Default: h2h (moneyline / match winner)
    return "h2h"


def _price_bucket(price: float) -> str:
    """Map price to 5c bucket label."""
    if price <= 0:
        return "0-5c"
    bucket = int(price * 100) // 5 * 5
    return f"{bucket}-{bucket+5}c"


class RN1Analyzer:
    """Analyzes RN1's historical trading patterns."""

    def __init__(self, force_reload: bool = False, max_records: int = 0):
        """Initialize analyzer. Loads from cache if available, otherwise from raw data.

        Args:
            force_reload: If True, ignore cache and recompute from raw data.
            max_records: If > 0, limit raw data loading (for testing).
        """
        self._patterns: dict[str, Any] = {}
        self._loaded = False

        if not force_reload and CACHE_FILE.exists():
            try:
                with open(CACHE_FILE) as f:
                    self._patterns = json.load(f)
                self._loaded = True
                log.info("Loaded RN1 patterns from cache (%d keys)", len(self._patterns))
                return
            except Exception as e:
                log.warning("Cache load failed, recomputing: %s", e)

        self._compute_patterns(max_records)

    def _compute_patterns(self, max_records: int = 0):
        """Compute all patterns from raw activity data."""
        if not ACTIVITY_FILE.exists():
            log.error("RN1 activity file not found: %s", ACTIVITY_FILE)
            self._patterns = {"error": "activity file not found"}
            return

        log.info("Loading RN1 activity data from %s...", ACTIVITY_FILE)
        t0 = time.time()

        with open(ACTIVITY_FILE) as f:
            records = json.load(f)
        if max_records > 0:
            records = records[:max_records]

        load_time = time.time() - t0
        log.info("Loaded %d records in %.1fs", len(records), load_time)

        # Separate by type
        buys = []
        sells = []
        merges = []
        redeems = []

        for r in records:
            rtype = r.get("type", "")
            side = r.get("side", "")
            if rtype == "TRADE" and side == "BUY":
                buys.append(r)
            elif rtype == "TRADE" and side == "SELL":
                sells.append(r)
            elif rtype == "MERGE":
                merges.append(r)
            elif rtype == "REDEEM":
                redeems.append(r)

        log.info("Breakdown: %d buys, %d sells, %d merges, %d redeems",
                 len(buys), len(sells), len(merges), len(redeems))

        # Compute all patterns
        self._patterns = {
            "computed_at": datetime.now(timezone.utc).isoformat(),
            "total_records": len(records),
            "record_counts": {
                "buys": len(buys),
                "sells": len(sells),
                "merges": len(merges),
                "redeems": len(redeems),
            },
            "entry_price_distribution": self._compute_entry_price_distribution(buys),
            "position_sizing": self._compute_position_sizing(buys),
            "holding_periods": self._compute_holding_periods(buys, sells, merges, redeems),
            "sport_preferences": self._compute_sport_preferences(buys, merges, redeems),
            "merge_patterns": self._compute_merge_patterns(buys, merges),
            "time_of_day": self._compute_time_of_day(buys + merges),
            "market_type_preferences": self._compute_market_type_preferences(buys),
            "consecutive_trades": self._compute_consecutive_trades(buys),
            "profitable_slugs": self._compute_profitable_slugs(buys, merges, redeems),
            "top_sports_by_profit": [],  # filled below
            "top_markets_by_volume": [],  # filled below
        }

        # Derive top-level rankings
        sport_prefs = self._patterns["sport_preferences"]
        self._patterns["top_sports_by_profit"] = sorted(
            [{"sport": k, **v} for k, v in sport_prefs.items()],
            key=lambda x: x.get("estimated_profit", 0),
            reverse=True,
        )[:20]

        slug_stats = self._patterns["profitable_slugs"]
        self._patterns["top_markets_by_volume"] = sorted(
            [{"slug": k, **v} for k, v in slug_stats.items()],
            key=lambda x: x.get("total_usdc", 0),
            reverse=True,
        )[:50]

        # Save cache
        self._save_cache()
        self._loaded = True
        log.info("RN1 pattern analysis complete in %.1fs", time.time() - t0)

    def _save_cache(self):
        """Save patterns to cache file."""
        try:
            with open(CACHE_FILE, "w") as f:
                json.dump(self._patterns, f, indent=2, default=str)
            log.info("Saved patterns cache to %s", CACHE_FILE)
        except Exception as e:
            log.error("Failed to save cache: %s", e)

    # -----------------------------------------------------------------------
    # Pattern computations
    # -----------------------------------------------------------------------

    def _compute_entry_price_distribution(self, buys: list[dict]) -> dict:
        """What prices does RN1 buy at? Histogram by 5c buckets."""
        buckets = defaultdict(lambda: {"count": 0, "total_usdc": 0.0})
        for r in buys:
            price = r.get("price", 0)
            if price <= 0 or price >= 1:
                continue
            bucket = _price_bucket(price)
            buckets[bucket]["count"] += 1
            buckets[bucket]["total_usdc"] += r.get("usdcSize", 0)

        # Sort by price bucket
        result = {}
        for b in sorted(buckets.keys(), key=lambda x: int(x.split("-")[0])):
            result[b] = buckets[b]
        return result

    def _compute_position_sizing(self, buys: list[dict]) -> dict:
        """How does RN1 size positions? By sport, price level, market type."""
        by_sport = defaultdict(list)
        by_price = defaultdict(list)
        by_type = defaultdict(list)

        for r in buys:
            usdc = r.get("usdcSize", 0)
            if usdc <= 0:
                continue
            sport = _detect_sport(r.get("slug", ""))
            price_bucket = _price_bucket(r.get("price", 0))
            mtype = _detect_market_type(r.get("slug", ""))

            by_sport[sport].append(usdc)
            by_price[price_bucket].append(usdc)
            by_type[mtype].append(usdc)

        def _stats(values: list[float]) -> dict:
            if not values:
                return {"count": 0, "mean": 0, "median": 0, "total": 0, "p25": 0, "p75": 0}
            values.sort()
            n = len(values)
            return {
                "count": n,
                "mean": round(sum(values) / n, 2),
                "median": round(values[n // 2], 2),
                "total": round(sum(values), 2),
                "p25": round(values[n // 4], 2),
                "p75": round(values[3 * n // 4], 2),
            }

        return {
            "by_sport": {k: _stats(v) for k, v in sorted(by_sport.items(),
                         key=lambda x: -sum(x[1]))[:20]},
            "by_price_bucket": {k: _stats(v) for k, v in sorted(by_price.items(),
                                key=lambda x: int(x[0].split("-")[0]))},
            "by_market_type": {k: _stats(v) for k, v in by_type.items()},
            "overall": _stats([r.get("usdcSize", 0) for r in buys if r.get("usdcSize", 0) > 0]),
        }

    def _compute_holding_periods(self, buys: list[dict], sells: list[dict],
                                  merges: list[dict], redeems: list[dict]) -> dict:
        """Estimate holding periods from buy to sell/redeem/merge per slug."""
        # Group by slug: first buy and last exit
        slug_buys = defaultdict(list)
        slug_exits = defaultdict(list)

        for r in buys:
            slug = r.get("slug", "")
            if slug:
                slug_buys[slug].append(r.get("timestamp", 0))

        for r in sells + merges + redeems:
            slug = r.get("slug", "")
            if slug:
                slug_exits[slug].append(r.get("timestamp", 0))

        holding_hours = []
        for slug in slug_buys:
            if slug in slug_exits:
                first_buy = min(slug_buys[slug])
                last_exit = max(slug_exits[slug])
                if last_exit > first_buy:
                    hours = (last_exit - first_buy) / 3600.0
                    holding_hours.append(hours)

        if not holding_hours:
            return {"count": 0, "mean_hours": 0, "median_hours": 0}

        holding_hours.sort()
        n = len(holding_hours)
        # Bucketize
        buckets = {"<1h": 0, "1-6h": 0, "6-24h": 0, "1-3d": 0, "3-7d": 0, ">7d": 0}
        for h in holding_hours:
            if h < 1:
                buckets["<1h"] += 1
            elif h < 6:
                buckets["1-6h"] += 1
            elif h < 24:
                buckets["6-24h"] += 1
            elif h < 72:
                buckets["1-3d"] += 1
            elif h < 168:
                buckets["3-7d"] += 1
            else:
                buckets[">7d"] += 1

        return {
            "count": n,
            "mean_hours": round(sum(holding_hours) / n, 1),
            "median_hours": round(holding_hours[n // 2], 1),
            "p10_hours": round(holding_hours[n // 10], 1),
            "p90_hours": round(holding_hours[9 * n // 10], 1),
            "buckets": buckets,
        }

    def _compute_sport_preferences(self, buys: list[dict], merges: list[dict],
                                    redeems: list[dict]) -> dict:
        """Sport preferences weighted by USDC volume and estimated profit."""
        sports = defaultdict(lambda: {
            "buy_count": 0, "buy_usdc": 0.0, "merge_count": 0, "merge_usdc": 0.0,
            "redeem_count": 0, "redeem_usdc": 0.0, "estimated_profit": 0.0,
        })

        for r in buys:
            sport = _detect_sport(r.get("slug", ""))
            sports[sport]["buy_count"] += 1
            sports[sport]["buy_usdc"] += r.get("usdcSize", 0)
            # Estimate: REDEEM pays $1/share, cost is usdcSize
            # Profit from buys = redeem_usdc - buy_usdc (computed below)

        for r in merges:
            sport = _detect_sport(r.get("slug", ""))
            sports[sport]["merge_count"] += 1
            sports[sport]["merge_usdc"] += r.get("usdcSize", 0)

        for r in redeems:
            sport = _detect_sport(r.get("slug", ""))
            sports[sport]["redeem_count"] += 1
            sports[sport]["redeem_usdc"] += r.get("usdcSize", 0)

        # Estimate profit per sport: redeem + merge payout - buy cost
        # MERGE returns $1 per pair merged (usdcSize = shares merged = USDC received)
        # REDEEM pays out shares at $1 each (usdcSize = payout)
        # Cost is total buy USDC
        for sport, v in sports.items():
            v["estimated_profit"] = round(
                v["redeem_usdc"] + v["merge_usdc"] - v["buy_usdc"], 2
            )

        # Round all values
        result = {}
        for sport, v in sports.items():
            result[sport] = {k: round(val, 2) if isinstance(val, float) else val
                             for k, val in v.items()}
        return result

    def _compute_merge_patterns(self, buys: list[dict], merges: list[dict]) -> dict:
        """Analyze MERGE activity: when, how much, estimated profit per merge."""
        if not merges:
            return {"count": 0, "total_usdc": 0, "avg_size": 0, "slugs": 0}

        # Merge by slug
        slug_merges = defaultdict(lambda: {"merge_usdc": 0.0, "merge_count": 0})
        for r in merges:
            slug = r.get("slug", "")
            slug_merges[slug]["merge_usdc"] += r.get("usdcSize", 0)
            slug_merges[slug]["merge_count"] += 1

        # Buy cost by slug (for merged slugs only)
        slug_buy_cost = defaultdict(float)
        for r in buys:
            slug = r.get("slug", "")
            if slug in slug_merges:
                slug_buy_cost[slug] += r.get("usdcSize", 0)

        # Compute per-slug merge profit
        # MERGE: you get $1 per pair. usdcSize = number of pairs merged = USDC received
        merge_profits = []
        for slug in slug_merges:
            received = slug_merges[slug]["merge_usdc"]
            cost = slug_buy_cost.get(slug, 0)
            if cost > 0:
                profit_pct = (received - cost) / cost * 100
                merge_profits.append({
                    "slug": slug,
                    "merge_usdc": round(received, 2),
                    "buy_cost": round(cost, 2),
                    "profit": round(received - cost, 2),
                    "profit_pct": round(profit_pct, 1),
                })

        merge_sizes = [r.get("usdcSize", 0) for r in merges if r.get("usdcSize", 0) > 0]
        merge_sizes.sort()
        n = len(merge_sizes)

        # Time of day for merges
        merge_hours = defaultdict(int)
        for r in merges:
            ts = r.get("timestamp", 0)
            if ts > 0:
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                merge_hours[hour] += 1

        return {
            "count": len(merges),
            "total_usdc": round(sum(merge_sizes), 2),
            "avg_size": round(sum(merge_sizes) / n, 2) if n > 0 else 0,
            "median_size": round(merge_sizes[n // 2], 2) if n > 0 else 0,
            "unique_slugs": len(slug_merges),
            "profitable_merges": sorted(
                [p for p in merge_profits if p["profit"] > 0],
                key=lambda x: -x["profit"]
            )[:30],
            "merge_hours_utc": dict(sorted(merge_hours.items())),
        }

    def _compute_time_of_day(self, trades: list[dict]) -> dict:
        """When does RN1 trade? UTC hour heatmap."""
        hours = defaultdict(lambda: {"count": 0, "usdc": 0.0})
        days = defaultdict(lambda: {"count": 0, "usdc": 0.0})

        for r in trades:
            ts = r.get("timestamp", 0)
            if ts <= 0:
                continue
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            h = dt.hour
            d = dt.strftime("%A")
            hours[h]["count"] += 1
            hours[h]["usdc"] += r.get("usdcSize", 0)
            days[d]["count"] += 1
            days[d]["usdc"] += r.get("usdcSize", 0)

        return {
            "by_hour_utc": {str(h): {"count": v["count"], "usdc": round(v["usdc"], 2)}
                            for h, v in sorted(hours.items())},
            "by_day_of_week": {d: {"count": v["count"], "usdc": round(v["usdc"], 2)}
                               for d, v in days.items()},
            "peak_hour_utc": max(hours, key=lambda h: hours[h]["count"]) if hours else None,
            "peak_day": max(days, key=lambda d: days[d]["count"]) if days else None,
        }

    def _compute_market_type_preferences(self, buys: list[dict]) -> dict:
        """h2h vs spread vs total breakdown."""
        types = defaultdict(lambda: {"count": 0, "usdc": 0.0, "avg_price": 0.0, "prices": []})

        for r in buys:
            mtype = _detect_market_type(r.get("slug", ""))
            usdc = r.get("usdcSize", 0)
            price = r.get("price", 0)
            types[mtype]["count"] += 1
            types[mtype]["usdc"] += usdc
            if 0 < price < 1:
                types[mtype]["prices"].append(price)

        result = {}
        for mtype, v in types.items():
            prices = v["prices"]
            result[mtype] = {
                "count": v["count"],
                "usdc": round(v["usdc"], 2),
                "pct_of_trades": 0,  # filled below
                "avg_price": round(sum(prices) / len(prices), 4) if prices else 0,
                "median_price": round(sorted(prices)[len(prices) // 2], 4) if prices else 0,
            }

        total = sum(v["count"] for v in result.values())
        for mtype in result:
            result[mtype]["pct_of_trades"] = round(
                result[mtype]["count"] / total * 100, 1) if total > 0 else 0

        return result

    def _compute_consecutive_trades(self, buys: list[dict]) -> dict:
        """Does RN1 scale into positions? Average buys per slug."""
        slug_counts = defaultdict(int)
        for r in buys:
            slug = r.get("slug", "")
            if slug:
                slug_counts[slug] += 1

        if not slug_counts:
            return {"avg_buys_per_slug": 0, "max_buys_per_slug": 0}

        counts = list(slug_counts.values())
        counts.sort()
        n = len(counts)

        # Distribution of trades per slug
        buckets = {"1": 0, "2-5": 0, "6-10": 0, "11-20": 0, "21-50": 0, ">50": 0}
        for c in counts:
            if c == 1:
                buckets["1"] += 1
            elif c <= 5:
                buckets["2-5"] += 1
            elif c <= 10:
                buckets["6-10"] += 1
            elif c <= 20:
                buckets["11-20"] += 1
            elif c <= 50:
                buckets["21-50"] += 1
            else:
                buckets[">50"] += 1

        return {
            "unique_slugs": n,
            "avg_buys_per_slug": round(sum(counts) / n, 1),
            "median_buys_per_slug": counts[n // 2],
            "max_buys_per_slug": max(counts),
            "distribution": buckets,
        }

    def _compute_profitable_slugs(self, buys: list[dict], merges: list[dict],
                                   redeems: list[dict]) -> dict:
        """Which specific market slugs were most profitable?"""
        slugs = defaultdict(lambda: {
            "buy_usdc": 0.0, "buy_count": 0, "merge_usdc": 0.0,
            "redeem_usdc": 0.0, "sport": "", "market_type": "",
        })

        for r in buys:
            slug = r.get("slug", "")
            if not slug:
                continue
            slugs[slug]["buy_usdc"] += r.get("usdcSize", 0)
            slugs[slug]["buy_count"] += 1
            if not slugs[slug]["sport"]:
                slugs[slug]["sport"] = _detect_sport(slug)
                slugs[slug]["market_type"] = _detect_market_type(slug)

        for r in merges:
            slug = r.get("slug", "")
            if slug:
                slugs[slug]["merge_usdc"] += r.get("usdcSize", 0)

        for r in redeems:
            slug = r.get("slug", "")
            if slug:
                slugs[slug]["redeem_usdc"] += r.get("usdcSize", 0)

        # Compute P&L per slug
        result = {}
        for slug, v in slugs.items():
            total_usdc = v["buy_usdc"]
            payout = v["redeem_usdc"] + v["merge_usdc"]
            pnl = payout - total_usdc
            result[slug] = {
                "total_usdc": round(total_usdc, 2),
                "buy_count": v["buy_count"],
                "payout": round(payout, 2),
                "pnl": round(pnl, 2),
                "roi_pct": round(pnl / total_usdc * 100, 1) if total_usdc > 0 else 0,
                "sport": v["sport"],
                "market_type": v["market_type"],
            }

        return result

    # -----------------------------------------------------------------------
    # Public query API
    # -----------------------------------------------------------------------

    @property
    def patterns(self) -> dict:
        return self._patterns

    def entry_price_distribution(self) -> dict:
        return self._patterns.get("entry_price_distribution", {})

    def position_sizing_patterns(self) -> dict:
        return self._patterns.get("position_sizing", {})

    def holding_period_analysis(self) -> dict:
        return self._patterns.get("holding_periods", {})

    def sport_preferences(self) -> dict:
        return self._patterns.get("sport_preferences", {})

    def merge_patterns(self) -> dict:
        return self._patterns.get("merge_patterns", {})

    def time_of_day_patterns(self) -> dict:
        return self._patterns.get("time_of_day", {})

    def market_type_preferences(self) -> dict:
        return self._patterns.get("market_type_preferences", {})

    def consecutive_trade_patterns(self) -> dict:
        return self._patterns.get("consecutive_trades", {})

    def profitable_slugs(self) -> dict:
        return self._patterns.get("profitable_slugs", {})

    def top_sports_by_profit(self) -> list[dict]:
        return self._patterns.get("top_sports_by_profit", [])

    def top_markets_by_volume(self) -> list[dict]:
        return self._patterns.get("top_markets_by_volume", [])

    def summary(self) -> dict:
        """Return comprehensive summary for dashboard display."""
        return {
            "total_records": self._patterns.get("total_records", 0),
            "record_counts": self._patterns.get("record_counts", {}),
            "entry_price_distribution": self.entry_price_distribution(),
            "position_sizing": {
                "overall": self._patterns.get("position_sizing", {}).get("overall", {}),
                "by_market_type": self._patterns.get("position_sizing", {}).get("by_market_type", {}),
            },
            "holding_periods": self.holding_period_analysis(),
            "merge_patterns": {
                "count": self.merge_patterns().get("count", 0),
                "total_usdc": self.merge_patterns().get("total_usdc", 0),
                "avg_size": self.merge_patterns().get("avg_size", 0),
                "unique_slugs": self.merge_patterns().get("unique_slugs", 0),
            },
            "time_of_day": {
                "peak_hour_utc": self.time_of_day_patterns().get("peak_hour_utc"),
                "peak_day": self.time_of_day_patterns().get("peak_day"),
            },
            "market_type_preferences": self.market_type_preferences(),
            "consecutive_trades": {
                "avg_per_slug": self.consecutive_trade_patterns().get("avg_buys_per_slug", 0),
                "unique_slugs": self.consecutive_trade_patterns().get("unique_slugs", 0),
            },
            "top_sports_by_profit": self.top_sports_by_profit()[:10],
            "top_markets_by_volume": self.top_markets_by_volume()[:10],
            "computed_at": self._patterns.get("computed_at", ""),
        }

    def find_rn1_style_opportunities(self, current_markets: list[dict]) -> list[dict]:
        """Score current markets based on how closely they match RN1's patterns.

        Args:
            current_markets: List of dicts with keys like:
                slug, sport, market_type, price, question, token_id, neg_risk

        Returns:
            List of dicts with rn1_score (0-100) and component scores.
        """
        if not current_markets:
            return []

        # Build scoring reference data
        sport_prefs = self.sport_preferences()
        total_sport_volume = sum(v.get("buy_usdc", 0) for v in sport_prefs.values())
        sport_volume_pct = {
            sport: v.get("buy_usdc", 0) / total_sport_volume * 100
            for sport, v in sport_prefs.items()
        } if total_sport_volume > 0 else {}

        entry_dist = self.entry_price_distribution()
        total_entry_count = sum(v.get("count", 0) for v in entry_dist.values())
        entry_pct = {
            bucket: v.get("count", 0) / total_entry_count * 100
            for bucket, v in entry_dist.items()
        } if total_entry_count > 0 else {}

        mtype_prefs = self.market_type_preferences()
        total_mtype = sum(v.get("count", 0) for v in mtype_prefs.values())

        tod = self.time_of_day_patterns()
        hour_data = tod.get("by_hour_utc", {})
        total_hour_count = sum(v.get("count", 0) for v in hour_data.values())

        current_hour = datetime.now(timezone.utc).hour

        scored = []
        for market in current_markets:
            slug = market.get("slug", "")
            sport = market.get("sport", "") or _detect_sport(slug)
            mtype = market.get("market_type", "") or _detect_market_type(slug)
            price = market.get("price", 0)

            # 1. Sport match score (0-30)
            sport_score = 0
            sport_pct = sport_volume_pct.get(sport, 0)
            if sport_pct > 0:
                sport_score = min(30, sport_pct / 2)  # Top sports get up to 30
            else:
                # Check category
                cat = _detect_sport_category(sport)
                cat_sports = SPORT_CATEGORY_MAP.get(cat, [])
                cat_pct = sum(sport_volume_pct.get(s, 0) for s in cat_sports)
                sport_score = min(20, cat_pct / 3)

            # 2. Price range score (0-30)
            price_score = 0
            if 0 < price < 1:
                bucket = _price_bucket(price)
                bucket_pct = entry_pct.get(bucket, 0)
                price_score = min(30, bucket_pct * 1.5)
                # Bonus for 5-40c sweet spot
                if 0.05 <= price <= 0.40:
                    price_score = min(30, price_score + 10)

            # 3. Market type score (0-20)
            mtype_score = 0
            if mtype in mtype_prefs and total_mtype > 0:
                mtype_pct = mtype_prefs[mtype]["count"] / total_mtype * 100
                mtype_score = min(20, mtype_pct / 5)

            # 4. Time of day score (0-10)
            time_score = 0
            hour_key = str(current_hour)
            if hour_key in hour_data and total_hour_count > 0:
                hour_pct = hour_data[hour_key]["count"] / total_hour_count * 100
                time_score = min(10, hour_pct * 2)

            # 5. Merge opportunity bonus (0-10)
            merge_score = 0
            # If market has complementary sides summing < $1, it's a merge candidate
            combined_price = market.get("combined_price", 0)
            if combined_price > 0 and combined_price < 1.0:
                discount = (1.0 - combined_price) * 100  # discount in cents
                merge_score = min(10, discount * 2)

            total_score = min(100, sport_score + price_score + mtype_score +
                              time_score + merge_score)

            scored.append({
                **market,
                "rn1_score": round(total_score, 1),
                "rn1_components": {
                    "sport": round(sport_score, 1),
                    "price": round(price_score, 1),
                    "market_type": round(mtype_score, 1),
                    "time_of_day": round(time_score, 1),
                    "merge": round(merge_score, 1),
                },
                "detected_sport": sport,
                "detected_market_type": mtype,
            })

        scored.sort(key=lambda x: -x["rn1_score"])
        return scored
