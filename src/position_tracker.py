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
    status: str = "open"       # open, won, lost
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

    def open_position(self, token_id: str, slug: str, outcome: str,
                      sport: str, market_type: str, entry_price: float,
                      fair_prob: float, edge_pct: float, shares: float,
                      cost_usdc: float, bookmaker: str):
        """Record a new position."""
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
        """Check open positions for resolution via Polymarket API.

        For resolved markets, curPrice will be 0 (losing) or ~1 (winning).
        Redeemable=True means the market has settled.
        """
        resolved = []
        open_positions = [p for p in self.positions.values() if p.status == "open"]

        if not open_positions:
            return resolved

        for pos in open_positions:
            try:
                market = poly_client.get_market_by_condition(
                    pos.token_id.split("_")[0] if "_" in pos.token_id else ""
                )
                if not market:
                    continue

                # Check if market is resolved
                if not market.get("resolved", False):
                    continue

                # Determine resolution
                cur_price = float(market.get("outcomePrices", "[0,0]").strip("[]").split(",")[0])
                won = cur_price > 0.5

                if won:
                    payout = pos.shares * 1.0  # $1 per share
                else:
                    payout = 0.0

                pnl = payout - pos.cost_usdc

                # Update position
                pos.status = "won" if won else "lost"
                pos.resolution_price = cur_price
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
                })

            except Exception as e:
                log.debug("Resolution check failed for %s: %s", pos.slug, e)

        if resolved:
            self.save()

        return resolved

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
