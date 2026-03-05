"""Position sizing and risk management."""
import logging
from dataclasses import dataclass, field

from .config import Config

log = logging.getLogger(__name__)


@dataclass
class Position:
    token_id: str
    outcome: str
    market_slug: str
    side: str
    size: float  # shares
    avg_price: float
    cost_basis: float  # USDC spent


class RiskManager:
    """Manage exposure limits and position sizing."""

    def __init__(self, config: Config):
        self.config = config
        self.positions: dict[str, Position] = {}
        self.total_exposure: float = 0.0
        self.realized_pnl: float = 0.0
        self.trade_count: int = 0

    def check_can_trade(self, usdc_amount: float) -> bool:
        """Check if a new trade is within risk limits."""
        if self.total_exposure + usdc_amount > self.config.max_total_exposure_usdc:
            log.warning("Trade rejected: would exceed max exposure ($%.0f + $%.0f > $%.0f)",
                        self.total_exposure, usdc_amount, self.config.max_total_exposure_usdc)
            return False
        if usdc_amount > self.config.max_position_usdc:
            log.warning("Trade rejected: position size $%.0f > max $%.0f",
                        usdc_amount, self.config.max_position_usdc)
            return False
        return True

    def calculate_position_size(self, edge_pct: float, price: float, kelly_fraction: float = 0.25) -> float:
        """Calculate position size using fractional Kelly criterion.

        Args:
            edge_pct: Expected edge as percentage (e.g. 5.0 = 5%)
            price: Entry price (probability)
            kelly_fraction: Fraction of full Kelly to use (default 0.25 = quarter Kelly)

        Returns:
            USDC amount to risk
        """
        if edge_pct <= 0 or price <= 0 or price >= 1:
            return 0.0

        # Kelly: f* = (bp - q) / b
        # where b = (1/price - 1), p = fair_prob, q = 1 - fair_prob
        fair_prob = price + (edge_pct / 100.0 * price)
        b = (1.0 / price) - 1.0
        q = 1.0 - fair_prob

        if b <= 0:
            return 0.0

        kelly_full = (b * fair_prob - q) / b
        if kelly_full <= 0:
            return 0.0

        kelly_size = kelly_full * kelly_fraction

        # Cap at max position size
        usdc_size = min(
            kelly_size * self.config.max_total_exposure_usdc,
            self.config.max_position_usdc,
        )

        # Floor at $5 minimum to be worth the gas
        if usdc_size < 5.0:
            return 0.0

        return round(usdc_size, 2)

    def record_trade(self, token_id: str, outcome: str, slug: str,
                     side: str, size: float, price: float, usdc: float):
        """Record a new trade."""
        self.positions[token_id] = Position(
            token_id=token_id,
            outcome=outcome,
            market_slug=slug,
            side=side,
            size=size,
            avg_price=price,
            cost_basis=usdc,
        )
        self.total_exposure += usdc
        self.trade_count += 1
        log.info("Recorded: %s %s %.0f shares @ %.4f ($%.2f) on %s [%s]",
                 side, outcome, size, price, usdc, slug, token_id[:16])

    def record_resolution(self, token_id: str, payout: float):
        """Record a position resolution."""
        pos = self.positions.pop(token_id, None)
        if pos:
            pnl = payout - pos.cost_basis
            self.realized_pnl += pnl
            self.total_exposure -= pos.cost_basis
            log.info("Resolved: %s %s -> PnL $%.2f (payout $%.2f, cost $%.2f)",
                     pos.market_slug, pos.outcome, pnl, payout, pos.cost_basis)

    def summary(self) -> dict:
        """Return current risk summary."""
        return {
            "total_exposure": self.total_exposure,
            "open_positions": len(self.positions),
            "realized_pnl": self.realized_pnl,
            "trade_count": self.trade_count,
            "headroom": self.config.max_total_exposure_usdc - self.total_exposure,
        }
