"""RN1-style sports arbitrage strategy with learning.

Core logic: Compare Polymarket CLOB prices to sharp bookmaker (Pinnacle) odds.
When Polymarket misprices an outcome relative to the sharp line, buy the
underpriced side.

RN1 is highly profitable (+$20.35M total, +$85K/day avg). Key mechanisms:
- Pinnacle-vs-Polymarket pre-game arb (sharp line as fair value)
- MERGE strategy (buy both sides, merge for $1 when mispriced)
- MAKER orders only (avoid 3-second TAKER delay on sports)

Key parameters calibrated from research:
- Full 5-95c price range for h2h/spread/total markets
- Use MAKER orders only (sports markets have 3-second TAKER delay)
- 15% Kelly fraction, $8 max position
- Min $1K 24h volume (avoid market impact), prefer higher volume
- Hold to resolution (not scalping)
- Target 3-20% edge per trade, min $100 liquidity
- Monitor 26 sports simultaneously
- Slippage tracking: expected vs fill price logged per trade
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
import os

from .config import Config
from .polymarket_client import PolymarketClient
from .odds_client import OddsClient
from .matcher import match_markets
from .risk_manager import RiskManager
from .position_tracker import PositionTracker

MAX_POSITION_SCALE = 3  # Can hold up to 3x max_position_usdc per market
MAX_DAYS_TO_EVENT = 10  # Balance capital recycling vs trade count
MAX_SPREAD_BPS = 300  # Skip markets where bid-ask spread exceeds edge
# Sub-market slug patterns to exclude (phantom edges from comparing sub-market to match-winner odds)
SUB_MARKET_PATTERNS = (
    "-first-set-", "-set-handicap-", "-set-totals-", "-match-total-",
    "-first-half-", "-second-half-", "-first-quarter-", "-1st-set-",
    "-2nd-set-", "-3rd-set-", "-first-5-innings-", "-first-period-",
)
# High-liquidity sports (kept for backward compat in external imports)
HIGH_LIQ_SPORTS = {"nba", "nfl", "nhl", "epl", "ucl", "bun", "lal", "sea", "fl1", "mlb", "efa"}

# Sport tiers by RN1 ROI analysis — higher tier = more aggressive sizing
SPORT_TIER = {
    # Tier 1: US sports (80-112% ROI) — 1.5x Kelly boost
    "cfb": 1, "nba": 1, "nfl": 1,
    # Tier 2: Esports (35-50% ROI) — 1.3x Kelly boost
    "cs2": 2, "dota2": 2, "lol": 2, "val": 2, "codmw": 2,
    # Tier 3: Top soccer + hockey (35-48% ROI) — 1.2x Kelly boost
    "epl": 3, "bun": 3, "nhl": 3, "mlb": 3,
    # Tier 4: Everything else — 1.0x (default)
}

TIER_KELLY_BOOST = {
    1: 1.5,   # US sports get 50% more Kelly
    2: 1.3,   # Esports get 30% more
    3: 1.2,   # Top soccer/hockey get 20% more
    4: 1.0,   # Default
}

log = logging.getLogger(__name__)


MIN_VOLUME_24H = 1000  # $1K minimum 24h volume — avoid moving illiquid markets
FILL_SIZE = 2.0  # $2 per fill — split orders into small chunks to minimize market impact

@dataclass
class Opportunity:
    slug: str
    question: str
    outcome: str
    token_id: str
    market_type: str
    poly_price: float
    fair_prob: float
    edge_pct: float
    size_usdc: float
    bookmaker: str
    neg_risk: bool
    line: float = None
    sport: str = ""
    commence_time: str = ""
    adjusted_edge: float = None  # After learning adjustment
    rn1_score: float = 0.0  # RN1 pattern match score (0-100)
    volume_24h: float = 0.0  # Polymarket 24h volume in USD
    spread_bps: float = 0.0  # Effective bid-ask spread in basis points
    effective_mid: float = 0.0  # Effective midpoint from both YES/NO books


class Strategy:
    """RN1-style sports odds arbitrage strategy with learning."""

    def __init__(self, config: Config, dry_run: bool = True):
        self.config = config
        self.dry_run = dry_run
        self.poly = PolymarketClient(config, dry_run=dry_run)
        self.odds = OddsClient(config)
        self.data_dir = config.data_dir
        os.makedirs(self.data_dir, exist_ok=True)
        self.risk = RiskManager(config)
        self.tracker = PositionTracker(config, data_dir=self.data_dir)
        self.risk.sync_from_tracker(self.tracker)

        # Optional modules
        self._learning = None
        self._merge = None
        self._rn1_signals = None

        if config.learning_enabled:
            try:
                from .learning_agent import LearningAgent
                self._learning = LearningAgent(data_dir=self.data_dir)
                log.info("Learning agent enabled (%d historical trades)",
                         len(self._learning.trades))
            except Exception as e:
                log.warning("Learning agent unavailable: %s", e)

        if config.merge_enabled:
            try:
                from .merge_strategy import MergeStrategy
                self._merge = MergeStrategy(self.poly, config)
                log.info("Merge arbitrage enabled")
            except Exception as e:
                log.warning("Merge strategy unavailable: %s", e)

        # RN1 pattern signals
        try:
            from .rn1_signals import RN1Signals
            self._rn1_signals = RN1Signals()
            log.info("RN1 signals enabled")
        except Exception as e:
            log.warning("RN1 signals unavailable: %s", e)

        # Strategy-specific parameters
        self.min_edge_pct = config.min_edge_pct
        self.max_entry_price = config.max_entry_price
        self.min_entry_price = config.min_entry_price
        self.min_liquidity = 100.0  # $100 floor: filter truly illiquid markets only
        self.max_edge_pct = config.max_edge_pct

        # Cache of last-fetched PM markets (reused for merge scan)
        self._last_pm_markets: list[dict] = []

        # MAKER order tracking for stale cancellation
        self._pending_maker_orders: list[dict] = []
        self.maker_order_ttl = 120  # Cancel unfilled MAKER orders after 2 minutes

    def scan(self) -> list[Opportunity]:
        """Run a single scan cycle. Returns filtered, sized opportunities."""
        log.info("=" * 60)
        log.info("Scan at %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

        # 1. Fetch Polymarket sports markets
        pm_markets = self.poly.get_active_sports_markets()
        self._last_pm_markets = pm_markets  # Reuse for merge scan (avoid re-fetch)
        log.info("Step 1: %d Polymarket sports markets", len(pm_markets))

        # 2. Fetch sharp bookmaker odds (h2h + spreads + totals)
        if self.odds._oddspapi:
            self.odds._oddspapi.clear_cache()
        odds_data = self.odds.get_all_sports_odds(self.config.target_sports)
        total_odds = sum(len(v) for v in odds_data.values())
        log.info("Step 2: %d odds events across %d sports (API remaining: %s)",
                 total_odds, len(odds_data), self.odds.requests_remaining)

        # 3. Match markets and calculate edges
        matched = match_markets(pm_markets, odds_data)
        log.info("Step 3: %d matched market pairs", len(matched))

        # 4. Filter and size opportunities — track pending exposure so we
        #    don't blow past the max in a single scan batch
        self._pending_exposure = 0.0
        self._filter_counts = {}
        total_edges = 0
        candidates = []
        for m in matched:
            for edge in m["edges"]:
                total_edges += 1
                opp = self._evaluate_edge(m, edge)
                if opp:
                    candidates.append(opp)
        if total_edges > 0:
            log.info("Edge filter breakdown (%d total, %d passed): %s",
                     total_edges, len(candidates), self._filter_counts)

        # Sort: best tier first, then by edge, tight spreads, volume, RN1 score
        candidates.sort(key=lambda x: (
            SPORT_TIER.get(x.sport, 4),  # Lower tier number = higher priority
            -(x.adjusted_edge or x.edge_pct),
            x.spread_bps,  # tighter spreads preferred
            -x.volume_24h,
            -x.rn1_score,
        ))

        # Second pass: enforce cumulative exposure limit (best edges first)
        opportunities = []
        cumulative = self.risk.total_exposure
        for opp in candidates:
            if cumulative + opp.size_usdc > self.config.max_total_exposure_usdc:
                remaining = self.config.max_total_exposure_usdc - cumulative
                if remaining < 0.50:
                    break
                opp.size_usdc = round(remaining, 2)
            cumulative += opp.size_usdc
            opportunities.append(opp)

        log.info("Step 4: %d directional opportunities", len(opportunities))
        for i, opp in enumerate(opportunities[:15]):
            adj = f" adj={opp.adjusted_edge:.1f}%" if opp.adjusted_edge is not None else ""
            rn1 = f" rn1={opp.rn1_score:.0f}" if opp.rn1_score > 0 else ""
            spr = f" spr={opp.spread_bps:.0f}bp" if opp.spread_bps > 0 else ""
            liq = f" T{SPORT_TIER.get(opp.sport, 4)}"
            log.info("  #%d: %s [%s] (%s) | poly=%.3f fair=%.3f edge=+%.1f%%%s%s%s%s | $%.0f",
                     i + 1, opp.slug, opp.outcome, opp.market_type,
                     opp.poly_price, opp.fair_prob, opp.edge_pct, adj, rn1, spr, liq,
                     opp.size_usdc)

        return opportunities

    def scan_merges(self, pm_markets: list[dict] = None) -> list:
        """Scan for merge arbitrage opportunities."""
        if not self._merge:
            return []
        if pm_markets is None:
            pm_markets = self.poly.get_active_sports_markets()
        return self._merge.scan_merge_opportunities(pm_markets)

    def _has_conflicting_position(self, slug: str, outcome: str) -> bool:
        """Check if we already hold the opposite side of this market.

        Prevents: Over + Under on same total, both teams on same h2h,
        or adjacent total lines on the same game (e.g. O241.5 + U240.5).
        """
        import re
        # Extract base game slug (without spread/total suffix)
        base = re.sub(r'-(?:total|spread)-.*$', '', slug)

        for pos in self.tracker.positions.values():
            if pos.status != "open":
                continue
            pos_base = re.sub(r'-(?:total|spread)-.*$', '', pos.slug)
            if pos_base != base:
                continue

            # Same slug, different outcome = direct conflict (Over vs Under, Team A vs Team B)
            if pos.slug == slug and pos.outcome != outcome:
                log.debug("Conflict: already hold %s on %s, skipping %s", pos.outcome, slug, outcome)
                return True

            # Adjacent total lines on same game (e.g. O241.5 + U240.5)
            if "total" in slug and "total" in pos.slug:
                is_over = outcome.lower() == "over"
                pos_is_over = pos.outcome.lower() == "over"
                if is_over != pos_is_over:
                    log.debug("Conflict: opposite side of adjacent total on %s, skipping", base)
                    return True

        return False

    def _evaluate_edge(self, match: dict, edge: dict) -> Opportunity | None:
        """Evaluate a single edge for tradability."""
        pm = match["polymarket"]
        odds = match["odds_event"]
        slug = pm.get("slug", "")

        # Sub-market filter: skip first-set, set-handicap etc. (phantom edges)
        for pattern in SUB_MARKET_PATTERNS:
            if pattern in slug:
                self._filter_counts["sub_market"] = self._filter_counts.get("sub_market", 0) + 1
                return None

        # Only buy-side (edge > 0)
        if edge["side"] != "BUY":
            self._filter_counts["no_buy"] = self._filter_counts.get("no_buy", 0) + 1
            return None

        # Edge threshold
        if edge["edge_pct"] < self.min_edge_pct:
            self._filter_counts["low_edge"] = self._filter_counts.get("low_edge", 0) + 1
            return None

        # Cap unrealistic edges (likely matching errors)
        if edge["edge_pct"] > self.max_edge_pct:
            self._filter_counts["high_edge"] = self._filter_counts.get("high_edge", 0) + 1
            return None

        # Price range filter: only buy in profitable range
        if edge["polymarket_price"] > self.max_entry_price:
            self._filter_counts["price_high"] = self._filter_counts.get("price_high", 0) + 1
            return None
        if edge["polymarket_price"] < self.min_entry_price:
            self._filter_counts["price_low"] = self._filter_counts.get("price_low", 0) + 1
            return None

        # Time-to-resolution filter: skip events more than 5 days out
        commence = odds.get("commence_time", "")
        if commence:
            try:
                ct = datetime.fromisoformat(commence.replace("Z", "+00:00"))
                hours_out = (ct - datetime.now(timezone.utc)).total_seconds() / 3600
                if hours_out > MAX_DAYS_TO_EVENT * 24:
                    self._filter_counts["too_far"] = self._filter_counts.get("too_far", 0) + 1
                    return None
            except (ValueError, TypeError):
                pass

        # Liquidity filter
        if pm.get("liquidity", 0) < self.min_liquidity:
            self._filter_counts["liquidity"] = self._filter_counts.get("liquidity", 0) + 1
            return None

        # Volume filter: avoid markets where our trades move the price
        if pm.get("volume_24h", 0) < MIN_VOLUME_24H:
            self._filter_counts["low_volume"] = self._filter_counts.get("low_volume", 0) + 1
            return None

        # Spread filter: fetch effective spread from both YES/NO orderbooks
        spread_info = None
        spread_bps = 0.0
        effective_mid = edge["polymarket_price"]
        try:
            spread_info = self.poly.get_effective_spread(edge["token_id"])
            if spread_info:
                spread_bps = spread_info["spread_bps"]
                effective_mid = spread_info["mid"]
                # Skip if spread exceeds edge (would eat all profit)
                edge_bps = edge["edge_pct"] * 100  # edge_pct is %, convert to bps
                if spread_bps > edge_bps:
                    self._filter_counts["wide_spread"] = self._filter_counts.get("wide_spread", 0) + 1
                    return None
                # Also enforce absolute max spread
                if spread_bps > MAX_SPREAD_BPS:
                    self._filter_counts["max_spread"] = self._filter_counts.get("max_spread", 0) + 1
                    return None
        except Exception:
            pass  # Fall through if spread unavailable

        # Position scaling: allow adding to existing positions up to MAX_POSITION_SCALE
        existing_cost = self.tracker.get_position_cost(edge["token_id"])
        max_per_market = self.config.max_position_usdc * MAX_POSITION_SCALE
        if existing_cost >= max_per_market:
            self._filter_counts["max_scaled"] = self._filter_counts.get("max_scaled", 0) + 1
            return None

        # Prevent contradictory positions (both sides of same game/line)
        outcome = edge.get("outcome", "")
        if slug and self._has_conflicting_position(slug, outcome):
            self._filter_counts["conflict"] = self._filter_counts.get("conflict", 0) + 1
            return None

        # Learning-adjusted edge
        adjusted_edge = None
        if self._learning:
            adjusted_edge = self._learning.adjusted_edge(
                raw_edge_pct=edge["edge_pct"],
                sport=pm.get("sport", ""),
                market_type=edge.get("market_type", "h2h"),
                entry_price=edge["polymarket_price"],
                bookmaker=odds.get("bookmaker", ""),
            )
            # If learning says edge is below threshold, skip
            if adjusted_edge < self.min_edge_pct:
                log.debug("Learning adjusted %s edge %.1f%% -> %.1f%% (below threshold)",
                          pm["slug"], edge["edge_pct"], adjusted_edge)
                return None

        # Position sizing — boost Kelly fraction for high-ROI sport tiers
        sizing_edge = adjusted_edge or edge["edge_pct"]
        sport = pm.get("sport", "")
        tier = SPORT_TIER.get(sport, 4)
        kelly_boost = TIER_KELLY_BOOST.get(tier, 1.0)
        boosted_kelly = self.config.kelly_fraction * kelly_boost
        size = self.risk.calculate_position_size(
            sizing_edge,
            edge["polymarket_price"],
            kelly_fraction=boosted_kelly,
        )
        if size < 1.0:
            self._filter_counts["size_below_min"] = self._filter_counts.get("size_below_min", 0) + 1
            return None

        # Cap size so total position doesn't exceed max_per_market
        if existing_cost > 0:
            remaining = max_per_market - existing_cost
            if remaining < 1.0:  # $1 minimum order
                self._filter_counts["max_scaled"] = self._filter_counts.get("max_scaled", 0) + 1
                return None
            size = min(size, remaining)

        # Note: cumulative exposure limit enforced in scan() second pass

        # RN1 pattern score
        rn1_score = 0.0
        if self._rn1_signals:
            rn1_score = self._rn1_signals.score_opportunity(
                slug=pm["slug"],
                sport=pm.get("sport", ""),
                market_type=edge.get("market_type", "h2h"),
                price=edge["polymarket_price"],
            )

        return Opportunity(
            slug=pm["slug"],
            question=pm["question"],
            outcome=edge["outcome"],
            token_id=edge["token_id"],
            market_type=edge.get("market_type", "h2h"),
            poly_price=edge["polymarket_price"],
            fair_prob=edge["fair_prob"],
            edge_pct=edge["edge_pct"],
            size_usdc=size,
            bookmaker=odds["bookmaker"],
            neg_risk=pm["neg_risk"],
            line=edge.get("line"),
            sport=pm.get("sport", ""),
            commence_time=odds.get("commence_time", ""),
            adjusted_edge=adjusted_edge,
            rn1_score=rn1_score,
            volume_24h=pm.get("volume_24h", 0),
            spread_bps=spread_bps,
            effective_mid=effective_mid,
        )

    def execute(self, opportunities: list[Opportunity]):
        """Place orders for opportunities.

        Hybrid execution with order fragmentation:
        - TAKER orders are split into multiple small fills ($FILL_SIZE each)
          to minimize market impact and walk the orderbook depth, mimicking
          RN1's median $10.62 per fill across 10-30 fills per position.
        - MAKER orders remain a single resting limit (they don't move the market).
        - Unfilled MAKER orders are cancelled after MAKER_ORDER_TTL_SECONDS.
        """
        for opp in opportunities:
            try:
                # Fetch live spread from both YES/NO orderbooks
                spread = self.poly.get_effective_spread(opp.token_id)
                if spread:
                    best_ask = spread["ask"]
                    best_bid = spread["bid"]
                    mid = spread["mid"]
                    live_spread_bps = spread["spread_bps"]
                else:
                    # Fallback to Gamma price
                    best_ask = opp.poly_price
                    best_bid = opp.poly_price
                    mid = opp.poly_price
                    live_spread_bps = 0.0

                # Check edge at best ask (worst case for buyer)
                edge_at_ask = (opp.fair_prob - best_ask) / best_ask * 100 if best_ask > 0 else 0
                edge_at_mid = (opp.fair_prob - mid) / mid * 100 if mid > 0 else 0

                # Skip if no edge even at midpoint
                if edge_at_mid < self.min_edge_pct:
                    log.info("SKIP: %s [%s] edge at mid=%.1f%% (< %.1f%%) spread=%.0fbps",
                             opp.slug, opp.outcome, edge_at_mid, self.min_edge_pct, live_spread_bps)
                    continue

                # Hybrid: cross spread if edge is high at ask, else MAKER at mid
                if edge_at_ask >= self.min_edge_pct * 2:
                    # Strong edge — cross spread for immediate fill
                    order_price = best_ask
                    order_mode = "TAKER"
                elif edge_at_ask >= self.min_edge_pct:
                    # Moderate edge at ask — still worth crossing
                    order_price = best_ask
                    order_mode = "TAKER"
                else:
                    # Edge only exists at mid — place MAKER order
                    order_price = mid
                    order_mode = "MAKER"

                # ── TAKER: fragment into multiple small fills ────────────
                if order_mode == "TAKER" and opp.size_usdc >= FILL_SIZE * 2:
                    self._execute_fragmented_taker(opp, order_price, edge_at_ask,
                                                   edge_at_mid, live_spread_bps)
                    continue

                # ── Single order path (MAKER or small TAKER) ────────────
                shares = opp.size_usdc / order_price

                result = self.poly.place_limit_order(
                    token_id=opp.token_id,
                    price=order_price,
                    size=shares,
                    side="BUY",
                    neg_risk=opp.neg_risk,
                )

                # Track MAKER orders for later cancellation if unfilled
                order_id = None
                if isinstance(result, dict):
                    order_id = result.get("orderID") or result.get("id")
                if order_mode == "MAKER" and order_id and order_id != "dry-run":
                    self._pending_maker_orders.append({
                        "order_id": order_id,
                        "placed_at": time.time(),
                        "token_id": opp.token_id,
                        "slug": opp.slug,
                        "outcome": opp.outcome,
                    })

                # In live mode, check actual fill price
                fill_price = None
                if not self.dry_run and isinstance(result, dict):
                    fill_price = result.get("avgPrice") or result.get("price")
                    if fill_price:
                        fill_price = float(fill_price)

                actual_price = fill_price or order_price

                # Track position
                self.tracker.open_position(
                    token_id=opp.token_id,
                    slug=opp.slug,
                    outcome=opp.outcome,
                    sport=opp.sport,
                    market_type=opp.market_type,
                    entry_price=actual_price,
                    fair_prob=opp.fair_prob,
                    edge_pct=opp.edge_pct,
                    shares=shares,
                    cost_usdc=opp.size_usdc,
                    bookmaker=opp.bookmaker,
                )

                self.risk.record_trade(
                    token_id=opp.token_id,
                    outcome=opp.outcome,
                    slug=opp.slug,
                    side="BUY",
                    size=shares,
                    price=actual_price,
                    usdc=opp.size_usdc,
                )

                # Tag scaling orders
                is_scale = self.tracker.get_position_cost(opp.token_id) > opp.size_usdc
                mode_tag = f"{order_mode}/SCALE" if is_scale else order_mode

                log.info("ORDER [%s]: %s %s [%s] %.0f shares @ %.3f ($%.1f) "
                         "edge_ask=%.1f%% edge_mid=%.1f%% spread=%.0fbps vol=$%.0f",
                         mode_tag, opp.market_type.upper(), opp.slug, opp.outcome,
                         shares, order_price, opp.size_usdc,
                         edge_at_ask, edge_at_mid, live_spread_bps, opp.volume_24h)

            except Exception as e:
                log.error("Order failed for %s: %s", opp.slug, e)

    def _execute_fragmented_taker(self, opp: Opportunity, base_price: float,
                                   edge_at_ask: float, edge_at_mid: float,
                                   live_spread_bps: float):
        """Split a TAKER order into multiple small fills to minimize market impact.

        Mimics RN1's execution style: median $10.62 per fill across 10-30 fills.
        Walks the orderbook by fetching ask levels and distributing fills across them.
        Each fill is >= $1 (CLOB minimum) with a 0.3s delay between fills.
        """
        num_fills = max(1, int(opp.size_usdc / FILL_SIZE))
        fill_usdc = opp.size_usdc / num_fills

        # Ensure each fill meets CLOB minimum of $1
        if fill_usdc < 1.0:
            num_fills = max(1, int(opp.size_usdc))
            fill_usdc = opp.size_usdc / num_fills

        # Try to fetch orderbook for price levels to walk
        ask_levels = []
        try:
            book = self.poly.get_orderbook(opp.token_id)
            if book and hasattr(book, "asks") and book.asks:
                ask_levels = sorted(book.asks, key=lambda lvl: float(lvl.price))
            elif isinstance(book, dict) and book.get("asks"):
                ask_levels = sorted(book["asks"],
                                    key=lambda lvl: float(lvl.get("price", lvl.get("p", 0))))
        except Exception:
            pass  # Fall back to base_price for all fills

        total_shares = 0.0
        total_cost = 0.0
        fills_placed = 0
        ask_idx = 0
        level_filled = 0.0  # Shares consumed at current ask level

        for i in range(num_fills):
            # Determine price for this fill: walk ask levels if available
            if ask_levels and ask_idx < len(ask_levels):
                lvl = ask_levels[ask_idx]
                level_price = float(lvl.price if hasattr(lvl, "price")
                                    else lvl.get("price", lvl.get("p", base_price)))
                level_size = float(lvl.size if hasattr(lvl, "size")
                                   else lvl.get("size", lvl.get("s", 0)))
                fill_price = level_price
                fill_shares = fill_usdc / fill_price
                # If we've consumed this level's available size, move to next
                if level_filled + fill_shares >= level_size and ask_idx + 1 < len(ask_levels):
                    ask_idx += 1
                    level_filled = 0.0
                else:
                    level_filled += fill_shares
            else:
                fill_price = base_price
                fill_shares = fill_usdc / fill_price

            try:
                result = self.poly.place_limit_order(
                    token_id=opp.token_id,
                    price=fill_price,
                    size=fill_shares,
                    side="BUY",
                    neg_risk=opp.neg_risk,
                )

                # Check actual fill price in live mode
                actual_fill_price = fill_price
                if not self.dry_run and isinstance(result, dict):
                    fp = result.get("avgPrice") or result.get("price")
                    if fp:
                        actual_fill_price = float(fp)

                total_shares += fill_shares
                total_cost += fill_shares * actual_fill_price
                fills_placed += 1

                log.info("FILL %d/%d: %s [%s] %.0f shares @ %.3f ($%.1f)",
                         fills_placed, num_fills, opp.slug, opp.outcome,
                         fill_shares, actual_fill_price, fill_usdc)

            except Exception as e:
                log.warning("Fill %d/%d failed for %s: %s", i + 1, num_fills, opp.slug, e)

            # Delay between fills (skip after last fill)
            if i < num_fills - 1:
                time.sleep(0.3)

        if fills_placed == 0:
            log.error("All fills failed for %s [%s]", opp.slug, opp.outcome)
            return

        # Calculate volume-weighted average entry price
        avg_price = total_cost / total_shares if total_shares > 0 else base_price
        actual_usdc = total_shares * avg_price

        # Track position ONCE with aggregate values
        self.tracker.open_position(
            token_id=opp.token_id,
            slug=opp.slug,
            outcome=opp.outcome,
            sport=opp.sport,
            market_type=opp.market_type,
            entry_price=avg_price,
            fair_prob=opp.fair_prob,
            edge_pct=opp.edge_pct,
            shares=total_shares,
            cost_usdc=actual_usdc,
            bookmaker=opp.bookmaker,
        )

        self.risk.record_trade(
            token_id=opp.token_id,
            outcome=opp.outcome,
            slug=opp.slug,
            side="BUY",
            size=total_shares,
            price=avg_price,
            usdc=actual_usdc,
        )

        # Tag scaling orders
        is_scale = self.tracker.get_position_cost(opp.token_id) > actual_usdc
        mode_tag = "TAKER %d fills/SCALE" % fills_placed if is_scale else "TAKER %d fills" % fills_placed

        log.info("ORDER [%s]: %s %s [%s] %.0f shares @ %.3f avg ($%.1f) "
                 "edge_ask=%.1f%% edge_mid=%.1f%% spread=%.0fbps vol=$%.0f",
                 mode_tag, opp.market_type.upper(), opp.slug, opp.outcome,
                 total_shares, avg_price, actual_usdc,
                 edge_at_ask, edge_at_mid, live_spread_bps, opp.volume_24h)

    def check_resolutions(self):
        """Check if any open positions have resolved and feed results to learning agent."""
        resolved = self.tracker.check_resolutions(self.poly)
        for res in resolved:
            log.info("RESOLVED: %s [%s] -> %s | PnL=$%.2f (entry=%.3f, shares=%.0f)",
                     res["slug"], res["outcome"], "WON" if res["won"] else "LOST",
                     res["pnl"], res["entry_price"], res["shares"])
            self.risk.record_resolution(res["token_id"], res["payout"])

            # Feed to learning agent
            if self._learning:
                pos = self.tracker.positions.get(res["token_id"])
                if pos:
                    from .learning_agent import TradeOutcome
                    self._learning.record_outcome(TradeOutcome(
                        token_id=res["token_id"],
                        slug=res["slug"],
                        sport=pos.sport,
                        market_type=pos.market_type,
                        outcome=res["outcome"],
                        entry_price=res["entry_price"],
                        fair_prob_at_entry=pos.fair_prob,
                        edge_pct_at_entry=pos.edge_pct,
                        shares=res["shares"],
                        cost_usdc=pos.cost_usdc,
                        bookmaker=pos.bookmaker,
                        opened_at=pos.opened_at,
                        resolved_at=pos.closed_at,
                        won=res["won"],
                        pnl=res["pnl"],
                        resolution_price=res.get("resolution_price", 0),
                    ))

    def cancel_stale_maker_orders(self):
        """Cancel unfilled MAKER orders that have been on the book too long.

        Market may have moved against us — no point leaving stale orders.
        Only applies to live trading (dry run orders are simulated).
        """
        if self.dry_run or not self._pending_maker_orders:
            return

        now = time.time()
        still_pending = []
        for order in self._pending_maker_orders:
            age = now - order["placed_at"]
            if age > self.maker_order_ttl:
                try:
                    self.poly.cancel_order(order["order_id"])
                    log.info("CANCEL stale MAKER: %s [%s] after %ds (order=%s)",
                             order["slug"], order["outcome"], int(age),
                             order["order_id"][:16])
                except Exception as e:
                    log.debug("Cancel failed for %s: %s", order["order_id"][:16], e)
            else:
                still_pending.append(order)
        self._pending_maker_orders = still_pending

    def run_loop(self, interval: int = None):
        """Run strategy in a continuous loop."""
        interval = interval or self.config.scan_interval_seconds
        log.info("Starting strategy loop (interval=%ds, dry_run=%s)", interval, self.dry_run)

        if not self.dry_run:
            self.poly.connect()

        cycle = 0
        while True:
            cycle += 1
            try:
                log.info("--- Cycle %d ---", cycle)

                # Check resolutions first (UMA fully resolved)
                self.check_resolutions()

                # Cancel stale MAKER orders before scanning for new ones
                self.cancel_stale_maker_orders()

                # Early exits disabled — hold to resolution (~2h) to avoid
                # paying the bid-ask spread on exit (spread drag > time value)

                # Scan for directional opportunities
                opps = self.scan()
                if opps:
                    self.execute(opps)

                # Scan for merge opportunities (every cycle — merges are risk-free)
                if self._merge:
                    merge_opps = self.scan_merges(self._last_pm_markets)
                    if merge_opps:
                        log.info("Found %d merge opportunities", len(merge_opps))
                        executed = self._merge.scan_and_execute(
                            markets=self._last_pm_markets,
                        )
                        batch_profit = sum(
                            ex.get("expected_profit", 0)
                            for ex in executed
                            if ex.get("success")
                        )
                        for ex in executed:
                            log.info("MERGE: %s profit=$%.4f", ex.get("slug", ""), ex.get("expected_profit", 0))
                        if batch_profit > 0:
                            log.info("Merge batch total estimated profit: $%.4f", batch_profit)

                # Log portfolio state
                summary = self.tracker.summary()
                risk = self.risk.summary()
                log.info("Portfolio: %d open | $%.0f exposure/$%.0f max | $%.2f realized PnL | "
                         "%d wins / %d losses",
                         summary["open_count"], risk["total_exposure"],
                         self.config.max_total_exposure_usdc,
                         summary["realized_pnl"],
                         summary["total_wins"], summary["total_losses"])

                # Print learning report every 10 cycles
                if self._learning and cycle % 10 == 0:
                    metrics = self._learning.export_metrics()
                    if metrics.get("total_trades", 0) > 0:
                        log.info("Learning: %d trades | %.1f%% win rate | "
                                 "best sport: %s | best type: %s",
                                 metrics["total_trades"],
                                 metrics.get("overall_win_rate", 0) * 100,
                                 metrics.get("best_sport", "n/a"),
                                 metrics.get("best_market_type", "n/a"))

            except KeyboardInterrupt:
                log.info("Strategy stopped by user")
                self.tracker.save()
                if self._learning:
                    self._learning.save()
                break
            except Exception as e:
                log.error("Strategy cycle error: %s", e, exc_info=True)

            log.info("Sleeping %ds...", interval)
            time.sleep(interval)
