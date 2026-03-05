"""Main scanner loop: discover markets, compare odds, generate signals."""
import logging
import time
from datetime import datetime, timezone

from .config import Config
from .polymarket_client import PolymarketClient
from .odds_client import OddsClient
from .matcher import match_markets
from .risk_manager import RiskManager

log = logging.getLogger(__name__)


class Scanner:
    """Scan for mispricings between Polymarket and sharp bookmakers."""

    def __init__(self, config: Config, dry_run: bool = True):
        self.config = config
        self.dry_run = dry_run
        self.poly = PolymarketClient(config, dry_run=dry_run)
        self.odds = OddsClient(config)
        self.risk = RiskManager(config)

    def run_once(self) -> list[dict]:
        """Run a single scan cycle. Returns list of actionable opportunities."""
        log.info("=" * 60)
        log.info("Scan cycle at %s", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))

        # 1. Fetch Polymarket sports markets
        pm_markets = self.poly.get_active_sports_markets()
        log.info("Step 1: %d Polymarket sports markets", len(pm_markets))

        # 2. Fetch sharp bookmaker odds
        odds_data = self.odds.get_all_sports_odds(self.config.target_sports)
        total_odds = sum(len(v) for v in odds_data.values())
        log.info("Step 2: %d odds events across %d sports", total_odds, len(odds_data))

        # 3. Match and calculate edges
        matched = match_markets(pm_markets, odds_data)
        log.info("Step 3: %d matched market pairs", len(matched))

        # 4. Filter for actionable opportunities
        opportunities = []
        for m in matched:
            for edge in m["edges"]:
                if edge["edge_pct"] >= self.config.min_edge_pct and edge["side"] == "BUY":
                    size = self.risk.calculate_position_size(
                        edge["edge_pct"],
                        edge["polymarket_price"],
                    )
                    if size > 0 and self.risk.check_can_trade(size):
                        opp = {
                            "slug": m["polymarket"]["slug"],
                            "question": m["polymarket"]["question"],
                            "outcome": edge["outcome"],
                            "token_id": edge["token_id"],
                            "poly_price": edge["polymarket_price"],
                            "fair_prob": edge["fair_prob"],
                            "edge_pct": edge["edge_pct"],
                            "suggested_size_usdc": size,
                            "bookmaker": m["odds_event"]["bookmaker"],
                            "neg_risk": m["polymarket"]["neg_risk"],
                            "market_type": edge.get("market_type", "h2h"),
                            "line": edge.get("line"),
                        }
                        opportunities.append(opp)

        # Sort by edge descending
        opportunities.sort(key=lambda x: -x["edge_pct"])

        log.info("Step 4: %d actionable opportunities (min edge %.1f%%)",
                 len(opportunities), self.config.min_edge_pct)
        for i, opp in enumerate(opportunities[:10]):
            mtype = opp.get("market_type", "h2h")
            log.info("  #%d: %s [%s] (%s) | poly=%.3f fair=%.3f edge=+%.1f%% | size=$%.0f",
                     i + 1, opp["slug"], opp["outcome"], mtype,
                     opp["poly_price"], opp["fair_prob"], opp["edge_pct"],
                     opp["suggested_size_usdc"])

        return opportunities

    def execute_opportunities(self, opportunities: list[dict]):
        """Place orders for identified opportunities."""
        for opp in opportunities:
            try:
                result = self.poly.place_limit_order(
                    token_id=opp["token_id"],
                    price=opp["poly_price"],
                    size=opp["suggested_size_usdc"] / opp["poly_price"],
                    side="BUY",
                    neg_risk=opp["neg_risk"],
                )
                self.risk.record_trade(
                    token_id=opp["token_id"],
                    outcome=opp["outcome"],
                    slug=opp["slug"],
                    side="BUY",
                    size=opp["suggested_size_usdc"] / opp["poly_price"],
                    price=opp["poly_price"],
                    usdc=opp["suggested_size_usdc"],
                )
                log.info("Order placed: %s -> %s", opp["slug"], result)
            except Exception as e:
                log.error("Order failed for %s: %s", opp["slug"], e)

    def run_loop(self, interval_seconds: int = 300):
        """Run scanner in a loop."""
        log.info("Starting scanner loop (interval=%ds, dry_run=%s)", interval_seconds, self.dry_run)
        self.poly.connect()

        while True:
            try:
                opportunities = self.run_once()
                if opportunities:
                    self.execute_opportunities(opportunities)

                risk_summary = self.risk.summary()
                log.info("Risk: exposure=$%.0f/%0.f | positions=%d | realized_pnl=$%.2f",
                         risk_summary["total_exposure"],
                         self.config.max_total_exposure_usdc,
                         risk_summary["open_positions"],
                         risk_summary["realized_pnl"])

            except KeyboardInterrupt:
                log.info("Scanner stopped by user")
                break
            except Exception as e:
                log.error("Scan cycle error: %s", e, exc_info=True)

            log.info("Sleeping %ds until next scan...", interval_seconds)
            time.sleep(interval_seconds)
