"""Unit tests for RiskManager position sizing and exposure management."""
import pytest
from unittest.mock import MagicMock
from src.config import Config
from src.risk_manager import RiskManager, Position


@pytest.fixture
def cfg():
    """Test config with known values."""
    c = Config.__new__(Config)
    c.bankroll_usdc = 500.0
    c.max_position_usdc = 8.0
    c.max_total_exposure_usdc = 400.0
    c.min_edge_pct = 1.5
    c.max_edge_pct = 20.0
    c.kelly_fraction = 0.15
    c.min_entry_price = 0.05
    c.max_entry_price = 0.95
    return c


@pytest.fixture
def rm(cfg):
    return RiskManager(cfg)


# ---- Position Sizing (Kelly) ----

class TestPositionSizing:

    def test_reasonable_size_for_moderate_edge(self, rm):
        """5% edge at price=0.3 should give a reasonable $1-$8 size."""
        size = rm.calculate_position_size(edge_pct=5.0, price=0.3)
        assert 0.50 <= size <= 8.0, f"Expected $0.50-$8.00, got ${size}"

    def test_small_edge_below_floor(self, rm):
        """0.5% edge at price=0.5 should return $0 (below $0.50 floor)."""
        size = rm.calculate_position_size(edge_pct=0.5, price=0.5)
        assert size == 0.0

    def test_huge_edge_capped_at_max(self, rm):
        """50% edge at price=0.1 with large kelly should be capped at max_position_usdc ($8)."""
        # Use full Kelly (fraction=1.0) to guarantee kelly_size * bankroll >> max_position
        size = rm.calculate_position_size(edge_pct=50, price=0.1, kelly_fraction=1.0)
        assert size == rm.config.max_position_usdc

    def test_zero_edge_returns_zero(self, rm):
        size = rm.calculate_position_size(edge_pct=0.0, price=0.3)
        assert size == 0.0

    def test_negative_edge_returns_zero(self, rm):
        size = rm.calculate_position_size(edge_pct=-5.0, price=0.3)
        assert size == 0.0

    def test_price_boundary_low(self, rm):
        """price=0.01 with a real edge should not crash."""
        size = rm.calculate_position_size(edge_pct=10.0, price=0.01)
        assert isinstance(size, float)
        assert size >= 0.0

    def test_price_boundary_high(self, rm):
        """price=0.99 should handle gracefully (b is small)."""
        size = rm.calculate_position_size(edge_pct=5.0, price=0.99)
        assert isinstance(size, float)
        assert size >= 0.0

    def test_price_at_zero_returns_zero(self, rm):
        size = rm.calculate_position_size(edge_pct=5.0, price=0.0)
        assert size == 0.0

    def test_price_at_one_returns_zero(self, rm):
        size = rm.calculate_position_size(edge_pct=5.0, price=1.0)
        assert size == 0.0

    def test_kelly_fraction_smaller_gives_smaller_size(self, rm):
        """0.05 Kelly should produce smaller size than 0.25."""
        size_small = rm.calculate_position_size(edge_pct=10.0, price=0.3, kelly_fraction=0.05)
        size_large = rm.calculate_position_size(edge_pct=10.0, price=0.3, kelly_fraction=0.25)
        # Both should be positive
        assert size_small > 0 or size_large > 0
        # Smaller fraction => smaller or equal size (capping could equalize)
        assert size_small <= size_large

    def test_realized_pnl_affects_bankroll(self, rm):
        """Positive realized PnL should increase effective bankroll and thus sizes."""
        size_before = rm.calculate_position_size(edge_pct=5.0, price=0.3)
        rm.realized_pnl = 200.0  # +$200 profit
        size_after = rm.calculate_position_size(edge_pct=5.0, price=0.3)
        # After profit, effective bankroll is larger, so size should be >= before
        assert size_after >= size_before

    def test_size_is_rounded_to_two_decimals(self, rm):
        size = rm.calculate_position_size(edge_pct=8.0, price=0.25)
        if size > 0:
            assert size == round(size, 2)


# ---- Exposure Limits ----

