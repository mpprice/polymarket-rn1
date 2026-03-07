"""Track open positions, resolutions, and PnL with persistence."""
import csv
import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Position:
    token_id: str
    slug: str
    outcome: str
    sport: str
    market_type: str
    entry_price: float
    fair_prob: float
    edge_pct: float
    shares: float
    cost_usdc: float
    bookmaker: str
    opened_at: str = ""
    status: str = "open"       # open, won, lost, early_exit
    resolution_price: float = 0.0
    payout: float = 0.0
    pnl: float = 0.0
    closed_at: str = ""


class PositionTracker:
    """Tracks all positions with CSV persistence for auditability."""

    def __init__(self, config: Config, data_dir: str = None):
        self.config = config
        self.data_dir = data_dir or os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "data"
        )
        self.positions_file = os.path.join(self.data_dir, "my_positions.csv")
        self.trades_file = os.path.join(self.data_dir, "my_trades.csv")
        self.positions: dict[str, Position] = {}
        self._load()

    def _load(self):
        """Load positions from CSV."""
        if not os.path.exists(self.positions_file):
            return
        try:
            with open(self.positions_file, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    pos = Position(
                        token_id=row["token_id"],
                        slug=row["slug"],
                        outcome=row["outcome"],
                        sport=row.get("sport", ""),
                        market_type=row.get("market_type", "h2h"),
                        entry_price=float(row.get("entry_price", 0)),
                        fair_prob=float(row.get("fair_prob", 0)),
                        edge_pct=float(row.get("edge_pct", 0)),
                        shares=float(row.get("shares", 0)),
                        cost_usdc=float(row.get("cost_usdc", 0)),
                        bookmaker=row.get("bookmaker", ""),
                        opened_at=row.get("opened_at", ""),
                        status=row.get("status", "open"),
                        resolution_price=float(row.get("resolution_price", 0)),
                        payout=float(row.get("payout", 0)),
                        pnl=float(row.get("pnl", 0)),
                        closed_at=row.get("closed_at", ""),
                    )
                    self.positions[pos.token_id] = pos
            log.info("Loaded %d positions from %s", len(self.positions), self.positions_file)
        except Exception as e:
            log.warning("Failed to load positions: %s", e)

    def save(self):
        """Save all positions to CSV."""
        if not self.positions:
            return
        fieldnames = list(Position.__dataclass_fields__.keys())
        with open(self.positions_file, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for pos in self.positions.values():
                writer.writerow(asdict(pos))
        log.info("Saved %d positions to %s", len(self.positions), self.positions_file)

    def has_position(self, token_id: str) -> bool:
        """Check if we already have an open position for this token."""
        pos = self.positions.get(token_id)
        return pos is not None and pos.status == "open"

    def get_position_cost(self, token_id: str) -> float:
        """Return total USDC cost for a position, or 0 if not held."""
        pos = self.positions.get(token_id)
        if pos is not None and pos.status == "open":
            return float(pos.cost_usdc or 0)
        return 0.0

    def open_position(self, token_id: str, slug: str, outcome: str,
                      sport: str, market_type: str, entry_price: float,
                      fair_prob: float, edge_pct: float, shares: float,
                      cost_usdc: float, bookmaker: str):
        """Record a new position or scale into an existing one.

        When scaling, accumulates shares and cost, and computes a
        volume-weighted average entry price.
        """
        existing = self.positions.get(token_id)
        if existing and existing.status == "open":
            # Scale into existing position — accumulate shares/cost, VWAP entry
            old_shares = existing.shares
            old_cost = existing.cost_usdc
            new_shares = old_shares + shares
            new_cost = old_cost + cost_usdc
            existing.entry_price = new_cost / new_shares if new_shares > 0 else entry_price
            existing.shares = new_shares
            existing.cost_usdc = new_cost
            existing.fair_prob = fair_prob  # Update to latest fair value
            existing.edge_pct = edge_pct
            self._append_trade("SCALE", existing)
            self.save()
            log.info("Scaled: %s [%s] +%.0f shares @ %.3f (+$%.0f) -> total %.0f shares $%.0f",
                     slug, outcome, shares, entry_price, cost_usdc,
                     new_shares, new_cost)
            return

        now = datetime.now(timezone.utc).isoformat()
        pos = Position(
            token_id=token_id,
            slug=slug,
            outcome=outcome,
            sport=sport,
            market_type=market_type,
            entry_price=entry_price,
            fair_prob=fair_prob,
            edge_pct=edge_pct,
            shares=shares,
            cost_usdc=cost_usdc,
            bookmaker=bookmaker,
            opened_at=now,
        )
        self.positions[token_id] = pos
        self._append_trade("OPEN", pos)
        self.save()
        log.info("Opened: %s [%s] %.0f shares @ %.3f ($%.0f)",
                 slug, outcome, shares, entry_price, cost_usdc)

    def check_resolutions(self, poly_client) -> list[dict]:
        """Check open positions for resolution via Polymarket Gamma API.

        Looks up each position's market by slug, checks if resolved,
        then matches our outcome against the market's outcomes/prices
        to determine win/loss.
        """
        resolved = []
        open_positions = [p for p in self.positions.values() if p.status == "open"]

        if not open_positions:
            return resolved

        # Deduplicate slug lookups (multiple positions can share a slug)
        slug_cache: dict[str, Optional[dict]] = {}

        for pos in open_positions:
            try:
                slug = pos.slug
                if slug not in slug_cache:
                    slug_cache[slug] = poly_client.get_market_by_slug(slug)
                market = slug_cache[slug]
                if not market:
                    continue

                # Check if market is resolved (Gamma uses umaResolutionStatus or closed+outcomePrices)
                uma_status = market.get("umaResolutionStatus", "")
                if uma_status != "resolved":
                    continue

                # Parse outcomes and prices
                outcomes_raw = market.get("outcomes", "[]")
                prices_raw = market.get("outcomePrices", "[]")
                if isinstance(outcomes_raw, str):
                    outcomes_raw = json.loads(outcomes_raw)
                if isinstance(prices_raw, str):
                    prices_raw = json.loads(prices_raw)

                # Match our position's outcome to the market's outcome list
                # to find the resolution price for our specific side
                our_price = None
                for i, outcome_name in enumerate(outcomes_raw):
                    if i < len(prices_raw) and outcome_name == pos.outcome:
                        our_price = float(prices_raw[i])
                        break

                if our_price is None:
                    # Fallback: match by token_id against clobTokenIds
                    token_ids_raw = market.get("clobTokenIds", "[]")
                    if isinstance(token_ids_raw, str):
                        token_ids_raw = json.loads(token_ids_raw)
                    for i, tid in enumerate(token_ids_raw):
                        if tid == pos.token_id and i < len(prices_raw):
                            our_price = float(prices_raw[i])
                            break

                if our_price is None:
                    log.warning("Could not match outcome '%s' in resolved market %s "
                                "(outcomes=%s)", pos.outcome, slug, outcomes_raw)
                    continue

                won = our_price > 0.5

                if won:
                    payout = pos.shares * 1.0  # $1 per share
                else:
                    payout = 0.0

                pnl = payout - pos.cost_usdc

                # Update position
                pos.status = "won" if won else "lost"
                pos.resolution_price = our_price
                pos.payout = payout
                pos.pnl = pnl
                pos.closed_at = datetime.now(timezone.utc).isoformat()

                self._append_trade("RESOLVE", pos)

                resolved.append({
                    "token_id": pos.token_id,
                    "slug": pos.slug,
                    "outcome": pos.outcome,
                    "won": won,
                    "entry_price": pos.entry_price,
                    "shares": pos.shares,
                    "payout": payout,
                    "pnl": pnl,
                    "resolution_price": our_price,
                })

            except Exception as e:
                log.debug("Resolution check failed for %s: %s", pos.slug, e)

        if resolved:
            self.save()

        return resolved

    def check_early_exits(self, poly_client,
                          win_threshold: float = 0.990,
                          loss_threshold: float = 0.002) -> list[dict]:
        """Check if any open positions can be exited early based on price.

        When price is near 1.0 (winner) or near 0.0 (loser), the outcome
        is essentially certain but UMA resolution takes 2-3h. Selling now
        frees capital immediately at ~99% of final value.

        Args:
            poly_client: PolymarketClient instance
            win_threshold: Sell if current price >= this (default 0.990)
            loss_threshold: Sell if current price <= this (default 0.002)

        Returns:
            List of early exit dicts with position details and exit price.
        """
        exits = []
        open_positions = [p for p in self.positions.values() if p.status == "open"]

        if not open_positions:
            return exits

        for pos in open_positions:
            try:
                mid = poly_client.get_midpoint_unauthenticated(pos.token_id)
                if mid is None:
                    continue

                exit_type = None
                if mid >= win_threshold:
                    exit_type = "early_win"
                elif mid <= loss_threshold:
                    exit_type = "early_loss"

                if not exit_type:
                    continue

                # Calculate exit P&L
                exit_price = mid
                payout = pos.shares * exit_price
                pnl = payout - pos.cost_usdc

                exits.append({
                    "token_id": pos.token_id,
                    "slug": pos.slug,
                    "outcome": pos.outcome,
                    "sport": pos.sport,
                    "exit_type": exit_type,
                    "entry_price": pos.entry_price,
                    "exit_price": exit_price,
                    "shares": pos.shares,
                    "cost_usdc": pos.cost_usdc,
                    "payout": payout,
                    "pnl": pnl,
                })

            except Exception as e:
                log.debug("Early exit check failed for %s: %s", pos.slug, e)

        return exits

    def close_early_exit(self, token_id: str, exit_price: float, payout: float):
        """Mark a position as early-exited."""
        pos = self.positions.get(token_id)
        if not pos or pos.status != "open":
            return

        pos.status = "won" if payout > pos.cost_usdc else "lost"
        pos.resolution_price = exit_price
        pos.payout = payout
        pos.pnl = payout - pos.cost_usdc
        pos.closed_at = datetime.now(timezone.utc).isoformat()

        self._append_trade("EARLY_EXIT", pos)
        self.save()

    def _append_trade(self, trade_type: str, pos: Position):
        """Append a trade record to the trades CSV."""
        now = datetime.now(timezone.utc).isoformat()
        row = {
            "timestamp": now,
            "type": trade_type,
            "token_id": pos.token_id,
            "slug": pos.slug,
            "outcome": pos.outcome,
            "sport": pos.sport,
            "market_type": pos.market_type,
            "entry_price": pos.entry_price,
            "fair_prob": pos.fair_prob,
            "edge_pct": pos.edge_pct,
            "shares": pos.shares,
            "cost_usdc": pos.cost_usdc,
            "pnl": pos.pnl,
            "status": pos.status,
        }
        file_exists = os.path.exists(self.trades_file)
        with open(self.trades_file, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if not file_exists:
                writer.writeheader()
            writer.writerow(row)

    def summary(self) -> dict:
        """Return portfolio summary."""
        open_pos = [p for p in self.positions.values() if p.status == "open"]
        won = [p for p in self.positions.values() if p.status == "won"]
        lost = [p for p in self.positions.values() if p.status == "lost"]

        total_exposure = sum(p.cost_usdc for p in open_pos)
        realized_pnl = sum(p.pnl for p in won) + sum(p.pnl for p in lost)
        unrealized_value = sum(p.shares * p.entry_price for p in open_pos)  # at cost

        return {
            "open_count": len(open_pos),
            "total_wins": len(won),
            "total_losses": len(lost),
            "win_rate": len(won) / max(1, len(won) + len(lost)) * 100,
            "total_exposure": total_exposure,
            "realized_pnl": realized_pnl,
            "unrealized_value": unrealized_value,
            "total_positions": len(self.positions),
        }

    def print_report(self):
        """Print detailed portfolio report."""
        s = self.summary()
        open_pos = [p for p in self.positions.values() if p.status == "open"]

        print(f"\n{'='*70}")
        print("PORTFOLIO REPORT")
        print(f"{'='*70}")
        print(f"  Open positions:   {s['open_count']}")
        print(f"  Total exposure:   ${s['total_exposure']:,.2f}")
        print(f"  Wins / Losses:    {s['total_wins']} / {s['total_losses']}")
        print(f"  Win rate:         {s['win_rate']:.1f}%")
        print(f"  Realized PnL:     ${s['realized_pnl']:,.2f}")

        if open_pos:
            print(f"\n  Open Positions:")
            print(f"  {'Slug':<40} {'Outcome':>10} {'Type':>7} {'Entry':>6} "
                  f"{'Fair':>6} {'Edge':>6} {'Shares':>8} {'Cost':>8}")
            print(f"  {'-'*40} {'-'*10} {'-'*7} {'-'*6} {'-'*6} {'-'*6} {'-'*8} {'-'*8}")
            for p in sorted(open_pos, key=lambda x: -x.cost_usdc):
                print(f"  {p.slug:<40} {p.outcome:>10} {p.market_type:>7} "
                      f"{p.entry_price:>6.3f} {p.fair_prob:>6.3f} "
                      f"{p.edge_pct:>5.1f}% {p.shares:>8.0f} ${p.cost_usdc:>7.0f}")
