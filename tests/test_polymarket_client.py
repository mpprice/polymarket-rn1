"""Unit tests for src.polymarket_client.PolymarketClient."""
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.config import Config
from src.polymarket_client import PolymarketClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or []
    resp.raise_for_status = MagicMock()
    return resp


def _make_client(dry_run=True) -> PolymarketClient:
    cfg = Config()
    cfg.target_sports = ["epl", "nba"]
    cfg.gamma_url = "https://gamma-api.polymarket.com"
    return PolymarketClient(cfg, dry_run=dry_run)


# ---------------------------------------------------------------------------
# _parse_market
# ---------------------------------------------------------------------------

class TestParseMarket:
    """Test market data normalization."""

    def test_string_outcomes_parsed(self):
        client = _make_client()
        m = {
            "conditionId": "0xabc123",
            "question": "Will Arsenal win?",
            "slug": "epl-ars-che",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.65","0.35"]',
            "clobTokenIds": '["token1","token2"]',
            "volume24hr": "5000",
            "liquidity": "12000",
            "endDate": "2026-03-08T17:00:00Z",
            "negRisk": False,
            "active": True,
        }
        result = client._parse_market(m, "epl")
        assert result["outcomes"] == ["Yes", "No"]
        assert result["prices"] == [0.65, 0.35]
        assert result["token_ids"] == ["token1", "token2"]

    def test_list_outcomes_handled(self):
        client = _make_client()
        m = {
            "conditionId": "0xdef456",
            "question": "Will Lakers win?",
            "slug": "nba-lal-bos",
            "outcomes": ["Yes", "No"],
            "outcomePrices": [0.48, 0.52],
            "clobTokenIds": ["tk_a", "tk_b"],
            "volume24hr": 3000,
            "liquidity": 8000,
            "endDate": "2026-03-05T22:00:00Z",
            "negRisk": True,
            "active": True,
        }
        result = client._parse_market(m, "nba")
        assert result["outcomes"] == ["Yes", "No"]
        assert result["prices"] == [0.48, 0.52]

    def test_neg_risk_extraction(self):
        client = _make_client()
        # negRisk=True
        m1 = {
            "conditionId": "0x1",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "clobTokenIds": '[]',
            "negRisk": True,
        }
        assert client._parse_market(m1, "epl")["neg_risk"] is True

        # enableNegRisk=True, negRisk absent
        m2 = {
            "conditionId": "0x2",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "clobTokenIds": '[]',
            "enableNegRisk": True,
        }
        assert client._parse_market(m2, "epl")["neg_risk"] is True

        # Both false
        m3 = {
            "conditionId": "0x3",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "clobTokenIds": '[]',
            "negRisk": False,
            "enableNegRisk": False,
        }
        assert client._parse_market(m3, "epl")["neg_risk"] is False

    def test_liquidity_extraction(self):
        client = _make_client()
        m = {
            "conditionId": "0x4",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "clobTokenIds": '[]',
            "liquidity": "25000.50",
        }
        result = client._parse_market(m, "epl")
        assert result["liquidity"] == 25000.50

    def test_missing_fields_use_defaults(self):
        client = _make_client()
        m = {
            "outcomes": '["A","B"]',
            "outcomePrices": '["0.6","0.4"]',
            "clobTokenIds": '[]',
        }
        result = client._parse_market(m, "nba")
        assert result["condition_id"] == ""
        assert result["question"] == ""
        assert result["volume_24h"] == 0.0
        assert result["liquidity"] == 0.0
        assert result["active"] is True

    def test_sport_field_set(self):
        client = _make_client()
        m = {
            "conditionId": "0x5",
            "outcomes": '["Yes","No"]',
            "outcomePrices": '["0.5","0.5"]',
            "clobTokenIds": '[]',
        }
        result = client._parse_market(m, "nba")
        assert result["sport"] == "nba"


# ---------------------------------------------------------------------------
# get_active_sports_markets
# ---------------------------------------------------------------------------

