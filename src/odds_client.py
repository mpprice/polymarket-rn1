"""Fetch sharp odds from The Odds API and convert to implied probabilities.

V2: Supports h2h, spreads, and totals markets in a single API call.
"""
import logging
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

# Mapping from Polymarket sport slugs to The Odds API sport keys
# Extended based on RN1 activity analysis (all sports RN1 trades)
SPORT_KEY_MAP = {
    # Football (soccer) — top tier
    "epl": "soccer_epl",
    "bun": "soccer_germany_bundesliga",
    "sea": "soccer_italy_serie_a",
    "fl1": "soccer_france_ligue_one",
    "lal": "soccer_spain_la_liga",
    "ucl": "soccer_uefa_champs_league",
    "uel": "soccer_uefa_europa_league",
    # Football — second tier
    "elc": "soccer_efl_champ",
    "itsb": "soccer_italy_serie_b",
    "bl2": "soccer_germany_bundesliga2",
    "por": "soccer_portugal_primeira_liga",
    "es2": "soccer_spain_segunda_division",
    "ere": "soccer_netherlands_eredivisie",
    "scop": "soccer_spl",
    "mex": "soccer_mexico_ligamx",
    "arg": "soccer_argentina_primera_division",
    "bra": "soccer_brazil_campeonato",
    "mls": "soccer_usa_mls",
    "tur": "soccer_turkey_super_league",
    # "spl" removed: soccer_spain_primera_division doesn't exist; La Liga is "lal" -> soccer_spain_la_liga
    # US sports
    "nba": "basketball_nba",
    "cbb": "basketball_ncaab",
    "nfl": "americanfootball_nfl",
    "nhl": "icehockey_nhl",
    # Tennis — The Odds API uses tournament-specific keys
    "atp": "tennis_atp_indian_wells",  # rotates by active tournament
    "wta": "tennis_wta_indian_wells",
    # Esports — not covered by The Odds API
    # "cs2", "lol", "dota2", "val", "codmw" — would need separate data source
}

# Sharp bookmakers to use as fair odds reference (in priority order)
SHARP_BOOKS = ["pinnacle", "betfair_ex_eu", "matchbook", "betcris"]


