"""Unit tests for src.odds_client.OddsClient."""
import json
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.config import Config
from src.odds_client import OddsClient, SPORT_KEY_MAP, SHARP_BOOKS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_response(status_code=200, json_data=None, headers=None):
    """Create a mock requests.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or []
    resp.headers = headers or {"x-requests-remaining": "450"}
    resp.raise_for_status = MagicMock()
    if status_code >= 400:
        from requests.exceptions import HTTPError
        err = HTTPError(response=resp)
        resp.raise_for_status.side_effect = err
    return resp


def _make_client() -> OddsClient:
    cfg = Config()
    cfg.odds_api_key = "test-key"
    client = OddsClient(cfg)
    return client


# ---------------------------------------------------------------------------
# SPORT_KEY_MAP completeness
# ---------------------------------------------------------------------------

class TestSportKeyMap:
    """Verify SPORT_KEY_MAP covers all target sports."""

    def test_all_target_sports_have_mapping(self):
        cfg = Config()
        for sport in cfg.target_sports:
            assert sport in SPORT_KEY_MAP, f"'{sport}' missing from SPORT_KEY_MAP"

    def test_cfb_maps_to_ncaaf(self):
        assert SPORT_KEY_MAP["cfb"] == "americanfootball_ncaaf"

    def test_nhl_maps_to_icehockey(self):
        assert SPORT_KEY_MAP["nhl"] == "icehockey_nhl"

    def test_cbb_maps_to_ncaab(self):
        assert SPORT_KEY_MAP["cbb"] == "basketball_ncaab"

    def test_ere_maps_to_eredivisie(self):
        assert SPORT_KEY_MAP["ere"] == "soccer_netherlands_eredivisie"

    def test_epl_maps_to_soccer_epl(self):
        assert SPORT_KEY_MAP["epl"] == "soccer_epl"

    def test_nba_maps_to_basketball_nba(self):
        assert SPORT_KEY_MAP["nba"] == "basketball_nba"

    def test_sharp_books_order(self):
        assert SHARP_BOOKS == ["pinnacle", "betfair_ex_eu", "matchbook", "betcris"]


# ---------------------------------------------------------------------------
# _parse_h2h
# ---------------------------------------------------------------------------

class TestParseH2H:
    """Test head-to-head market parsing."""

    def test_fair_probs_sum_to_one(self):
        client = _make_client()
        market = {
            "key": "h2h",
            "outcomes": [
                {"name": "Team A", "price": 2.0},
                {"name": "Team B", "price": 3.5},
                {"name": "Draw", "price": 4.0},
            ],
        }
        outcomes, overround = client._parse_h2h(market)
        total_fair = sum(o["fair_prob"] for o in outcomes.values())
        assert abs(total_fair - 1.0) < 1e-9, f"Fair probs sum to {total_fair}, not 1.0"

    def test_overround_calculation(self):
        client = _make_client()
        market = {
            "key": "h2h",
            "outcomes": [
                {"name": "Team A", "price": 2.0},   # implied 0.5
                {"name": "Team B", "price": 3.5},   # implied ~0.2857
                {"name": "Draw", "price": 4.0},     # implied 0.25
            ],
        }
        outcomes, overround = client._parse_h2h(market)
        # total_implied = 0.5 + 0.2857 + 0.25 = 1.0357
        expected_overround = (1.0 / 2.0 + 1.0 / 3.5 + 1.0 / 4.0) - 1.0
        assert abs(overround - expected_overround) < 1e-6

    def test_single_outcome(self):
        client = _make_client()
        market = {
            "key": "h2h",
            "outcomes": [
                {"name": "Winner", "price": 1.5},
            ],
        }
        outcomes, overround = client._parse_h2h(market)
        assert len(outcomes) == 1
        assert abs(outcomes["Winner"]["fair_prob"] - 1.0) < 1e-9

    def test_two_way_market(self):
        client = _make_client()
        market = {
            "key": "h2h",
            "outcomes": [
                {"name": "Home", "price": 1.90},
                {"name": "Away", "price": 1.90},
            ],
        }
        outcomes, overround = client._parse_h2h(market)
        assert abs(outcomes["Home"]["fair_prob"] - 0.5) < 1e-9
        assert abs(outcomes["Away"]["fair_prob"] - 0.5) < 1e-9

    def test_empty_outcomes(self):
        client = _make_client()
        market = {"key": "h2h", "outcomes": []}
        outcomes, overround = client._parse_h2h(market)
        assert len(outcomes) == 0
        assert overround == -1.0  # total_implied=0, so 0-1=-1


# ---------------------------------------------------------------------------
# _parse_spreads
# ---------------------------------------------------------------------------

class TestParseSpreads:
    """Test spread market parsing."""

    def test_spread_fair_probs_normalize(self):
        client = _make_client()
        market = {
            "key": "spreads",
            "outcomes": [
                {"name": "Team A", "price": 1.91, "point": -2.5},
                {"name": "Team B", "price": 1.91, "point": 2.5},
            ],
        }
        outcomes = client._parse_spreads(market)
        total = sum(o["fair_prob"] for o in outcomes.values())
        assert abs(total - 1.0) < 1e-9

    def test_spread_point_extraction(self):
        client = _make_client()
        market = {
            "key": "spreads",
            "outcomes": [
                {"name": "Team A", "price": 1.95, "point": -3.5},
                {"name": "Team B", "price": 1.87, "point": 3.5},
            ],
        }
        outcomes = client._parse_spreads(market)
        assert outcomes["Team A"]["point"] == -3.5
        assert outcomes["Team B"]["point"] == 3.5

    def test_spread_decimal_odds_preserved(self):
        client = _make_client()
        market = {
            "key": "spreads",
            "outcomes": [
                {"name": "Home", "price": 2.10, "point": -1.5},
                {"name": "Away", "price": 1.80, "point": 1.5},
            ],
        }
        outcomes = client._parse_spreads(market)
        assert outcomes["Home"]["decimal_odds"] == 2.10
        assert outcomes["Away"]["decimal_odds"] == 1.80

    def test_spread_missing_point_defaults_to_zero(self):
        client = _make_client()
        market = {
            "key": "spreads",
            "outcomes": [
                {"name": "Team A", "price": 1.90},
            ],
        }
        outcomes = client._parse_spreads(market)
        assert outcomes["Team A"]["point"] == 0


# ---------------------------------------------------------------------------
# _parse_totals
# ---------------------------------------------------------------------------

class TestParseTotals:
    """Test totals market parsing."""

    def test_totals_over_under(self):
        client = _make_client()
        market = {
            "key": "totals",
            "outcomes": [
                {"name": "Over", "price": 1.95, "point": 2.5},
                {"name": "Under", "price": 1.87, "point": 2.5},
            ],
        }
        outcomes = client._parse_totals(market)
        assert "Over" in outcomes
        assert "Under" in outcomes
        total = sum(o["fair_prob"] for o in outcomes.values())
        assert abs(total - 1.0) < 1e-9

    def test_totals_point_value(self):
        client = _make_client()
        market = {
            "key": "totals",
            "outcomes": [
                {"name": "Over", "price": 1.85, "point": 226.5},
                {"name": "Under", "price": 1.98, "point": 226.5},
            ],
        }
        outcomes = client._parse_totals(market)
        assert outcomes["Over"]["point"] == 226.5
        assert outcomes["Under"]["point"] == 226.5


# ---------------------------------------------------------------------------
# _parse_event_multi
# ---------------------------------------------------------------------------

class TestParseEventMulti:
    """Test full event parsing with bookmaker priority."""

    def _full_event(self, bookmakers):
        return {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-03-08T15:00:00Z",
            "sport_key": "soccer_epl",
            "bookmakers": bookmakers,
        }

    def _bookmaker(self, key, h2h_outcomes=None, spread_outcomes=None, total_outcomes=None):
        markets = []
        if h2h_outcomes:
            markets.append({"key": "h2h", "outcomes": h2h_outcomes})
        if spread_outcomes:
            markets.append({"key": "spreads", "outcomes": spread_outcomes})
        if total_outcomes:
            markets.append({"key": "totals", "outcomes": total_outcomes})
        return {"key": key, "markets": markets}

    def test_selects_pinnacle_as_sharpest(self):
        client = _make_client()
        h2h = [
            {"name": "Arsenal", "price": 1.80},
            {"name": "Chelsea", "price": 3.50},
            {"name": "Draw", "price": 4.20},
        ]
        event = self._full_event([
            self._bookmaker("betcris", h2h_outcomes=h2h),
            self._bookmaker("pinnacle", h2h_outcomes=h2h),
            self._bookmaker("matchbook", h2h_outcomes=h2h),
        ])
        result = client._parse_event_multi(event)
        assert result["bookmaker"] == "pinnacle"

    def test_selects_betfair_if_no_pinnacle(self):
        client = _make_client()
        h2h = [
            {"name": "Arsenal", "price": 1.80},
            {"name": "Chelsea", "price": 3.50},
            {"name": "Draw", "price": 4.20},
        ]
        event = self._full_event([
            self._bookmaker("betcris", h2h_outcomes=h2h),
            self._bookmaker("betfair_ex_eu", h2h_outcomes=h2h),
        ])
        result = client._parse_event_multi(event)
        assert result["bookmaker"] == "betfair_ex_eu"

    def test_fallback_to_first_bookmaker(self):
        client = _make_client()
        h2h = [
            {"name": "Arsenal", "price": 1.80},
            {"name": "Chelsea", "price": 3.50},
            {"name": "Draw", "price": 4.20},
        ]
        event = self._full_event([
            self._bookmaker("some_random_book", h2h_outcomes=h2h),
        ])
        result = client._parse_event_multi(event)
        assert result["bookmaker"] == "some_random_book"

    def test_no_bookmakers_returns_none(self):
        client = _make_client()
        event = self._full_event([])
        result = client._parse_event_multi(event)
        assert result is None

    def test_no_h2h_market_returns_none(self):
        """Event must have h2h to be useful."""
        client = _make_client()
        spread = [
            {"name": "Arsenal", "price": 1.90, "point": -1.5},
            {"name": "Chelsea", "price": 1.90, "point": 1.5},
        ]
        event = self._full_event([
            self._bookmaker("pinnacle", spread_outcomes=spread),
        ])
        result = client._parse_event_multi(event)
        assert result is None

    def test_multi_market_parsing(self):
        """Full event with h2h + spreads + totals."""
        client = _make_client()
        h2h = [
            {"name": "Arsenal", "price": 1.80},
            {"name": "Chelsea", "price": 3.50},
            {"name": "Draw", "price": 4.20},
        ]
        spreads = [
            {"name": "Arsenal", "price": 1.91, "point": -1.5},
            {"name": "Chelsea", "price": 1.91, "point": 1.5},
        ]
        totals = [
            {"name": "Over", "price": 1.85, "point": 2.5},
            {"name": "Under", "price": 1.98, "point": 2.5},
        ]
        event = self._full_event([
            self._bookmaker("pinnacle",
                            h2h_outcomes=h2h,
                            spread_outcomes=spreads,
                            total_outcomes=totals),
        ])
        result = client._parse_event_multi(event)
        assert "outcomes" in result
        assert "spread_outcomes" in result
        assert "total_outcomes" in result
        assert result["home_team"] == "Arsenal"
        assert result["away_team"] == "Chelsea"
        assert result["sport_key"] == "soccer_epl"
        assert result["market_type"] == "h2h"


# ---------------------------------------------------------------------------
# get_all_sports_odds error handling
# ---------------------------------------------------------------------------

class TestGetAllSportsOdds:
    """Test get_all_sports_odds with mocked HTTP calls."""

    @patch("src.odds_client.requests.Session.get")
    def test_404_logs_warning_and_continues(self, mock_get):
        """404 for a sport should not crash; other sports should still be fetched."""
        from requests.exceptions import HTTPError

        resp_404 = _mock_response(404)
        resp_ok = _mock_response(200, json_data=[])
        # Also need a response for _resolve_sport_key (returns no active tournaments)
        resp_sports = _mock_response(200, json_data=[])

        def side_effect(url, **kwargs):
            if "soccer_epl" in url:
                return resp_404
            if "/sports" in url and "soccer" not in url:
                return resp_sports
            return resp_ok

        mock_get.side_effect = side_effect
        client = _make_client()
        # Only test two sports to keep it simple
        result = client.get_all_sports_odds(["epl", "nba"])
        # EPL should fail with 404, NBA should succeed (empty)
        assert "nba" in result
        # EPL may or may not be in result (depends on resolve), but no exception raised

    @patch("src.odds_client.requests.Session.get")
    def test_network_error_continues(self, mock_get):
        from requests.exceptions import ConnectionError

        def side_effect(url, **kwargs):
            if "soccer_epl" in url:
                raise ConnectionError("Network error")
            return _mock_response(200, json_data=[])

        mock_get.side_effect = side_effect
        client = _make_client()
        result = client.get_all_sports_odds(["epl", "nba"])
        assert "nba" in result

    @patch("src.odds_client.requests.Session.get")
    def test_unmapped_sport_skipped(self, mock_get):
        mock_get.return_value = _mock_response(200, json_data=[])
        client = _make_client()
        result = client.get_all_sports_odds(["unknown_sport_xyz"])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# _resolve_sport_key
# ---------------------------------------------------------------------------

class TestResolveSportKey:
    """Test auto-resolution of tennis sport keys."""

    @patch("src.odds_client.requests.Session.get")
    def test_resolve_atp_finds_active_tournament(self, mock_get):
        sports_data = [
            {"key": "tennis_atp_roland_garros", "active": True},
            {"key": "tennis_atp_indian_wells", "active": False},
            {"key": "soccer_epl", "active": True},
        ]
        mock_get.return_value = _mock_response(200, json_data=sports_data)
        client = _make_client()
        result = client._resolve_sport_key("atp", "tennis_atp_indian_wells")
        assert result == "tennis_atp_roland_garros"

    @patch("src.odds_client.requests.Session.get")
    def test_resolve_wta_finds_active_tournament(self, mock_get):
        sports_data = [
            {"key": "tennis_wta_australian_open", "active": True},
        ]
        mock_get.return_value = _mock_response(200, json_data=sports_data)
        client = _make_client()
        result = client._resolve_sport_key("wta", "tennis_wta_indian_wells")
        assert result == "tennis_wta_australian_open"

    @patch("src.odds_client.requests.Session.get")
    def test_non_tennis_returns_none(self, mock_get):
        client = _make_client()
        result = client._resolve_sport_key("nba", "basketball_nba")
        assert result is None
        # Should not even call the API
        mock_get.assert_not_called()

    @patch("src.odds_client.requests.Session.get")
    def test_no_active_tournament_returns_none(self, mock_get):
        sports_data = [
            {"key": "tennis_atp_wimbledon", "active": False},
        ]
        mock_get.return_value = _mock_response(200, json_data=sports_data)
        client = _make_client()
        result = client._resolve_sport_key("atp", "tennis_atp_indian_wells")
        assert result is None

    @patch("src.odds_client.requests.Session.get")
    def test_api_error_returns_none(self, mock_get):
        mock_get.side_effect = Exception("API down")
        client = _make_client()
        result = client._resolve_sport_key("atp", "tennis_atp_indian_wells")
        assert result is None


# ---------------------------------------------------------------------------
# get_odds basic flow
# ---------------------------------------------------------------------------

class TestGetOdds:
    """Test get_odds method."""

    @patch("src.odds_client.requests.Session.get")
    def test_returns_parsed_events(self, mock_get):
        raw_events = [
            {
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2026-03-08T15:00:00Z",
                "sport_key": "soccer_epl",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Arsenal", "price": 1.80},
                                    {"name": "Chelsea", "price": 3.50},
                                    {"name": "Draw", "price": 4.20},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        mock_get.return_value = _mock_response(200, json_data=raw_events)
        client = _make_client()
        events = client.get_odds("soccer_epl")
        assert len(events) == 1
        assert events[0]["home_team"] == "Arsenal"
        assert events[0]["bookmaker"] == "pinnacle"

    @patch("src.odds_client.requests.Session.get")
    def test_tracks_remaining_requests(self, mock_get):
        mock_get.return_value = _mock_response(
            200, json_data=[], headers={"x-requests-remaining": "123"}
        )
        client = _make_client()
        client.get_odds("soccer_epl")
        assert client.requests_remaining == "123"
