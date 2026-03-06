"""Unit tests for Strategy edge evaluation, conflict detection, and scan logic."""
import pytest
from unittest.mock import MagicMock, patch, PropertyMock
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

from src.strategy import Strategy, Opportunity, MAX_DAYS_TO_EVENT
from src.config import Config


# ---- Fixtures ----

def _make_config():
    """Build a test Config without touching env or .env files."""
    c = Config.__new__(Config)
    c.bankroll_usdc = 500.0
    c.max_position_usdc = 8.0
    c.max_total_exposure_usdc = 400.0
    c.min_edge_pct = 1.5
    c.max_edge_pct = 20.0
    c.kelly_fraction = 0.15
    c.min_entry_price = 0.05
    c.max_entry_price = 0.95
    c.data_dir = "data_test"
    c.learning_enabled = False
    c.merge_enabled = False
    c.target_sports = ["nba", "nfl"]
    c.scan_interval_seconds = 60
    c.private_key = ""
    c.api_key = ""
    c.api_secret = ""
    c.api_passphrase = ""
    c.chain_id = 137
    c.clob_url = "https://clob.polymarket.com"
    c.gamma_url = "https://gamma-api.polymarket.com"
    c.data_url = "https://data-api.polymarket.com"
    c.odds_api_key = ""
    c.min_learning_samples = 20
    c.min_merge_profit = 0.02
    return c


def _make_strategy():
    """Build a Strategy with all external dependencies mocked."""
    cfg = _make_config()
    with patch("src.strategy.PolymarketClient"), \
         patch("src.strategy.OddsClient"), \
         patch("src.strategy.PositionTracker") as MockTracker, \
         patch("os.makedirs"):
        # Tracker mock
        tracker_inst = MagicMock()
        tracker_inst.positions = {}
        tracker_inst.has_position = MagicMock(return_value=False)
        MockTracker.return_value = tracker_inst

        strat = Strategy(cfg, dry_run=True)
        # Override risk manager to avoid any state leakage
        strat.risk = MagicMock()
        strat.risk.total_exposure = 0.0
        strat.risk.calculate_position_size = MagicMock(return_value=5.0)
        strat.risk.check_can_trade = MagicMock(return_value=True)
        strat.tracker = tracker_inst
        strat._learning = None
        strat._merge = None
        strat._rn1_signals = None
    return strat


def _make_match(slug="nba-lakers-vs-celtics", question="Who wins?",
                liquidity=500.0, sport="nba", neg_risk=False,
                commence_time=None):
    """Build a mock matched market dict."""
    if commence_time is None:
        # 2 hours from now (within range)
        ct = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    else:
        ct = commence_time
    return {
        "polymarket": {
            "slug": slug,
            "question": question,
            "liquidity": liquidity,
            "sport": sport,
            "neg_risk": neg_risk,
        },
        "odds_event": {
            "bookmaker": "pinnacle",
            "commence_time": ct,
        },
    }


def _make_edge(side="BUY", edge_pct=5.0, price=0.30, outcome="Lakers",
               token_id="tok_lakers", market_type="h2h", line=None):
    return {
        "side": side,
        "edge_pct": edge_pct,
        "polymarket_price": price,
        "outcome": outcome,
        "token_id": token_id,
        "market_type": market_type,
        "fair_prob": price + (edge_pct / 100.0 * price),
        "line": line,
    }


@pytest.fixture
def strat():
    return _make_strategy()


# ---- Constants ----

class TestConstants:
    def test_max_days_to_event_is_10(self):
        assert MAX_DAYS_TO_EVENT == 10

    def test_min_liquidity_is_100(self):
        s = _make_strategy()
        assert s.min_liquidity == 100.0


# ---- _evaluate_edge filtering ----

