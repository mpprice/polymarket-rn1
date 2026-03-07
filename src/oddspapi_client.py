"""Fetch esports + cricket odds from OddsPapi (Pinnacle).

Outputs the same event format as OddsClient so the matcher works unchanged.

API structure:
  1. /sports           → list sport IDs
  2. /tournaments      → active tournaments per sport (has upcoming/live counts)
  3. /fixtures         → participant names per tournament
  4. /odds-by-tournaments → Pinnacle odds (no names — must join with fixtures)

Key differences from The Odds API:
  - Moneyline market ID differs by sport (CS2=171, Dota2=161, Cricket=340)
  - odds-by-tournaments returns max 5 tournament IDs per call
  - Participant names only come from /fixtures endpoint

API docs: https://oddspapi.io/us/docs
"""
import logging
import time
from typing import Optional

import requests

log = logging.getLogger(__name__)

# OddsPapi sport IDs
SPORT_IDS = {
    "cs2": 17,
    "dota2": 16,
    "lol": 18,
    "val": 61,
    "codmw": 56,
    # Cricket
    "ipl": 27,
    "crint": 27,
    "cricipl": 27,
    "cricpsl": 27,
    "cricpakt20cup": 27,
}

# Moneyline market ID per sport (varies by sport on OddsPapi)
MONEYLINE_MARKET_ID: dict[int, str] = {
    17: "171",   # CS2
    16: "161",   # Dota2
    18: "181",   # LoL (estimated pattern)
    61: "611",   # Valorant (estimated)
    56: "561",   # CoD (estimated)
    27: "340",   # Cricket match winner
}

# Moneyline outcome IDs: {market_id: (home_outcome_id, away_outcome_id)}
MONEYLINE_OUTCOMES: dict[str, tuple[str, str]] = {
    "171": ("171", "172"),  # CS2
    "161": ("161", "162"),  # Dota2
    "181": ("181", "182"),  # LoL
    "611": ("611", "612"),  # Valorant
    "561": ("561", "562"),  # CoD
    "340": ("340", "341"),  # Cricket
}

# Cricket tournament keywords to filter (skip SRL/simulated)
CRICKET_SKIP_KEYWORDS = {"SRL", "Simulated", "eSoccer", "eBasketball"}

# Cricket tournament keywords for IPL, PSL, etc.
CRICKET_TOURNAMENT_MAP = {
    "ipl": ["Premier League", "Indian Premier"],
    "cricipl": ["Premier League", "Indian Premier"],
    "cricpsl": ["Pakistan Super League", "PSL"],
    "cricpakt20cup": ["National T20 Cup", "Pakistan T20"],
    "crint": [],  # all cricket
}