class TestExposureLimits:

    def test_can_trade_under_limit(self, rm):
        rm.total_exposure = 100.0
        assert rm.check_can_trade(5.0) is True  # $5 <= max_position_usdc=$8

    def test_cannot_trade_would_exceed_total(self, rm):
        rm.total_exposure = 395.0
        assert rm.check_can_trade(10.0) is False

    def test_cannot_trade_exceeds_max_position(self, rm):
        rm.total_exposure = 0.0
        assert rm.check_can_trade(10.0) is False  # max_position_usdc=8

    def test_exactly_at_limit(self, rm):
        rm.total_exposure = 392.0
        assert rm.check_can_trade(8.0) is True

    def test_one_cent_over_limit(self, rm):
        rm.total_exposure = 392.01
        assert rm.check_can_trade(8.0) is False


# ---- State Tracking ----

class TestStateTracking:

    def test_record_trade_updates_exposure(self, rm):
        rm.record_trade("tok1", "Yes", "game-slug", "BUY", 100.0, 0.30, 30.0)
        assert rm.total_exposure == 30.0
        assert rm.trade_count == 1
        assert "tok1" in rm.positions

    def test_record_multiple_trades(self, rm):
        rm.record_trade("tok1", "Yes", "game1", "BUY", 50, 0.3, 15.0)
        rm.record_trade("tok2", "No", "game2", "BUY", 30, 0.5, 15.0)
        assert rm.total_exposure == 30.0
        assert rm.trade_count == 2

    def test_record_resolution_adjusts_exposure_and_pnl(self, rm):
        rm.record_trade("tok1", "Yes", "game1", "BUY", 100, 0.30, 30.0)
        # Won: payout = shares * 1.0 = 100.0
        rm.record_resolution("tok1", payout=100.0)
        assert rm.total_exposure == 0.0
        assert rm.realized_pnl == pytest.approx(70.0)  # 100 - 30
        assert "tok1" not in rm.positions

    def test_record_resolution_loss(self, rm):
        rm.record_trade("tok1", "Yes", "game1", "BUY", 100, 0.30, 30.0)
        # Lost: payout = 0
        rm.record_resolution("tok1", payout=0.0)
        assert rm.total_exposure == 0.0
        assert rm.realized_pnl == pytest.approx(-30.0)

    def test_record_resolution_unknown_token_with_cost_basis(self, rm):
        """Resolution for token not tracked in risk manager (e.g. after restart)."""
        rm.total_exposure = 50.0
        rm.record_resolution("unknown_tok", payout=20.0, cost_basis=10.0)
        assert rm.total_exposure == 40.0
        assert rm.realized_pnl == pytest.approx(10.0)

    def test_record_resolution_unknown_token_no_cost(self, rm):
        """Unknown token, no cost_basis => warning, no crash."""
        initial_pnl = rm.realized_pnl
        rm.record_resolution("mystery", payout=0.0)
        assert rm.realized_pnl == initial_pnl  # No change

    def test_sync_from_tracker(self, rm):
        """sync_from_tracker should sum cost_usdc of open positions."""
        tracker = MagicMock()
        p1 = MagicMock()
        p1.status = "open"
        p1.cost_usdc = 25.0
        p2 = MagicMock()
        p2.status = "open"
        p2.cost_usdc = 15.0
        p3 = MagicMock()
        p3.status = "won"
        p3.cost_usdc = 100.0  # Should be excluded
        tracker.positions = {"a": p1, "b": p2, "c": p3}
        rm.sync_from_tracker(tracker)
        assert rm.total_exposure == pytest.approx(40.0)

    def test_exposure_never_goes_negative(self, rm):
        """Even if resolution reduces exposure below 0, it clamps to 0."""
        rm.total_exposure = 5.0
        rm.record_trade("tok1", "Yes", "g", "BUY", 10, 0.5, 5.0)
        # Now exposure=10, but record_resolution uses pos.cost_basis=5
        rm.record_resolution("tok1", payout=0.0)
        assert rm.total_exposure >= 0.0


# ---- Summary ----

class TestSummary:

    def test_summary_headroom(self, rm):
        rm.total_exposure = 100.0
        rm.realized_pnl = 50.0
        rm.trade_count = 3
        s = rm.summary()
        assert s["headroom"] == pytest.approx(300.0)
        assert s["total_exposure"] == pytest.approx(100.0)
        assert s["bankroll"] == pytest.approx(550.0)
        assert s["trade_count"] == 3

    def test_summary_empty(self, rm):
        s = rm.summary()
        assert s["headroom"] == pytest.approx(400.0)
        assert s["open_positions"] == 0