class OddsClient:
    """Fetch and normalize odds from The Odds API."""

    BASE_URL = "https://api.the-odds-api.com/v4"

    def __init__(self, config: Config):
        self.api_key = config.odds_api_key
        self._session = requests.Session()
        self._remaining_requests = None

    def get_odds(self, sport_key: str, markets: str = "h2h,spreads,totals") -> list[dict]:
        """Fetch odds for a sport. Returns normalized events with implied probs.

        Args:
            sport_key: The Odds API sport key (e.g. 'soccer_epl')
            markets: Comma-separated market types ('h2h', 'spreads', 'totals')
        """
        url = f"{self.BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "eu",
            "markets": markets,
            "oddsFormat": "decimal",
            "bookmakers": ",".join(SHARP_BOOKS),
        }
        resp = self._session.get(url, params=params, timeout=15)
        self._remaining_requests = resp.headers.get("x-requests-remaining")
        resp.raise_for_status()
        raw = resp.json()

        events = []
        for event in raw:
            parsed = self._parse_event_multi(event)
            if parsed:
                events.append(parsed)

        log.info("Fetched %d events for %s (API requests remaining: %s)",
                 len(events), sport_key, self._remaining_requests)
        return events

    def get_all_sports_odds(self, polymarket_sports: list[str]) -> dict[str, list[dict]]:
        """Fetch odds for all configured Polymarket sport categories."""
        all_odds = {}
        for pm_sport in polymarket_sports:
            odds_key = SPORT_KEY_MAP.get(pm_sport)
            if not odds_key:
                log.debug("No odds mapping for Polymarket sport: %s", pm_sport)
                continue
            try:
                events = self.get_odds(odds_key)
                all_odds[pm_sport] = events
            except requests.HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    # Sport key not in season — try to auto-resolve for tennis
                    resolved = self._resolve_sport_key(pm_sport, odds_key)
                    if resolved:
                        try:
                            events = self.get_odds(resolved)
                            all_odds[pm_sport] = events
                            SPORT_KEY_MAP[pm_sport] = resolved
                            log.info("Auto-resolved %s -> %s", pm_sport, resolved)
                            continue
                        except Exception:
                            pass
                    log.warning("Sport %s (%s) not in season (404)", pm_sport, odds_key)
                else:
                    log.warning("Failed to fetch odds for %s (%s): %s", pm_sport, odds_key, e)
            except Exception as e:
                log.warning("Failed to fetch odds for %s (%s): %s", pm_sport, odds_key, e)
        return all_odds

    def _resolve_sport_key(self, pm_sport: str, failed_key: str) -> str | None:
        """Try to find the correct sport key via the /sports endpoint."""
        prefix = None
        if pm_sport == "atp":
            prefix = "tennis_atp_"
        elif pm_sport == "wta":
            prefix = "tennis_wta_"
        else:
            return None

        try:
            resp = self._session.get(
                f"{self.BASE_URL}/sports",
                params={"apiKey": self.api_key},
                timeout=10,
            )
            resp.raise_for_status()
            for sport in resp.json():
                if sport["key"].startswith(prefix) and sport.get("active"):
                    return sport["key"]
        except Exception as e:
            log.debug("Failed to resolve sport key for %s: %s", pm_sport, e)
        return None

    def _parse_event_multi(self, event: dict) -> Optional[dict]:
        """Parse an event with h2h, spreads, and totals from the sharpest book."""
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        commence = event.get("commence_time", "")

        # Find the sharpest available bookmaker
        bookmakers = event.get("bookmakers", [])
        sharp_book = None
        for preferred in SHARP_BOOKS:
            for bm in bookmakers:
                if bm["key"] == preferred:
                    sharp_book = bm
                    break
            if sharp_book:
                break

        if not sharp_book:
            if bookmakers:
                sharp_book = bookmakers[0]
            else:
                return None

        result = {
            "home_team": home,
            "away_team": away,
            "commence_time": commence,
            "sport_key": event.get("sport_key", ""),
            "bookmaker": sharp_book["key"],
        }

        for market in sharp_book.get("markets", []):
            key = market["key"]
            if key == "h2h":
                result["outcomes"], result["overround"] = self._parse_h2h(market)
                result["market_type"] = "h2h"
            elif key == "spreads":
                result["spread_outcomes"] = self._parse_spreads(market)
            elif key == "totals":
                result["total_outcomes"] = self._parse_totals(market)

        # Must have at least h2h to be useful
        if "outcomes" not in result:
            return None

        return result

    def _parse_h2h(self, market: dict) -> tuple[dict, float]:
        """Parse h2h market into fair probabilities."""
        outcomes = {}
        total_implied = 0.0
        for outcome in market.get("outcomes", []):
            name = outcome["name"]
            decimal_odds = outcome["price"]
            implied_prob = 1.0 / decimal_odds if decimal_odds > 0 else 0
            total_implied += implied_prob
            outcomes[name] = {
                "decimal_odds": decimal_odds,
                "implied_prob_raw": implied_prob,
            }

        # Remove overround
        if total_implied > 0:
            for name in outcomes:
                outcomes[name]["fair_prob"] = outcomes[name]["implied_prob_raw"] / total_implied

        return outcomes, total_implied - 1.0

    def _parse_spreads(self, market: dict) -> dict:
        """Parse spreads market into fair probabilities with point values."""
        outcomes = {}
        total_implied = 0.0
        raw = []
        for outcome in market.get("outcomes", []):
            name = outcome["name"]
            decimal_odds = outcome["price"]
            point = outcome.get("point", 0)
            implied_prob = 1.0 / decimal_odds if decimal_odds > 0 else 0
            total_implied += implied_prob
            raw.append((name, decimal_odds, implied_prob, point))

        # Remove overround
        for name, decimal_odds, implied_prob, point in raw:
            fair_prob = implied_prob / total_implied if total_implied > 0 else 0
            outcomes[name] = {
                "decimal_odds": decimal_odds,
                "implied_prob_raw": implied_prob,
                "fair_prob": fair_prob,
                "point": point,
            }

        return outcomes

    def _parse_totals(self, market: dict) -> dict:
        """Parse totals market into fair probabilities with point values."""
        outcomes = {}
        total_implied = 0.0
        raw = []
        for outcome in market.get("outcomes", []):
            name = outcome["name"]
            decimal_odds = outcome["price"]
            point = outcome.get("point", 0)
            implied_prob = 1.0 / decimal_odds if decimal_odds > 0 else 0
            total_implied += implied_prob
            raw.append((name, decimal_odds, implied_prob, point))

        for name, decimal_odds, implied_prob, point in raw:
            fair_prob = implied_prob / total_implied if total_implied > 0 else 0
            outcomes[name] = {
                "decimal_odds": decimal_odds,
                "implied_prob_raw": implied_prob,
                "fair_prob": fair_prob,
                "point": point,
            }

        return outcomes

    @property
    def requests_remaining(self) -> Optional[str]:
        return self._remaining_requests