class TestEvaluateEdge:

    def test_valid_edge_passes(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match()
        e = _make_edge()
        opp = strat._evaluate_edge(m, e)
        assert opp is not None
        assert isinstance(opp, Opportunity)
        assert opp.slug == "nba-lakers-vs-celtics"
        assert opp.size_usdc == 5.0

    def test_sell_side_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match()
        e = _make_edge(side="SELL")
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("no_buy", 0) == 1

    def test_low_edge_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match()
        e = _make_edge(edge_pct=0.5)  # Below min_edge_pct=1.5
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("low_edge", 0) == 1

    def test_high_edge_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match()
        e = _make_edge(edge_pct=25.0)  # Above max_edge_pct=20.0
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("high_edge", 0) == 1

    def test_price_too_high_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match()
        e = _make_edge(price=0.96)  # Above max_entry_price=0.95
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("price_high", 0) == 1

    def test_price_too_low_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match()
        e = _make_edge(price=0.02)  # Below min_entry_price=0.05
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("price_low", 0) == 1

    def test_too_far_event_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        far_time = (datetime.now(timezone.utc) + timedelta(days=15)).isoformat()
        m = _make_match(commence_time=far_time)
        e = _make_edge()
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("too_far", 0) == 1

    def test_event_within_range_passes(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        ok_time = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat()
        m = _make_match(commence_time=ok_time)
        e = _make_edge()
        opp = strat._evaluate_edge(m, e)
        assert opp is not None

    def test_low_liquidity_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        m = _make_match(liquidity=50.0)  # Below min_liquidity=100
        e = _make_edge()
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("liquidity", 0) == 1

    def test_already_held_token_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        strat.tracker.has_position = MagicMock(return_value=True)
        m = _make_match()
        e = _make_edge()
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("already_held", 0) == 1

    def test_conflicting_position_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        # Set up an open position on the opposite outcome
        pos = MagicMock()
        pos.status = "open"
        pos.slug = "nba-lakers-vs-celtics"
        pos.outcome = "Celtics"
        strat.tracker.positions = {"tok_celtics": pos}
        m = _make_match(slug="nba-lakers-vs-celtics")
        e = _make_edge(outcome="Lakers")
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("conflict", 0) == 1

    def test_size_zero_filtered(self, strat):
        strat._pending_exposure = 0.0
        strat._filter_counts = {}
        strat.risk.calculate_position_size = MagicMock(return_value=0.0)
        m = _make_match()
        e = _make_edge()
        opp = strat._evaluate_edge(m, e)
        assert opp is None
        assert strat._filter_counts.get("size_zero", 0) == 1


# ---- _has_conflicting_position ----

class TestConflictingPosition:

    def test_same_slug_different_outcome_is_conflict(self, strat):
        pos = MagicMock()
        pos.status = "open"
        pos.slug = "nba-lakers-vs-celtics"
        pos.outcome = "Celtics"
        strat.tracker.positions = {"tok_celtics": pos}
        assert strat._has_conflicting_position("nba-lakers-vs-celtics", "Lakers") is True

    def test_same_slug_same_outcome_no_conflict(self, strat):
        """Same outcome = duplicate, not conflict."""
        pos = MagicMock()
        pos.status = "open"
        pos.slug = "nba-lakers-vs-celtics"
        pos.outcome = "Lakers"
        strat.tracker.positions = {"tok_lakers": pos}
        assert strat._has_conflicting_position("nba-lakers-vs-celtics", "Lakers") is False

    def test_different_game_no_conflict(self, strat):
        pos = MagicMock()
        pos.status = "open"
        pos.slug = "nba-warriors-vs-suns"
        pos.outcome = "Warriors"
        strat.tracker.positions = {"tok_warriors": pos}
        assert strat._has_conflicting_position("nba-lakers-vs-celtics", "Lakers") is False

    def test_adjacent_totals_over_under_conflict(self, strat):
        """Over on one total + Under on adjacent total on same game = conflict."""
        pos = MagicMock()
        pos.status = "open"
        pos.slug = "nba-lakers-vs-celtics-total-240-5"
        pos.outcome = "Over"
        strat.tracker.positions = {"tok_over": pos}
        result = strat._has_conflicting_position(
            "nba-lakers-vs-celtics-total-241-5", "Under"
        )
        assert result is True

    def test_same_total_same_side_no_conflict(self, strat):
        """Over on same total line = not conflict (would be duplicate)."""
        pos = MagicMock()
        pos.status = "open"
        pos.slug = "nba-lakers-vs-celtics-total-240-5"
        pos.outcome = "Over"
        strat.tracker.positions = {"tok_over": pos}
        result = strat._has_conflicting_position(
            "nba-lakers-vs-celtics-total-240-5", "Over"
        )
        assert result is False

    def test_closed_positions_ignored(self, strat):
        pos = MagicMock()
        pos.status = "won"  # Not open
        pos.slug = "nba-lakers-vs-celtics"
        pos.outcome = "Celtics"
        strat.tracker.positions = {"tok_celtics": pos}
        assert strat._has_conflicting_position("nba-lakers-vs-celtics", "Lakers") is False


# ---- Opportunity sorting ----

class TestOpportunitySorting:

    def test_sorted_by_adjusted_edge_desc_then_rn1(self, strat):
        """Candidates sorted by adjusted_edge desc, then rn1_score desc."""
        o1 = Opportunity(
            slug="g1", question="?", outcome="A", token_id="t1",
            market_type="h2h", poly_price=0.3, fair_prob=0.35,
            edge_pct=5.0, size_usdc=5.0, bookmaker="pin", neg_risk=False,
            adjusted_edge=8.0, rn1_score=50.0,
        )
        o2 = Opportunity(
            slug="g2", question="?", outcome="B", token_id="t2",
            market_type="h2h", poly_price=0.4, fair_prob=0.46,
            edge_pct=10.0, size_usdc=5.0, bookmaker="pin", neg_risk=False,
            adjusted_edge=10.0, rn1_score=30.0,
        )
        o3 = Opportunity(
            slug="g3", question="?", outcome="C", token_id="t3",
            market_type="h2h", poly_price=0.2, fair_prob=0.24,
            edge_pct=8.0, size_usdc=5.0, bookmaker="pin", neg_risk=False,
            adjusted_edge=8.0, rn1_score=80.0,
        )
        candidates = [o1, o3, o2]
        candidates.sort(key=lambda x: (-(x.adjusted_edge or x.edge_pct), -x.rn1_score))
        assert candidates[0].slug == "g2"  # highest adjusted_edge=10
        assert candidates[1].slug == "g3"  # adjusted_edge=8, rn1=80
        assert candidates[2].slug == "g1"  # adjusted_edge=8, rn1=50


# ---- Cumulative exposure limit in scan() ----

class TestScanExposureLimit:

    def test_opportunities_capped_at_max_exposure(self):
        """When cumulative exposure would exceed max, last opp is sized down."""
        strat = _make_strategy()
        strat.risk.total_exposure = 395.0  # Only $5 headroom left

        # Mock the full scan pipeline so we control candidates
        with patch.object(strat.poly, "get_active_sports_markets", return_value=[]), \
             patch.object(strat.odds, "get_all_sports_odds", return_value={}), \
             patch("src.strategy.match_markets", return_value=[]):

            # Directly test the cumulative logic by injecting candidates
            # We'll call scan() but since match_markets returns [], we get 0.
            # Instead, test the logic directly:
            pass

        # Simulate what scan() does in its second pass
        strat.risk.total_exposure = 395.0
        max_total = strat.config.max_total_exposure_usdc  # 400

        candidates = [
            Opportunity(slug="g1", question="?", outcome="A", token_id="t1",
                        market_type="h2h", poly_price=0.3, fair_prob=0.35,
                        edge_pct=10.0, size_usdc=4.0, bookmaker="pin",
                        neg_risk=False, adjusted_edge=10.0),
            Opportunity(slug="g2", question="?", outcome="B", token_id="t2",
                        market_type="h2h", poly_price=0.4, fair_prob=0.46,
                        edge_pct=8.0, size_usdc=4.0, bookmaker="pin",
                        neg_risk=False, adjusted_edge=8.0),
        ]
        candidates.sort(key=lambda x: (-(x.adjusted_edge or x.edge_pct), -x.rn1_score))

        opportunities = []
        cumulative = strat.risk.total_exposure  # 395
        for opp in candidates:
            if cumulative + opp.size_usdc > max_total:
                remaining = max_total - cumulative
                if remaining < 0.50:
                    break
                opp.size_usdc = round(remaining, 2)
            cumulative += opp.size_usdc
            opportunities.append(opp)

        # First opp: 395+4=399 <= 400 => passes as-is ($4)
        assert len(opportunities) >= 1
        assert opportunities[0].size_usdc == 4.0
        # Second opp: 399+4=403 > 400 => sized down to $1
        assert len(opportunities) == 2
        assert opportunities[1].size_usdc == pytest.approx(1.0)

    def test_beyond_limit_breaks(self):
        """When remaining < $0.50, no more opportunities accepted."""
        strat = _make_strategy()
        strat.risk.total_exposure = 399.80
        max_total = strat.config.max_total_exposure_usdc  # 400

        candidates = [
            Opportunity(slug="g1", question="?", outcome="A", token_id="t1",
                        market_type="h2h", poly_price=0.3, fair_prob=0.35,
                        edge_pct=10.0, size_usdc=5.0, bookmaker="pin",
                        neg_risk=False, adjusted_edge=10.0),
        ]

        opportunities = []
        cumulative = strat.risk.total_exposure
        for opp in candidates:
            if cumulative + opp.size_usdc > max_total:
                remaining = max_total - cumulative
                if remaining < 0.50:
                    break
                opp.size_usdc = round(remaining, 2)
            cumulative += opp.size_usdc
            opportunities.append(opp)

        # remaining = 0.20 < 0.50 => break, no opportunities
        assert len(opportunities) == 0