class OddsPapiClient:
    """Fetch Pinnacle odds from OddsPapi for esports + cricket."""

    BASE_URL = "https://api.oddspapi.io/v4"

    # Minimum seconds between API calls to avoid 429s
    REQUEST_DELAY = 0.5

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._session = requests.Session()
        self._tournament_cache: dict[int, list[dict]] = {}
        self._fixture_name_cache: dict[str, tuple[str, str]] = {}
        self._last_request_time: float = 0

    def get_esports_odds(self, pm_sport: str) -> list[dict]:
        """Fetch Pinnacle odds for a sport, returning OddsClient-compatible events.

        Works for esports (cs2, dota2) and cricket (ipl, crint, etc.).
        """
        sport_id = SPORT_IDS.get(pm_sport)
        if not sport_id:
            return []

        try:
            # Step 1: Get active tournaments
            tournaments = self._get_active_tournaments(sport_id, pm_sport)
            if not tournaments:
                log.debug("OddsPapi: no active tournaments for %s (sportId=%d)", pm_sport, sport_id)
                return []

            # Step 2: Fetch fixture names for participant lookup
            for t in tournaments:
                tid = t["tournamentId"]
                if not any(fid for fid, names in self._fixture_name_cache.items()):
                    self._cache_fixture_names(tid)

            # Step 3: Fetch Pinnacle odds in batches of 5
            all_events = []
            tournament_ids = [t["tournamentId"] for t in tournaments]
            for i in range(0, len(tournament_ids), 5):
                batch = tournament_ids[i:i + 5]
                events = self._fetch_odds_batch(batch, sport_id)
                all_events.extend(events)

            log.info("OddsPapi: %d events for %s from %d tournaments",
                     len(all_events), pm_sport, len(tournaments))
            return all_events

        except Exception as e:
            log.warning("OddsPapi fetch failed for %s: %s", pm_sport, e)
            return []

    def _get_active_tournaments(self, sport_id: int, pm_sport: str) -> list[dict]:
        """Get tournaments with upcoming or live fixtures."""
        if sport_id not in self._tournament_cache:
            resp = self._rate_limited_get(
                f"{self.BASE_URL}/tournaments",
                params={"apiKey": self.api_key, "sportId": sport_id},
                timeout=15,
            )
            resp.raise_for_status()
            self._tournament_cache[sport_id] = resp.json()

        tournaments = self._tournament_cache[sport_id]
        active = [
            t for t in tournaments
            if isinstance(t, dict) and (t.get("upcomingFixtures", 0) > 0 or t.get("liveFixtures", 0) > 0)
        ]

        # For cricket, filter out SRL/simulated and optionally filter by league
        if sport_id == 27:
            active = [
                t for t in active
                if not any(kw in t.get("tournamentName", "") for kw in CRICKET_SKIP_KEYWORDS)
                and not any(kw in t.get("categoryName", "") for kw in CRICKET_SKIP_KEYWORDS)
            ]
            # Filter by specific league if pm_sport is specific
            league_keywords = CRICKET_TOURNAMENT_MAP.get(pm_sport, [])
            if league_keywords:
                filtered = [
                    t for t in active
                    if any(kw.lower() in t.get("tournamentName", "").lower() for kw in league_keywords)
                ]
                if filtered:
                    active = filtered

        # Cap at 20 tournaments to limit API calls
        return active[:20]

    def _cache_fixture_names(self, tournament_id: int):
        """Fetch fixtures for a tournament and cache participant names."""
        try:
            resp = self._rate_limited_get(
                f"{self.BASE_URL}/fixtures",
                params={"apiKey": self.api_key, "tournamentId": tournament_id},
                timeout=15,
            )
            resp.raise_for_status()
            fixtures = resp.json()
            for f in fixtures:
                if isinstance(f, dict) and f.get("fixtureId"):
                    self._fixture_name_cache[f["fixtureId"]] = (
                        f.get("participant1Name", ""),
                        f.get("participant2Name", ""),
                    )
            log.debug("OddsPapi: cached %d fixture names for tournament %d",
                      len(fixtures) if isinstance(fixtures, list) else 0, tournament_id)
        except Exception as e:
            log.debug("OddsPapi: failed to cache fixtures for tournament %d: %s", tournament_id, e)

    def _fetch_odds_batch(self, tournament_ids: list[int], sport_id: int) -> list[dict]:
        """Fetch Pinnacle odds for a batch of tournaments (max 5)."""
        tids_str = ",".join(str(tid) for tid in tournament_ids)

        # Ensure we have fixture names for these tournaments
        for tid in tournament_ids:
            # Check if we have any fixtures from this tournament cached
            if not any(True for _ in self._fixture_name_cache):
                self._cache_fixture_names(tid)

        try:
            resp = self._rate_limited_get(
                f"{self.BASE_URL}/odds-by-tournaments",
                params={
                    "apiKey": self.api_key,
                    "bookmaker": "pinnacle",
                    "tournamentIds": tids_str,
                },
                timeout=30,
            )
            if resp.status_code == 404:
                # No fixtures with Pinnacle odds for these tournaments
                return []
            resp.raise_for_status()
        except requests.HTTPError:
            return []

        raw = resp.json()
        if not isinstance(raw, list):
            return []

        # Cache names for any tournaments we haven't fetched yet
        missing_tids = set()
        for fixture in raw:
            fid = fixture.get("fixtureId", "")
            if fid not in self._fixture_name_cache:
                tid = fixture.get("tournamentId")
                if tid:
                    missing_tids.add(tid)

        for tid in missing_tids:
            self._cache_fixture_names(tid)

        events = []
        market_id = MONEYLINE_MARKET_ID.get(sport_id, "171")

        for fixture in raw:
            parsed = self._parse_fixture(fixture, market_id)
            if parsed:
                events.append(parsed)

        return events

    def _parse_fixture(self, fixture: dict, market_id: str) -> Optional[dict]:
        """Parse an OddsPapi odds fixture into OddsClient-compatible format."""
        fid = fixture.get("fixtureId", "")

        # Get participant names from cache
        names = self._fixture_name_cache.get(fid)
        if names:
            home, away = names
        else:
            # Fallback: try participant fields on the fixture itself
            home = fixture.get("participant1Name", "")
            away = fixture.get("participant2Name", "")

        if not home or not away:
            return None

        commence = fixture.get("startTime", "")

        # Extract Pinnacle moneyline odds
        pinnacle = fixture.get("bookmakerOdds", {}).get("pinnacle", {})
        if not pinnacle:
            return None

        markets = pinnacle.get("markets", {})

        # Try the sport-specific market ID first, then try all markets for moneyline
        home_price, away_price = self._extract_moneyline(markets, market_id)

        if home_price is None or away_price is None:
            return None

        if home_price <= 1.0 or away_price <= 1.0:
            return None

        # Build fair probabilities
        home_implied = 1.0 / home_price
        away_implied = 1.0 / away_price
        total_implied = home_implied + away_implied

        if total_implied <= 0:
            return None

        outcomes = {
            home: {
                "decimal_odds": home_price,
                "implied_prob_raw": home_implied,
                "fair_prob": home_implied / total_implied,
            },
            away: {
                "decimal_odds": away_price,
                "implied_prob_raw": away_implied,
                "fair_prob": away_implied / total_implied,
            },
        }

        sport_id = fixture.get("sportId", "")
        return {
            "home_team": home,
            "away_team": away,
            "commence_time": commence,
            "sport_key": f"oddspapi_{sport_id}",
            "bookmaker": "pinnacle",
            "outcomes": outcomes,
            "overround": total_implied - 1.0,
            "market_type": "h2h",
        }

    def _extract_moneyline(self, markets: dict, primary_market_id: str) -> tuple[Optional[float], Optional[float]]:
        """Extract home/away moneyline odds from markets dict.

        Tries the primary market ID first, then scans all markets for
        a 2-way home/away market as fallback.
        """
        # Try primary market ID
        result = self._extract_from_market(markets.get(primary_market_id, {}))
        if result[0] is not None:
            return result

        # Fallback: scan all markets for a 2-way home/away moneyline
        for mid, mdata in markets.items():
            result = self._extract_from_market(mdata)
            if result[0] is not None:
                return result

        return None, None

    def _extract_from_market(self, market: dict) -> tuple[Optional[float], Optional[float]]:
        """Extract home/away prices from a single market's outcomes."""
        if not market:
            return None, None

        outcomes = market.get("outcomes", {})
        home_price = None
        away_price = None

        for outcome_id, outcome_data in outcomes.items():
            players = outcome_data.get("players", {})
            for player_id, player_data in players.items():
                oid = player_data.get("bookmakerOutcomeId", "")
                price = player_data.get("price")
                if oid == "home" and price and price > 1.0:
                    home_price = float(price)
                elif oid == "away" and price and price > 1.0:
                    away_price = float(price)

        if home_price is not None and away_price is not None:
            return home_price, away_price
        return None, None

    def _rate_limited_get(self, url: str, params: dict, timeout: int = 15) -> requests.Response:
        """GET with rate limiting to avoid 429s."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        resp = self._session.get(url, params=params, timeout=timeout)
        self._last_request_time = time.time()
        return resp

    def clear_cache(self):
        """Clear tournament and fixture caches (call between scan cycles)."""
        self._tournament_cache.clear()
        self._fixture_name_cache.clear()