class TestGetActiveSportsMarkets:
    """Test active sports market discovery."""

    @patch("src.polymarket_client.requests.Session.get")
    def test_limit_parameter_is_200(self, mock_get):
        """Verify the default limit passed to Gamma API is 200."""
        # First call: sport tags; Second call: events for each sport
        tag_resp = _mock_response(200, json_data=[
            {"sport": "epl", "tags": "1,82"},
        ])
        events_resp = _mock_response(200, json_data=[])

        mock_get.side_effect = [tag_resp, events_resp]

        client = _make_client()
        client.config.target_sports = ["epl"]
        client.get_active_sports_markets()

        # The second call should have limit=200
        events_call = mock_get.call_args_list[1]
        assert events_call.kwargs.get("params", {}).get("limit") == 200

    @patch("src.polymarket_client.requests.Session.get")
    def test_custom_limit_parameter(self, mock_get):
        tag_resp = _mock_response(200, json_data=[
            {"sport": "epl", "tags": "1,82"},
        ])
        events_resp = _mock_response(200, json_data=[])
        mock_get.side_effect = [tag_resp, events_resp]

        client = _make_client()
        client.config.target_sports = ["epl"]
        client.get_active_sports_markets(limit=50)

        events_call = mock_get.call_args_list[1]
        assert events_call.kwargs.get("params", {}).get("limit") == 50

    @patch("src.polymarket_client.requests.Session.get")
    def test_market_parsing_from_gamma(self, mock_get):
        tag_resp = _mock_response(200, json_data=[
            {"sport": "epl", "tags": "1,82"},
        ])
        events_resp = _mock_response(200, json_data=[
            {
                "slug": "epl-ars-che-2026-03-08",
                "markets": [
                    {
                        "conditionId": "0xabc",
                        "question": "Will Arsenal win?",
                        "slug": "epl-ars-che-2026-03-08-ars",
                        "outcomes": '["Yes","No"]',
                        "outcomePrices": '["0.60","0.40"]',
                        "clobTokenIds": '["tk1","tk2"]',
                        "volume24hr": "5000",
                        "liquidity": "10000",
                        "endDate": "2026-03-08T17:00:00Z",
                        "negRisk": False,
                        "active": True,
                    }
                ],
            }
        ])
        mock_get.side_effect = [tag_resp, events_resp]

        client = _make_client()
        client.config.target_sports = ["epl"]
        markets = client.get_active_sports_markets()
        assert len(markets) == 1
        assert markets[0]["condition_id"] == "0xabc"
        assert markets[0]["sport"] == "epl"
        assert markets[0]["prices"] == [0.60, 0.40]

    @patch("src.polymarket_client.requests.Session.get")
    def test_slug_filtering(self, mock_get):
        """Only events whose slug starts with sport prefix are included."""
        tag_resp = _mock_response(200, json_data=[
            {"sport": "epl", "tags": "1,82"},
        ])
        events_resp = _mock_response(200, json_data=[
            {
                "slug": "epl-ars-che-2026-03-08",
                "conditionId": "0x1",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.5","0.5"]',
                "clobTokenIds": '[]',
            },
            {
                "slug": "other-event-not-epl",
                "conditionId": "0x2",
                "outcomes": '["Yes","No"]',
                "outcomePrices": '["0.5","0.5"]',
                "clobTokenIds": '[]',
            },
        ])
        mock_get.side_effect = [tag_resp, events_resp]

        client = _make_client()
        client.config.target_sports = ["epl"]
        markets = client.get_active_sports_markets()
        # The second event slug doesn't start with "epl-" so it's filtered out
        # But events without "markets" key use [event] itself as the market list
        # The first event slug starts with "epl-" -> included
        # The second doesn't -> excluded
        assert len(markets) == 1

    @patch("src.polymarket_client.requests.Session.get")
    def test_no_tag_mapping_skips_sport(self, mock_get):
        tag_resp = _mock_response(200, json_data=[])  # no tags at all
        mock_get.side_effect = [tag_resp]

        client = _make_client()
        client.config.target_sports = ["epl"]
        markets = client.get_active_sports_markets()
        assert len(markets) == 0


# ---------------------------------------------------------------------------
# Dry run mode
# ---------------------------------------------------------------------------

class TestDryRunMode:
    """Test that dry_run prevents real API calls."""

    def test_place_limit_order_dry_run(self):
        client = _make_client(dry_run=True)
        result = client.place_limit_order(
            token_id="0x" + "a" * 64,
            price=0.45,
            size=10.0,
            side="BUY",
            neg_risk=False,
        )
        assert result["orderID"] == "dry-run"
        assert result["status"] == "simulated"

    def test_place_limit_order_sell_dry_run(self):
        client = _make_client(dry_run=True)
        result = client.place_limit_order(
            token_id="0x" + "b" * 64,
            price=0.70,
            size=5.0,
            side="SELL",
        )
        assert result["status"] == "simulated"

    def test_place_market_order_dry_run(self):
        client = _make_client(dry_run=True)
        result = client.place_market_order(
            token_id="0x" + "c" * 64,
            amount_usdc=25.0,
            side="BUY",
        )
        assert result["orderID"] == "dry-run"
        assert result["status"] == "simulated"

    def test_merge_positions_dry_run(self):
        client = _make_client(dry_run=True)
        result = client.merge_positions(
            condition_id="0x" + "d" * 64,
            amount=100,
        )
        assert result["status"] == "simulated"
        assert result["amount"] == 100

    def test_cancel_all_orders_dry_run(self):
        client = _make_client(dry_run=True)
        result = client.cancel_all_orders()
        assert result == {}

    def test_dry_run_does_not_use_clob(self):
        """Verify that _clob is never called in dry_run mode."""
        client = _make_client(dry_run=True)
        client._clob = MagicMock()

        client.place_limit_order("token", 0.5, 10.0)
        client.place_market_order("token", 25.0)
        client.merge_positions("cond", 50)
        client.cancel_all_orders()

        # _clob methods should never be invoked
        client._clob.create_and_post_order.assert_not_called()
        client._clob.merge.assert_not_called()
        client._clob.cancel_all.assert_not_called()


# ---------------------------------------------------------------------------
# _get_sport_tags
# ---------------------------------------------------------------------------

class TestGetSportTags:
    """Test sport tag mapping."""

    @patch("src.polymarket_client.requests.Session.get")
    def test_extracts_non_generic_tag(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data=[
            {"sport": "epl", "tags": "1,82,306"},
            {"sport": "nba", "tags": "1,100"},
        ])
        client = _make_client()
        tags = client._get_sport_tags()
        assert tags["epl"] == "82"  # first non-"1" tag
        assert tags["nba"] == "100"

    @patch("src.polymarket_client.requests.Session.get")
    def test_api_error_returns_empty(self, mock_get):
        mock_get.side_effect = Exception("API down")
        client = _make_client()
        tags = client._get_sport_tags()
        assert tags == {}
