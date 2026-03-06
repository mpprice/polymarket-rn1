"""RN1-style sports arbitrage strategy with learning.

Core logic: Compare Polymarket CLOB prices to sharp bookmaker (Pinnacle) odds.
When Polymarket misprices an outcome relative to the sharp line, buy the
underpriced side.

RN1 is highly profitable (+$20.35M total, +$85K/day avg). Key mechanisms:
- Pinnacle-vs-Polymarket pre-game arb (sharp line as fair value)
- MERGE strategy (buy both sides, merge for $1 when mispriced)
- MAKER orders only (avoid 3-second TAKER delay on sports)

Key parameters calibrated from research:
- Focus on 5-40c range (highest mispricing vs sharp books)
- Use MAKER orders only (sports markets have 3-second TAKER delay)
- 5% Kelly fraction max
- Hold to resolution (not scalping)
- Target 3-8% EV per trade (Gambot range)
- Monitor across all sports simultaneously
"""
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from .config import Config
from .polymarket_client import PolymarketClient
from .odds_client import OddsClient
from .matcher import match_markets
from .risk_manager import RiskManager
from .position_tracker import PositionTracker

log = logging.getLogger(__name__)


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


class Strategy:
    """RN1-style sports odds arbitrage strategy with learning."""

    def __init__(self, config: Config, dry_run: bool = True):
        self.config = config
        self.dry_run = dry_run
        self.poly = PolymarketClient(config, dry_run=dry_run)
        self.odds = OddsClient(config)
        self.risk = RiskManager(config)
        self.tracker = PositionTracker(config)

        # Optional modules
        self._learning = None
        self._merge = None
        self._rn1_signals = None

        if config.learning_enabled:
            try:
                from .learning_agent import LearningAgent
                self._learning = LearningAgent()
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
        self.min_liquidity = 0.0
        self.max_edge_pct = config.max_edge_pct

    def scan(self) -> list[Opportunity]:
        """Run a single scan cycle. Returns filtered, sized opportunities."""
        log.info("=" * 60)
        log.info("Scan at %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

        # 1. Fetch Polymarket sports markets
        pm_markets = self.poly.get_active_sports_markets()
        log.info("Step 1: %d Polymarket sports markets", len(pm_markets))

        # 2. Fetch sharp bookmaker odds (h2h + spreads + totals)
        odds_data = self.odds.get_all_sports_odds(self.config.target_sports)
        total_odds = sum(len(v) for v in odds_data.values())
        log.info("Step 2: %d odds events across %d sports (API remaining: %s)",
                 total_odds, len(odds_data), self.odds.requests_remaining)

        # 3. Match markets and calculate edges
        matched = match_markets(pm_markets, odds_data)
        log.info("Step 3: %d matched market pairs", len(matched))

        # 4. Filter and size opportunities
        opportunities = []
        for m in matched:
            for edge in m["edges"]:
                opp = self._evaluate_edge(m, edge)
                if opp:
                    opportunities.append(opp)

        # Sort by edge first, then RN1 score as tiebreaker
        opportunities.sort(key=lambda x: (-(x.adjusted_edge or x.edge_pct), -x.rn1_score))

        log.info("Step 4: %d directional opportunities", len(opportunities))
        for i, opp in enumerate(opportunities[:15]):
            adj = f" adj={opp.adjusted_edge:.1f}%" if opp.adjusted_edge else ""
            rn1 = f" rn1={opp.rn1_score:.0f}" if opp.rn1_score > 0 else ""
            log.info("  #%d: %s [%s] (%s) | poly=%.3f fair=%.3f edge=+%.1f%%%s%s | $%.0f",
                     i + 1, opp.slug, opp.outcome, opp.market_type,
                     opp.poly_price, opp.fair_prob, opp.edge_pct, adj, rn1, opp.size_usdc)

        return opportunities

    def scan_merges(self, pm_markets: list[dict] = None) -> list:
        """Scan for merge arbitrage opportunities."""
        if not self._merge:
            return []
        if pm_markets is None:
            pm_markets = self.poly.get_active_sports_markets()
        return self._merge.scan_merge_opportunities(pm_markets)

    def _evaluate_edge(self, match: dict, edge: dict) -> Opportunity | None:
        """Evaluate a single edge for tradability."""
        pm = match["polymarket"]
        odds = match["odds_event"]

        # Only buy-side (edge > 0)
        if edge["side"] != "BUY":
            return None

        # Edge threshold
        if edge["edge_pct"] < self.min_edge_pct:
            return None

        # Cap unrealistic edges (likely matching errors)
        if edge["edge_pct"] > self.max_edge_pct:
            return None

        # Price range filter: only buy in profitable range
        if edge["polymarket_price"] > self.max_entry_price:
            return None
        if edge["polymarket_price"] < self.min_entry_price:
            return None

        # Liquidity filter
        if pm.get("liquidity", 0) < self.min_liquidity:
            return None

        # Check if already holding this position
        if self.tracker.has_position(edge["token_id"]):
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

        # Position sizing
        sizing_edge = adjusted_edge or edge["edge_pct"]
        size = self.risk.calculate_position_size(
            sizing_edge,
            edge["polymarket_price"],
            kelly_fraction=self.config.kelly_fraction,
        )
        if size <= 0:
            return None
        if not self.risk.check_can_trade(size):
            return None

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
        )

    def execute(self, opportunities: list[Opportunity]):
        """Place orders for opportunities."""
        for opp in opportunities:
            try:
                shares = opp.size_usdc / opp.poly_price
                result = self.poly.place_limit_order(
                    token_id=opp.token_id,
                    price=opp.poly_price,
                    size=shares,
                    side="BUY",
                    neg_risk=opp.neg_risk,
                )

                # Track position
                self.tracker.open_position(
                    token_id=opp.token_id,
                    slug=opp.slug,
                    outcome=opp.outcome,
                    sport=opp.sport,
                    market_type=opp.market_type,
                    entry_price=opp.poly_price,
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
                    price=opp.poly_price,
                    usdc=opp.size_usdc,
                )

                log.info("ORDER: %s %s [%s] %.0f shares @ %.3f ($%.0f) edge=+%.1f%%",
                         opp.market_type.upper(), opp.slug, opp.outcome,
                         shares, opp.poly_price, opp.size_usdc, opp.edge_pct)

            except Exception as e:
                log.error("Order failed for %s: %s", opp.slug, e)

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

                # Check resolutions first
                self.check_resolutions()

                # Scan for directional opportunities
                opps = self.scan()
                if opps:
                    self.execute(opps)

                # Scan for merge opportunities (every 3rd cycle to save API calls)
                if self._merge and cycle % 3 == 0:
                    merge_opps = self.scan_merges()
                    if merge_opps:
                        log.info("Found %d merge opportunities", len(merge_opps))
                        executed = self._merge.scan_and_execute(
                            markets=None,  # uses cached
                            max_usdc_per_merge=self.config.max_position_usdc,
                        )
                        for ex in executed:
                            log.info("MERGE: %s profit=$%.4f", ex.get("slug", ""), ex.get("profit", 0))

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
