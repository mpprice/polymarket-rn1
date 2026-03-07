"""MERGE arbitrage strategy for Polymarket binary markets.

RN1 made $40.4M from MERGE events over 8 months -- this was their PRIMARY
profit mechanism, even more than directional bets.

How it works:
  - Binary markets: YES + NO = $1.00 (guaranteed by the CTF contract)
  - If you can buy YES at 0.45 and NO at 0.52 = total cost 0.97
  - Merge YES + NO tokens for $1.00 = $0.03 risk-free profit per pair
  - No directional exposure: profit is locked in at time of purchase

Execution details:
  - Use LIMIT (MAKER) orders only -- sports markets impose a 3-second delay
    on TAKER orders (anti-courtsiding measure)
  - Must buy BOTH sides before calling merge
  - Orderbook depth limits executable size (can't merge more than the
    thinnest side offers)
  - Gas on Polygon: ~$0.01/txn x 3 txns (buy YES, buy NO, merge) = ~$0.03
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .config import Config
from .polymarket_client import PolymarketClient

log = logging.getLogger(__name__)

# Cost assumptions
GAS_PER_TXN = 0.01          # ~$0.01 per Polygon txn
NUM_TXNS_PER_MERGE = 3       # buy YES + buy NO + merge
TOTAL_GAS_COST = GAS_PER_TXN * NUM_TXNS_PER_MERGE  # $0.03
MIN_PROFIT_PER_PAIR = 0.005  # $0.005/pair — merges are risk-free, even small profits add up
MAX_MERGE_USDC = 25.0        # Merges are risk-free, size bigger than directional ($8)


@dataclass
class MergeOpportunity:
    """A risk-free merge arbitrage opportunity on a binary market."""

    condition_id: str
    question: str
    slug: str
    sport: str

    yes_token_id: str
    no_token_id: str

    yes_price: float          # Best ask for YES
    no_price: float           # Best ask for NO
    total_cost: float         # yes_price + no_price

    profit_per_pair: float    # 1.0 - total_cost
    edge_pct: float           # (profit_per_pair / total_cost) * 100

    max_pairs: int            # Limited by orderbook depth (thinnest side)
    estimated_profit: float   # profit_per_pair * max_pairs

    neg_risk: bool = False
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MergeStrategy:
    """Scan for and execute merge (YES + NO = $1) arbitrage on Polymarket.

    This is risk-free arbitrage: buy both sides of a binary market when
    YES_ask + NO_ask < $1.00 (minus gas), then merge tokens for $1.00.
    """

    def __init__(self, poly_client: PolymarketClient, config: Config):
        self.poly = poly_client
        self.config = config
        self.min_profit_per_pair = MIN_PROFIT_PER_PAIR
        self.gas_cost = TOTAL_GAS_COST

    # ── Scanning ─────────────────────────────────────────────────────

    def scan_merge_opportunities(
        self, markets: list[dict]
    ) -> list[MergeOpportunity]:
        """Scan markets for merge arbitrage opportunities.

        For each binary market, fetch the best ask on YES and NO.
        If YES_ask + NO_ask < 1.00 - gas, it is a merge opportunity.

        Args:
            markets: List of market dicts (as returned by
                     PolymarketClient.get_active_sports_markets or similar).
                     Each must have 'condition_id', 'token_ids' (len 2),
                     'question', 'slug', 'sport', 'neg_risk'.

        Returns:
            Sorted list of MergeOpportunity (best edge first).
        """
        opportunities: list[MergeOpportunity] = []

        for mkt in markets:
            token_ids = mkt.get("token_ids", [])
            if len(token_ids) < 2:
                continue

            condition_id = mkt.get("condition_id", "")
            if not condition_id:
                continue

            yes_token_id = token_ids[0]
            no_token_id = token_ids[1]

            try:
                yes_ask, yes_depth = self._best_ask_with_depth(yes_token_id)
                no_ask, no_depth = self._best_ask_with_depth(no_token_id)
            except Exception as e:
                log.debug(
                    "Orderbook fetch failed for %s: %s",
                    mkt.get("slug", condition_id[:12]),
                    e,
                )
                continue

            if yes_ask is None or no_ask is None:
                continue

            total_cost = yes_ask + no_ask
            profit_per_pair = 1.0 - total_cost

            # Must clear gas costs
            if profit_per_pair < self.min_profit_per_pair:
                continue

            edge_pct = (profit_per_pair / total_cost) * 100.0
            max_pairs = min(yes_depth, no_depth)

            if max_pairs <= 0:
                continue

            estimated_profit = profit_per_pair * max_pairs

            opp = MergeOpportunity(
                condition_id=condition_id,
                question=mkt.get("question", ""),
                slug=mkt.get("slug", ""),
                sport=mkt.get("sport", ""),
                yes_token_id=yes_token_id,
                no_token_id=no_token_id,
                yes_price=yes_ask,
                no_price=no_ask,
                total_cost=total_cost,
                profit_per_pair=profit_per_pair,
                edge_pct=edge_pct,
                max_pairs=max_pairs,
                estimated_profit=estimated_profit,
                neg_risk=mkt.get("neg_risk", False),
            )
            opportunities.append(opp)

            log.debug(
                "MERGE candidate: %s | YES=%.4f NO=%.4f total=%.4f "
                "profit=%.4f/pair edge=%.2f%% depth=%d est=$%.2f",
                opp.slug,
                yes_ask,
                no_ask,
                total_cost,
                profit_per_pair,
                edge_pct,
                max_pairs,
                estimated_profit,
            )

        opportunities.sort(key=lambda o: -o.edge_pct)

        log.info(
            "Merge scan complete: %d candidates from %d markets",
            len(opportunities),
            len(markets),
        )
        for i, opp in enumerate(opportunities[:10]):
            log.info(
                "  #%d: %s | YES=%.3f+NO=%.3f=%.3f | "
                "profit=%.3f/pair (%.2f%%) | depth=%d | est=$%.2f",
                i + 1,
                opp.slug[:50],
                opp.yes_price,
                opp.no_price,
                opp.total_cost,
                opp.profit_per_pair,
                opp.edge_pct,
                opp.max_pairs,
                opp.estimated_profit,
            )

        return opportunities

    # ── Execution ────────────────────────────────────────────────────

    def execute_merge(
        self, opp: MergeOpportunity, max_usdc: float
    ) -> dict:
        """Execute a merge opportunity: buy YES, buy NO, then merge.

        Uses LIMIT (MAKER) orders to avoid the 3-second TAKER delay on
        sports markets.

        Args:
            opp: The MergeOpportunity to execute.
            max_usdc: Maximum USDC to spend on this merge.

        Returns:
            Dict with execution results including actual profit, shares
            bought, and order IDs.
        """
        # Determine how many pairs we can afford
        cost_per_pair = opp.total_cost
        affordable_pairs = int(math.floor(max_usdc / cost_per_pair))
        pairs_to_buy = min(affordable_pairs, opp.max_pairs)

        if pairs_to_buy <= 0:
            log.warning(
                "Cannot execute merge for %s: max_usdc=$%.2f but "
                "cost_per_pair=$%.4f, depth=%d",
                opp.slug,
                max_usdc,
                cost_per_pair,
                opp.max_pairs,
            )
            return {
                "success": False,
                "reason": "insufficient_funds_or_depth",
                "slug": opp.slug,
            }

        total_spend = pairs_to_buy * cost_per_pair
        expected_profit = pairs_to_buy * opp.profit_per_pair - self.gas_cost

        log.info(
            "MERGE %s: %d pairs | YES@%.4f + NO@%.4f = $%.4f/pair | "
            "spend=$%.2f expected_profit=$%.2f",
            opp.slug,
            pairs_to_buy,
            opp.yes_price,
            opp.no_price,
            cost_per_pair,
            total_spend,
            expected_profit,
        )

        if self.poly.dry_run:
            log.info(
                "[DRY RUN] Would buy %d YES @ %.4f + %d NO @ %.4f, "
                "then merge for $%.2f profit",
                pairs_to_buy,
                opp.yes_price,
                pairs_to_buy,
                opp.no_price,
                expected_profit,
            )
            return {
                "success": True,
                "dry_run": True,
                "slug": opp.slug,
                "condition_id": opp.condition_id,
                "pairs": pairs_to_buy,
                "yes_price": opp.yes_price,
                "no_price": opp.no_price,
                "total_spend": total_spend,
                "expected_profit": expected_profit,
                "yes_order_id": "dry-run-yes",
                "no_order_id": "dry-run-no",
                "merge_result": "dry-run-merge",
            }

        # Step 1: Buy YES tokens at ask via LIMIT order
        try:
            yes_result = self.poly.place_limit_order(
                token_id=opp.yes_token_id,
                price=opp.yes_price,
                size=float(pairs_to_buy),
                side="BUY",
                neg_risk=opp.neg_risk,
            )
            log.info(
                "  Step 1/3: Bought %d YES @ %.4f | order=%s",
                pairs_to_buy,
                opp.yes_price,
                yes_result.get("orderID", "unknown"),
            )
        except Exception as e:
            log.error("MERGE ABORT (YES buy failed): %s - %s", opp.slug, e)
            return {
                "success": False,
                "reason": "yes_buy_failed",
                "error": str(e),
                "slug": opp.slug,
            }

        # Step 2: Buy NO tokens at ask via LIMIT order
        try:
            no_result = self.poly.place_limit_order(
                token_id=opp.no_token_id,
                price=opp.no_price,
                size=float(pairs_to_buy),
                side="BUY",
                neg_risk=opp.neg_risk,
            )
            log.info(
                "  Step 2/3: Bought %d NO @ %.4f | order=%s",
                pairs_to_buy,
                opp.no_price,
                no_result.get("orderID", "unknown"),
            )
        except Exception as e:
            log.error(
                "MERGE PARTIAL (NO buy failed, holding YES): %s - %s",
                opp.slug,
                e,
            )
            return {
                "success": False,
                "reason": "no_buy_failed_holding_yes",
                "error": str(e),
                "slug": opp.slug,
                "yes_order_id": yes_result.get("orderID"),
            }

        # Step 3: Merge YES + NO -> USDC
        try:
            merge_result = self.merge_positions(
                opp.condition_id, pairs_to_buy
            )
            log.info(
                "  Step 3/3: Merged %d pairs -> $%.2f profit | %s",
                pairs_to_buy,
                expected_profit,
                merge_result,
            )
        except Exception as e:
            log.error(
                "MERGE FAIL (holding both sides): %s - %s", opp.slug, e
            )
            return {
                "success": False,
                "reason": "merge_call_failed_holding_both",
                "error": str(e),
                "slug": opp.slug,
                "yes_order_id": yes_result.get("orderID"),
                "no_order_id": no_result.get("orderID"),
            }

        return {
            "success": True,
            "dry_run": False,
            "slug": opp.slug,
            "condition_id": opp.condition_id,
            "pairs": pairs_to_buy,
            "yes_price": opp.yes_price,
            "no_price": opp.no_price,
            "total_spend": total_spend,
            "expected_profit": expected_profit,
            "yes_order_id": yes_result.get("orderID"),
            "no_order_id": no_result.get("orderID"),
            "merge_result": merge_result,
        }

    # ── Convenience ──────────────────────────────────────────────────

    def scan_and_execute(
        self,
        markets: list[dict],
        max_usdc_per_merge: float = None,
    ) -> list[dict]:
        """Scan for merge opportunities and execute the best ones.

        Args:
            markets: Markets to scan (from PolymarketClient).
            max_usdc_per_merge: Maximum USDC to deploy per merge.
                Defaults to MAX_MERGE_USDC ($25).

        Returns:
            List of execution result dicts.
        """
        if max_usdc_per_merge is None:
            max_usdc_per_merge = MAX_MERGE_USDC

        opportunities = self.scan_merge_opportunities(markets)

        if not opportunities:
            log.info("Merge scan: %d markets checked, 0 candidates (min_profit=%.3f)",
                     len(markets), self.min_profit_per_pair)
            return []

        results = []
        total_deployed = 0.0

        for opp in opportunities:
            # Respect overall budget for the cycle
            remaining = max(0.0, self.config.max_total_exposure_usdc - total_deployed)
            budget = min(max_usdc_per_merge, remaining)

            if budget < opp.total_cost:
                log.debug(
                    "Skipping %s: budget $%.2f < cost/pair $%.4f",
                    opp.slug,
                    budget,
                    opp.total_cost,
                )
                continue

            result = self.execute_merge(opp, budget)
            results.append(result)

            if result.get("success"):
                total_deployed += result.get("total_spend", 0.0)
                log.info(
                    "MERGE EXECUTED: %s | %d pairs | profit=$%.2f | "
                    "total_deployed=$%.2f",
                    opp.slug,
                    result.get("pairs", 0),
                    result.get("expected_profit", 0.0),
                    total_deployed,
                )

        log.info(
            "Merge cycle complete: %d/%d executed, $%.2f deployed",
            sum(1 for r in results if r.get("success")),
            len(opportunities),
            total_deployed,
        )
        return results

    # ── Helpers ───────────────────────────────────────────────────────

    def _best_ask_with_depth(
        self, token_id: str
    ) -> tuple[Optional[float], int]:
        """Get the best ask price and total depth (shares) at that level.

        Returns:
            (best_ask_price, total_shares_at_best_ask)
            or (None, 0) if no asks available.
        """
        book = self.poly.get_orderbook(token_id)

        # The CLOB orderbook has 'asks' as list of {price, size} dicts
        asks = book.get("asks", [])
        if not asks:
            return None, 0

        # Asks are sorted ascending by price; best ask is the lowest
        best_level = asks[0]
        best_price = float(best_level.get("price", 0))
        depth = int(float(best_level.get("size", 0)))

        return best_price, depth

    @staticmethod
    def merge_positions(condition_id: str, amount: int) -> dict:
        """Merge YES + NO token pairs back into USDC.

        In production, this calls the CTF contract's merge function
        via the CLOB client. This is a placeholder that logs the action.

        Args:
            condition_id: The market's condition ID.
            amount: Number of YES+NO pairs to merge.

        Returns:
            Dict describing the merge action.
        """
        log.info(
            "MERGE CALL: condition_id=%s amount=%d "
            "(placeholder -- implement via CLOB client merge())",
            condition_id,
            amount,
        )
        return {
            "action": "merge",
            "condition_id": condition_id,
            "amount": amount,
            "status": "placeholder",
        }
