"""RN1 Live Signal Generator.

Runs alongside the main strategy, scoring current Polymarket opportunities
based on how closely they match RN1's historical winning patterns.

Produces an rn1_score (0-100) for each opportunity.
"""
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from .rn1_analyzer import RN1Analyzer, _detect_sport, _detect_market_type, _detect_sport_category

log = logging.getLogger(__name__)


class RN1Signals:
    """Live signal generator based on RN1 pattern matching."""

    def __init__(self, force_reload: bool = False):
        """Initialize with RN1 pattern data (from cache if available)."""
        log.info("Initializing RN1 signal generator...")
        self._analyzer = RN1Analyzer(force_reload=force_reload)

        # Pre-compute scoring lookup tables for fast scoring
        self._sport_scores: dict[str, float] = {}
        self._price_bucket_scores: dict[str, float] = {}
        self._mtype_scores: dict[str, float] = {}
        self._hour_scores: dict[int, float] = {}

        self._build_lookup_tables()
        log.info("RN1 signals ready (sport scores: %d, price buckets: %d, "
                 "market types: %d, hours: %d)",
                 len(self._sport_scores), len(self._price_bucket_scores),
                 len(self._mtype_scores), len(self._hour_scores))

    def _build_lookup_tables(self):
        """Pre-compute normalized scores for fast lookup."""
        # Sport scores (0-30 scale)
        sport_prefs = self._analyzer.sport_preferences()
        total_vol = sum(v.get("buy_usdc", 0) for v in sport_prefs.values())
        if total_vol > 0:
            for sport, v in sport_prefs.items():
                pct = v.get("buy_usdc", 0) / total_vol * 100
                self._sport_scores[sport] = min(30.0, pct / 2)

        # Price bucket scores (0-30 scale)
        entry_dist = self._analyzer.entry_price_distribution()
        total_entries = sum(v.get("count", 0) for v in entry_dist.values())
        if total_entries > 0:
            for bucket, v in entry_dist.items():
                pct = v.get("count", 0) / total_entries * 100
                self._price_bucket_scores[bucket] = min(30.0, pct * 1.5)

        # Market type scores (0-20 scale)
        mtype_prefs = self._analyzer.market_type_preferences()
        total_mtype = sum(v.get("count", 0) for v in mtype_prefs.values())
        if total_mtype > 0:
            for mtype, v in mtype_prefs.items():
                pct = v["count"] / total_mtype * 100
                self._mtype_scores[mtype] = min(20.0, pct / 5)

        # Hour scores (0-10 scale)
        tod = self._analyzer.time_of_day_patterns()
        hour_data = tod.get("by_hour_utc", {})
        total_hours = sum(v.get("count", 0) for v in hour_data.values())
        if total_hours > 0:
            for h_str, v in hour_data.items():
                pct = v["count"] / total_hours * 100
                self._hour_scores[int(h_str)] = min(10.0, pct * 2)

    def score_opportunity(self, slug: str, sport: str = "", market_type: str = "",
                          price: float = 0.0, combined_price: float = 0.0) -> float:
        """Score a single opportunity on the RN1 scale (0-100).

        Args:
            slug: Market slug.
            sport: Sport key (auto-detected from slug if empty).
            market_type: h2h, spread, or total (auto-detected if empty).
            price: Entry price (0-1).
            combined_price: YES+NO combined price (for merge scoring).

        Returns:
            Score from 0-100.
        """
        if not sport:
            sport = _detect_sport(slug)
        if not market_type:
            market_type = _detect_market_type(slug)

        # 1. Sport score (0-30)
        sport_score = self._sport_scores.get(sport, 0)
        if sport_score == 0:
            # Try category fallback
            cat = _detect_sport_category(sport)
            from .rn1_analyzer import SPORT_CATEGORY_MAP
            cat_sports = SPORT_CATEGORY_MAP.get(cat, [])
            cat_score = sum(self._sport_scores.get(s, 0) for s in cat_sports)
            sport_score = min(20.0, cat_score / 3)

        # 2. Price score (0-30)
        price_score = 0.0
        if 0 < price < 1:
            bucket_idx = int(price * 100) // 5 * 5
            bucket = f"{bucket_idx}-{bucket_idx+5}c"
            price_score = self._price_bucket_scores.get(bucket, 0)
            # Sweet-spot bonus for 5-40c range
            if 0.05 <= price <= 0.40:
                price_score = min(30.0, price_score + 10)

        # 3. Market type score (0-20)
        mtype_score = self._mtype_scores.get(market_type, 0)

        # 4. Time of day score (0-10)
        current_hour = datetime.now(timezone.utc).hour
        time_score = self._hour_scores.get(current_hour, 0)

        # 5. Merge bonus (0-10)
        merge_score = 0.0
        if combined_price > 0 and combined_price < 1.0:
            discount_cents = (1.0 - combined_price) * 100
            merge_score = min(10.0, discount_cents * 2)

        total = min(100.0, sport_score + price_score + mtype_score +
                    time_score + merge_score)
        return round(total, 1)

    def score_opportunities(self, opportunities: list[dict]) -> list[dict]:
        """Score a batch of opportunities. Each dict should have slug, sport,
        market_type, price. Returns the same dicts with rn1_score added."""
        for opp in opportunities:
            opp["rn1_score"] = self.score_opportunity(
                slug=opp.get("slug", ""),
                sport=opp.get("sport", ""),
                market_type=opp.get("market_type", ""),
                price=opp.get("price", opp.get("poly_price", 0)),
                combined_price=opp.get("combined_price", 0),
            )
        return opportunities

    @property
    def analyzer(self) -> RN1Analyzer:
        """Access underlying analyzer for pattern queries."""
        return self._analyzer

    def get_summary(self) -> dict:
        """Get RN1 pattern summary for dashboard."""
        return self._analyzer.summary()
