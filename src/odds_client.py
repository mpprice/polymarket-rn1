"""Fetch sharp odds from The Odds API and convert to implied probabilities."""
import logging
from typing import Optional

import requests

from .config import Config

log = logging.getLogger(__name__)

# Mapping from Polymarket sport slugs to The Odds API sport keys
SPORT_KEY_MAP = {
    "epl": "soccer_epl",
    "bun": "soccer_germany_bundesliga",
    "sea": "soccer_italy_serie_a",
    "fl1": "soccer_france_ligue_one",
    "lal": "soccer_spain_la_liga",
    "ucl": "soccer_uefa_champs_league",
    "uel": "soccer_uefa_europa_league",
    "nba": "basketball_nba",
    "nfl": "americanfootball_nfl",
    "atp": "tennis_atp_french_open",  # varies by tournament
    "wta": "tennis_wta_french_open",
    # CS2/esports not covered by The Odds API - needs separate source
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

    def get_odds(self, sport_key: str, markets: str = "h2h") -> list[dict]:
        """Fetch odds for a sport. Returns normalized events with implied probs.

        Args:
            sport_key: The Odds API sport key (e.g. 'soccer_epl')
            markets: Odds market type ('h2h', 'spreads', 'totals')
        """
        url = f"{self.BASE_URL}/sports/{sport_key}/odds"
        params = {
            "apiKey": self.api_key,
            "regions": "eu",  # European decimal odds
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
            parsed = self._parse_event(event, markets)
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
            except Exception as e:
                log.warning("Failed to fetch odds for %s (%s): %s", pm_sport, odds_key, e)
        return all_odds

    def _parse_event(self, event: dict, market_type: str) -> Optional[dict]:
        """Extract sharp implied probabilities from an event."""
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
            # Fall back to first available
            if bookmakers:
                sharp_book = bookmakers[0]
            else:
                return None

        # Extract odds from the target market
        for market in sharp_book.get("markets", []):
            if market["key"] != market_type:
                continue

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

            # Remove overround (normalize to true probabilities)
            if total_implied > 0:
                for name in outcomes:
                    outcomes[name]["fair_prob"] = outcomes[name]["implied_prob_raw"] / total_implied

            return {
                "home_team": home,
                "away_team": away,
                "commence_time": commence,
                "sport_key": event.get("sport_key", ""),
                "bookmaker": sharp_book["key"],
                "market_type": market_type,
                "outcomes": outcomes,
                "overround": total_implied - 1.0,
            }
        return None

    @property
    def requests_remaining(self) -> Optional[str]:
        return self._remaining_requests
