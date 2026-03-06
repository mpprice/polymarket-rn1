#!/usr/bin/env python3
"""Edge Quality Analysis: Live comparison of Polymarket vs sharp bookmaker odds.

Fetches live data from both The Odds API (Pinnacle/Betfair) and Polymarket's
Gamma API, matches events, and computes edge quality metrics including:
- Raw edge distribution
- Edge after multiple overround removal methods (proportional, Shin's)
- Favourite-longshot bias in Polymarket vs sharp books
- Orderbook depth for markets with edges
- Summary statistics on the real-world opportunity set

Usage:
    python analysis/edge_quality_analysis.py [--use-cache] [--no-clob]

    --use-cache   Skip live API calls, use saved JSON from data/odds_cache/
    --no-clob     Skip CLOB orderbook depth checks (saves time)

Conserves API quota: only fetches 4 sports (NBA, NHL, EPL, Bundesliga).
All fetched data is saved to data/odds_cache/ for reuse.
"""
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

import requests

# ── Paths ────────────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CACHE_DIR = PROJECT_ROOT / "data" / "odds_cache"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Config ───────────────────────────────────────────────────────────────────
ODDS_API_KEY = os.getenv("ODDS_API_KEY", "b163f7a2df21a194b548a36fea51c4ef")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"

# Only fetch these to conserve quota (4 API calls)
SPORTS_TO_FETCH = {
    "basketball_nba": "NBA",
    "icehockey_nhl": "NHL",
    "soccer_epl": "EPL",
    "soccer_germany_bundesliga": "Bundesliga",
}

SHARP_BOOKS = ["pinnacle", "betfair_ex_eu", "matchbook", "betcris"]

# Team alias database (subset for matching)
TEAM_ALIASES = {
    "manchester united": ["man utd", "man united", "mufc", "mun"],
    "manchester city": ["man city", "mcfc", "mci"],
    "tottenham hotspur": ["tottenham", "spurs", "tot"],
    "wolverhampton wanderers": ["wolves", "wol"],
    "brighton and hove albion": ["brighton", "bri", "bha"],
    "west ham united": ["west ham", "whu", "wes"],
    "nottingham forest": ["nott forest", "nottm forest", "nfo", "not"],
    "newcastle united": ["newcastle", "new", "ncu"],
    "leicester city": ["leicester", "lei"],
    "arsenal": ["arsenal fc", "ars"],
    "liverpool": ["liverpool fc", "liv"],
    "chelsea": ["chelsea fc", "che"],
    "aston villa": ["avl", "vil"],
    "everton": ["everton fc", "eve"],
    "bournemouth": ["afc bournemouth", "bou"],
    "fulham": ["fulham fc", "ful"],
    "crystal palace": ["palace", "cry"],
    "brentford": ["brentford fc", "bre"],
    "ipswich town": ["ipswich", "ips"],
    "southampton": ["southampton fc", "sou"],
    "fc bayern munchen": ["bayern munich", "bayern", "bay"],
    "borussia dortmund": ["dortmund", "bvb", "dor"],
    "rb leipzig": ["leipzig", "rbl"],
    "bayer leverkusen": ["leverkusen", "lev", "b04"],
    "eintracht frankfurt": ["frankfurt", "sge", "ein"],
    "vfb stuttgart": ["stuttgart", "stu", "vfb"],
    "sc freiburg": ["freiburg", "fre", "scf"],
    "vfl wolfsburg": ["wolfsburg", "wob"],
    "borussia monchengladbach": ["gladbach", "bmg", "mon"],
    "1. fc union berlin": ["union berlin", "uni", "fcub"],
    "sv werder bremen": ["werder bremen", "werder", "wer", "svw"],
    "tsg hoffenheim": ["hoffenheim", "hof", "tsg"],
    "fc augsburg": ["augsburg", "aug", "fca"],
    "1. fc heidenheim": ["heidenheim", "hei", "fch"],
    "1. fsv mainz 05": ["mainz", "mai", "m05"],
    "fc st. pauli": ["st pauli", "pau", "stp"],
    "boston celtics": ["celtics", "bos"],
    "dallas mavericks": ["mavericks", "dal"],
    "new york knicks": ["knicks", "nyk"],
    "golden state warriors": ["warriors", "gsw"],
    "los angeles lakers": ["lakers", "lal"],
    "denver nuggets": ["nuggets", "den"],
    "houston rockets": ["rockets", "hou"],
    "milwaukee bucks": ["bucks", "mil"],
    "phoenix suns": ["suns", "phx"],
    "oklahoma city thunder": ["thunder", "okc"],
    "minnesota timberwolves": ["timberwolves", "min"],
    "cleveland cavaliers": ["cavaliers", "cavs", "cle"],
    "memphis grizzlies": ["grizzlies", "mem"],
    "sacramento kings": ["kings", "sac"],
    "indiana pacers": ["pacers", "ind"],
    "orlando magic": ["magic", "orl"],
    "miami heat": ["heat", "mia"],
    "philadelphia 76ers": ["76ers", "sixers", "phi"],
    "brooklyn nets": ["nets", "bkn"],
    "atlanta hawks": ["hawks", "atl"],
    "toronto raptors": ["raptors", "tor"],
    "los angeles clippers": ["clippers", "lac"],
    "new orleans pelicans": ["pelicans", "nop"],
    "san antonio spurs": ["sa spurs", "sas"],
    "portland trail blazers": ["trail blazers", "por"],
    "chicago bulls": ["bulls", "chi"],
    "charlotte hornets": ["hornets", "cha"],
    "detroit pistons": ["pistons", "det"],
    "washington wizards": ["wizards", "was"],
    "utah jazz": ["jazz", "uta"],
}

_ALIAS_LOOKUP = {}
_ABBREV_LOOKUP = {}
for _canon, _aliases in TEAM_ALIASES.items():
    _ALIAS_LOOKUP[_canon] = _canon
    for _a in _aliases:
        _ALIAS_LOOKUP[_a.lower()] = _canon
        if len(_a) <= 4 and _a.isalpha():
            _ABBREV_LOOKUP[_a.lower()] = _canon


def normalize_team(name):
    lower = name.lower().strip()
    for suffix in [" fc", " cf", " sc", " afc", " bc"]:
        if lower.endswith(suffix):
            lower = lower[:-len(suffix)].strip()
    return _ALIAS_LOOKUP.get(lower, lower)


def fuzzy_match(a, b, threshold=0.7):
    na = normalize_team(a)
    nb = normalize_team(b)
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


# ── Overround Removal Methods ────────────────────────────────────────────────

def remove_overround_proportional(implied_probs):
    """Proportional (multiplicative) method: divide each by total."""
    total = sum(implied_probs)
    if total <= 0:
        return implied_probs, 0.0
    fair = [p / total for p in implied_probs]
    return fair, total - 1.0


def remove_overround_shin(implied_probs, max_iter=1000, tol=1e-10):
    """Shin's (1991) model: accounts for favourite-longshot bias.

    Solves for insider trading fraction z such that:
      p_i = (sqrt(z^2 + 4*(1-z)*q_i/M) - z) / (2*(1-z))
    where q_i are raw implied probs and M is total implied prob.

    Returns fair probabilities and the estimated z parameter.
    """
    n = len(implied_probs)
    total = sum(implied_probs)
    if total <= 0 or n < 2:
        return implied_probs, 0.0

    # Bisect for z in (0, 1)
    z_lo, z_hi = 1e-10, 0.999
    best_z = 0
    best_fair = None
    best_err = 1e10

    for _ in range(max_iter):
        z = (z_lo + z_hi) / 2.0
        fair = []
        for q in implied_probs:
            disc = z * z + 4 * (1 - z) * q / total
            if disc < 0:
                disc = 0
            p = (math.sqrt(disc) - z) / (2 * (1 - z)) if (1 - z) > 1e-15 else q / total
            fair.append(p)
        s = sum(fair)
        err = abs(s - 1.0)
        if err < best_err:
            best_err = err
            best_z = z
            best_fair = fair[:]
        if err < tol:
            return fair, z
        if s > 1.0:
            z_lo = z
        else:
            z_hi = z

    # Return best found even if not perfectly converged
    if best_fair and best_err < 0.01:
        return best_fair, best_z

    # Fallback to proportional
    return remove_overround_proportional(implied_probs)


def remove_overround_power(implied_probs, max_iter=100, tol=1e-8):
    """Power method: find k such that sum(p_i^k) = 1.

    Each fair prob is p_i^k. Good for 2-way markets.
    """
    n = len(implied_probs)
    if n < 2 or sum(implied_probs) <= 0:
        return implied_probs, 0.0

    k_lo, k_hi = 0.5, 2.0
    for _ in range(max_iter):
        k = (k_lo + k_hi) / 2.0
        powered = [p ** k for p in implied_probs]
        s = sum(powered)
        if abs(s - 1.0) < tol:
            return powered, k
        if s > 1.0:
            k_hi = k
        else:
            k_lo = k

    return remove_overround_proportional(implied_probs)


def remove_overround_odds_ratio(implied_probs, max_iter=100, tol=1e-8):
    """Odds-ratio method: find c such that sum(p_i / (c + (1-c)*p_i)) = 1.

    Also known as the logit method. Good for markets with strong
    favourite-longshot bias.
    """
    n = len(implied_probs)
    if n < 2 or sum(implied_probs) <= 0:
        return implied_probs, 0.0

    c_lo, c_hi = 0.0, 0.99
    for _ in range(max_iter):
        c = (c_lo + c_hi) / 2.0
        fair = []
        for p in implied_probs:
            denom = c + (1 - c) * p
            fair.append(p / denom if denom > 0 else p)
        s = sum(fair)
        if abs(s - 1.0) < tol:
            return fair, c
        if s > 1.0:
            c_lo = c
        else:
            c_hi = c

    return remove_overround_proportional(implied_probs)


# ── Data Fetching ────────────────────────────────────────────────────────────

def fetch_odds_api(sport_key, use_cache=False):
    """Fetch odds from The Odds API. Caches to JSON."""
    cache_file = CACHE_DIR / f"odds_{sport_key}_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"

    if use_cache and cache_file.exists():
        print(f"  [CACHE] Loading {sport_key} from {cache_file.name}")
        with open(cache_file) as f:
            return json.load(f)

    print(f"  [API] Fetching {sport_key} from The Odds API...")
    url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,us",
        "markets": "h2h,spreads,totals",
        "oddsFormat": "decimal",
        "bookmakers": ",".join(SHARP_BOOKS),
    }
    resp = requests.get(url, params=params, timeout=15)
    remaining = resp.headers.get("x-requests-remaining", "?")
    used = resp.headers.get("x-requests-used", "?")
    print(f"    Status: {resp.status_code} | Quota: {remaining} remaining, {used} used")

    if resp.status_code != 200:
        print(f"    ERROR: {resp.text[:200]}")
        return []

    data = resp.json()
    with open(cache_file, "w") as f:
        json.dump(data, f, indent=2)
    print(f"    Got {len(data)} events, saved to {cache_file.name}")
    return data


def fetch_polymarket_sports(use_cache=False):
    """Fetch active Polymarket sports markets via Gamma API."""
    cache_file = CACHE_DIR / f"polymarket_sports_{datetime.now(timezone.utc).strftime('%Y%m%d')}.json"

    if use_cache and cache_file.exists():
        print(f"  [CACHE] Loading Polymarket markets from {cache_file.name}")
        with open(cache_file) as f:
            return json.load(f)

    print("  [API] Fetching Polymarket sports markets...")
    all_markets = []

    # Fetch events by sport tags
    try:
        # First get sport tag mappings
        resp = requests.get(f"{GAMMA_URL}/sports", timeout=10)
        sports = resp.json() if resp.status_code == 200 else []
        tag_map = {}
        for s in sports:
            sport_key = s.get("sport", "")
            tags = s.get("tags", "").split(",")
            for t in tags:
                t = t.strip()
                if t and t != "1":
                    tag_map[sport_key] = t
                    break
        print(f"    Found {len(tag_map)} sport tags: {list(tag_map.keys())[:10]}...")

        # Fetch events for target sports
        target_sports = ["epl", "bun", "nba", "nhl"]
        for sport in target_sports:
            tag_id = tag_map.get(sport)
            if not tag_id:
                continue
            resp = requests.get(
                f"{GAMMA_URL}/events",
                params={"active": "true", "closed": "false", "limit": 200, "tag_id": tag_id},
                timeout=10,
            )
            if resp.status_code != 200:
                continue
            events = resp.json()
            for event in events:
                slug = event.get("slug", "")
                for m in event.get("markets", [event]):
                    parsed = _parse_pm_market(m, sport)
                    if parsed:
                        all_markets.append(parsed)
            print(f"    {sport}: {len([m for m in all_markets if m['sport'] == sport])} markets")
            time.sleep(0.3)
    except Exception as e:
        print(f"    ERROR fetching Polymarket: {e}")

    # Also try direct markets endpoint as fallback
    try:
        resp = requests.get(
            f"{GAMMA_URL}/markets",
            params={"closed": "false", "active": "true", "limit": 500},
            timeout=15,
        )
        if resp.status_code == 200:
            for m in resp.json():
                q = m.get("question", "").lower()
                slug = m.get("slug", "").lower()
                # Filter for sports-related markets
                if any(kw in q or kw in slug for kw in [
                    "win", "beat", "score", "goal", "spread", "total",
                    "nba", "nhl", "epl", "bundesliga", "premier league",
                    "basketball", "hockey", "soccer", "football",
                ]):
                    sport = _detect_sport(slug, q)
                    if sport:
                        parsed = _parse_pm_market(m, sport)
                        if parsed and not any(
                            x["condition_id"] == parsed["condition_id"] for x in all_markets
                        ):
                            all_markets.append(parsed)
    except Exception as e:
        print(f"    Fallback fetch error: {e}")

    print(f"    Total: {len(all_markets)} Polymarket sports markets")
    with open(cache_file, "w") as f:
        json.dump(all_markets, f, indent=2, default=str)
    return all_markets


def _parse_pm_market(m, sport):
    """Parse a raw Polymarket market dict."""
    outcomes_raw = m.get("outcomes", '["Yes","No"]')
    if isinstance(outcomes_raw, str):
        try:
            outcomes_raw = json.loads(outcomes_raw)
        except json.JSONDecodeError:
            return None

    prices_raw = m.get("outcomePrices", '["0.5","0.5"]')
    if isinstance(prices_raw, str):
        try:
            prices_raw = json.loads(prices_raw)
        except json.JSONDecodeError:
            return None
    prices = [float(p) for p in prices_raw]

    token_ids_raw = m.get("clobTokenIds", "[]")
    if isinstance(token_ids_raw, str):
        try:
            token_ids_raw = json.loads(token_ids_raw)
        except json.JSONDecodeError:
            token_ids_raw = []

    return {
        "condition_id": m.get("conditionId", m.get("condition_id", "")),
        "question": m.get("question", ""),
        "slug": m.get("slug", ""),
        "sport": sport,
        "outcomes": outcomes_raw,
        "prices": prices,
        "token_ids": token_ids_raw,
        "volume_24h": float(m.get("volume24hr", m.get("volume24Hr", 0)) or 0),
        "liquidity": float(m.get("liquidity", 0) or 0),
        "end_date": m.get("endDate", m.get("end_date", "")),
    }


def _detect_sport(slug, question):
    """Detect sport from slug or question text."""
    slug = slug.lower()
    q = question.lower()
    if slug.startswith("epl-") or "premier league" in q:
        return "epl"
    if slug.startswith("bun-") or "bundesliga" in q:
        return "bun"
    if slug.startswith("nba-") or "nba" in q:
        return "nba"
    if slug.startswith("nhl-") or "nhl" in q:
        return "nhl"
    return None


def fetch_clob_orderbook(token_id):
    """Fetch orderbook from Polymarket CLOB for a specific token."""
    try:
        resp = requests.get(
            f"{CLOB_URL}/book",
            params={"token_id": token_id},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return None


# ── Matching ─────────────────────────────────────────────────────────────────

def match_events(pm_markets, odds_events_by_sport):
    """Match Polymarket markets to Odds API events."""
    matches = []

    sport_map = {
        "epl": "soccer_epl",
        "bun": "soccer_germany_bundesliga",
        "nba": "basketball_nba",
        "nhl": "icehockey_nhl",
    }

    for pm in pm_markets:
        sport = pm["sport"]
        odds_key = sport_map.get(sport)
        if not odds_key or odds_key not in odds_events_by_sport:
            continue

        slug = pm.get("slug", "")
        question = pm.get("question", "")

        # Extract team abbreviations from slug
        parts = slug.split("-")
        if len(parts) < 3:
            continue
        pm_teams = [parts[1], parts[2]]

        for odds_event in odds_events_by_sport[odds_key]:
            home = odds_event.get("home_team", "")
            away = odds_event.get("away_team", "")

            # Match both teams
            home_matched = False
            away_matched = False
            for pt in pm_teams:
                pt_lower = pt.lower().strip()
                canonical = _ABBREV_LOOKUP.get(pt_lower)
                if canonical:
                    if canonical == normalize_team(home):
                        home_matched = True
                    elif canonical == normalize_team(away):
                        away_matched = True
                else:
                    if fuzzy_match(pt_lower, home, 0.6):
                        home_matched = True
                    elif fuzzy_match(pt_lower, away, 0.6):
                        away_matched = True

            if home_matched and away_matched:
                matches.append({
                    "polymarket": pm,
                    "odds_event": odds_event,
                    "sport": sport,
                })
                break

    return matches


# ── Edge Calculation ─────────────────────────────────────────────────────────

def calculate_edges(matches):
    """Calculate edges for all matched markets using multiple overround methods."""
    results = []

    for match in matches:
        pm = match["polymarket"]
        event = match["odds_event"]
        sport = match["sport"]

        # Find sharpest bookmaker
        bookmakers = event.get("bookmakers", [])
        sharp_book = None
        for pref in SHARP_BOOKS:
            for bm in bookmakers:
                if bm["key"] == pref:
                    sharp_book = bm
                    break
            if sharp_book:
                break
        if not sharp_book and bookmakers:
            sharp_book = bookmakers[0]
        if not sharp_book:
            continue

        # Process h2h market
        for market in sharp_book.get("markets", []):
            if market["key"] != "h2h":
                continue

            outcomes = market.get("outcomes", [])
            if len(outcomes) < 2:
                continue

            # Raw implied probabilities
            raw_probs = []
            outcome_names = []
            decimal_odds_list = []
            for o in outcomes:
                dec = o["price"]
                ip = 1.0 / dec if dec > 0 else 0
                raw_probs.append(ip)
                outcome_names.append(o["name"])
                decimal_odds_list.append(dec)

            overround = sum(raw_probs) - 1.0

            # Apply all four methods
            fair_proportional, _ = remove_overround_proportional(raw_probs)
            fair_shin, shin_z = remove_overround_shin(raw_probs)
            fair_power, power_k = remove_overround_power(raw_probs)
            fair_odds_ratio, or_c = remove_overround_odds_ratio(raw_probs)

            # Match Polymarket outcomes to odds outcomes
            for i, pm_outcome in enumerate(pm.get("outcomes", [])):
                pm_price = pm["prices"][i] if i < len(pm["prices"]) else None
                token_id = pm["token_ids"][i] if i < len(pm["token_ids"]) else None
                if pm_price is None:
                    continue

                # Find the corresponding odds outcome
                fair_idx = _match_outcome_to_odds(
                    pm_outcome, pm.get("question", ""),
                    outcome_names, event["home_team"], event["away_team"]
                )
                if fair_idx is None:
                    continue

                result = {
                    "sport": sport,
                    "slug": pm["slug"],
                    "question": pm["question"],
                    "pm_outcome": pm_outcome,
                    "pm_price": pm_price,
                    "token_id": token_id,
                    "bookmaker": sharp_book["key"],
                    "odds_outcome": outcome_names[fair_idx],
                    "decimal_odds": decimal_odds_list[fair_idx],
                    "raw_implied": raw_probs[fair_idx],
                    "overround": overround,
                    "fair_proportional": fair_proportional[fair_idx],
                    "fair_shin": fair_shin[fair_idx],
                    "fair_power": fair_power[fair_idx],
                    "fair_odds_ratio": fair_odds_ratio[fair_idx],
                    "shin_z": shin_z if isinstance(shin_z, float) else 0,
                    "edge_raw": (raw_probs[fair_idx] - pm_price),
                    "edge_proportional": (fair_proportional[fair_idx] - pm_price),
                    "edge_shin": (fair_shin[fair_idx] - pm_price),
                    "edge_power": (fair_power[fair_idx] - pm_price),
                    "edge_odds_ratio": (fair_odds_ratio[fair_idx] - pm_price),
                    "edge_pct_proportional": ((fair_proportional[fair_idx] - pm_price) / pm_price * 100) if pm_price > 0 else 0,
                    "edge_pct_shin": ((fair_shin[fair_idx] - pm_price) / pm_price * 100) if pm_price > 0 else 0,
                    "n_outcomes": len(outcomes),
                    "is_favourite": pm_price > 0.5,
                    "commence_time": event.get("commence_time", ""),
                    "volume_24h": pm.get("volume_24h", 0),
                    "liquidity": pm.get("liquidity", 0),
                }
                results.append(result)

    return results


def _match_outcome_to_odds(pm_outcome, question, odds_names, home, away):
    """Map a Polymarket outcome to an index in odds_names."""
    pm_lower = pm_outcome.lower().strip()

    # Direct team name match
    for i, name in enumerate(odds_names):
        if fuzzy_match(pm_lower, name.lower(), 0.7):
            return i

    # Draw
    if pm_lower == "draw":
        for i, name in enumerate(odds_names):
            if name.lower() == "draw":
                return i
        return None

    # Yes/No on "Will X win?" questions
    team_match = re.search(r"Will (.+?) (?:win|draw|beat)", question)
    if not team_match:
        return None

    question_team = team_match.group(1).strip()
    matched_idx = None
    for i, name in enumerate(odds_names):
        if fuzzy_match(question_team, name, 0.6):
            matched_idx = i
            break
    if matched_idx is None:
        if fuzzy_match(question_team, home, 0.6):
            for i, name in enumerate(odds_names):
                if fuzzy_match(name, home, 0.6):
                    matched_idx = i
                    break
        elif fuzzy_match(question_team, away, 0.6):
            for i, name in enumerate(odds_names):
                if fuzzy_match(name, away, 0.6):
                    matched_idx = i
                    break

    if matched_idx is None:
        return None

    if pm_lower == "yes":
        return matched_idx
    elif pm_lower == "no":
        # Return the "other" outcome for 2-way, or None for 3-way
        if len(odds_names) == 2:
            return 1 - matched_idx
        # For 3-way (soccer), "No" maps to draw+other = 1 - team_prob
        # but we can't return a single index. Skip.
        return None

    return None


# ── Orderbook Analysis ───────────────────────────────────────────────────────

def analyze_orderbooks(edges_with_tokens, max_checks=20):
    """Check orderbook depth for markets with edges."""
    print("\n" + "=" * 70)
    print("ORDERBOOK DEPTH ANALYSIS")
    print("=" * 70)

    # Filter to edges > 3% (tradeable)
    tradeable = [e for e in edges_with_tokens if e["edge_pct_proportional"] > 3.0]
    tradeable.sort(key=lambda x: -x["edge_pct_proportional"])
    to_check = tradeable[:max_checks]

    if not to_check:
        print("No edges > 3% to check orderbooks for.")
        return []

    print(f"Checking orderbooks for top {len(to_check)} edges (>3%)...\n")

    orderbook_results = []
    for edge in to_check:
        token_id = edge.get("token_id")
        if not token_id:
            continue

        ob = fetch_clob_orderbook(token_id)
        if not ob:
            continue

        bids = ob.get("bids", [])
        asks = ob.get("asks", [])

        # Calculate depth
        bid_depth = sum(float(b.get("size", 0)) for b in bids[:5])
        ask_depth = sum(float(a.get("size", 0)) for a in asks[:5])
        best_bid = float(bids[0]["price"]) if bids else 0
        best_ask = float(asks[0]["price"]) if asks else 1
        spread = best_ask - best_bid

        result = {
            "slug": edge["slug"],
            "pm_outcome": edge["pm_outcome"],
            "edge_pct": edge["edge_pct_proportional"],
            "best_bid": best_bid,
            "best_ask": best_ask,
            "spread": spread,
            "bid_depth_5": bid_depth,
            "ask_depth_5": ask_depth,
            "n_bid_levels": len(bids),
            "n_ask_levels": len(asks),
        }
        orderbook_results.append(result)

        print(f"  {edge['slug'][:50]} [{edge['pm_outcome']}]")
        print(f"    Edge: +{edge['edge_pct_proportional']:.1f}% | "
              f"Bid/Ask: {best_bid:.3f}/{best_ask:.3f} | "
              f"Spread: {spread:.3f} ({spread / best_ask * 100:.1f}%) | "
              f"Depth(5): {bid_depth:.0f}/{ask_depth:.0f}")

        time.sleep(0.2)

    if orderbook_results:
        avg_spread = sum(r["spread"] for r in orderbook_results) / len(orderbook_results)
        avg_bid_depth = sum(r["bid_depth_5"] for r in orderbook_results) / len(orderbook_results)
        avg_ask_depth = sum(r["ask_depth_5"] for r in orderbook_results) / len(orderbook_results)
        print(f"\n  Summary ({len(orderbook_results)} markets):")
        print(f"    Avg spread: {avg_spread:.3f} ({avg_spread * 100:.1f}%)")
        print(f"    Avg bid depth (top 5): {avg_bid_depth:.0f} shares")
        print(f"    Avg ask depth (top 5): {avg_ask_depth:.0f} shares")
        wide_spreads = sum(1 for r in orderbook_results if r["spread"] > 0.05)
        print(f"    Markets with spread > 5c: {wide_spreads}/{len(orderbook_results)}")

    return orderbook_results


# ── Analysis & Reporting ─────────────────────────────────────────────────────

def print_summary(edges):
    """Print comprehensive edge quality analysis."""
    print("\n" + "=" * 70)
    print("EDGE QUALITY ANALYSIS REPORT")
    print(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    if not edges:
        print("\nNo matched markets found. Possible reasons:")
        print("  - No active Polymarket sports markets right now")
        print("  - Team name matching failed")
        print("  - Off-season for target sports")
        return

    # ── Basic Stats ──────────────────────────────────────────────────
    print(f"\n--- MATCHING SUMMARY ---")
    print(f"Total matched outcome-pairs: {len(edges)}")
    by_sport = {}
    for e in edges:
        by_sport.setdefault(e["sport"], []).append(e)
    for sport, items in sorted(by_sport.items()):
        print(f"  {sport}: {len(items)} outcomes")

    # ── Overround Analysis ───────────────────────────────────────────
    print(f"\n--- OVERROUND ANALYSIS ---")
    # Deduplicate by event (use slug as proxy)
    seen_events = set()
    unique_overrounds = []
    by_book_overround = {}
    for e in edges:
        key = (e["slug"], e["bookmaker"])
        if key not in seen_events:
            seen_events.add(key)
            unique_overrounds.append(e["overround"])
            by_book_overround.setdefault(e["bookmaker"], []).append(e["overround"])

    if unique_overrounds:
        avg_or = sum(unique_overrounds) / len(unique_overrounds)
        print(f"Average overround: {avg_or * 100:.2f}%")
        print(f"Range: [{min(unique_overrounds) * 100:.2f}%, {max(unique_overrounds) * 100:.2f}%]")
        for book, ors in sorted(by_book_overround.items()):
            avg = sum(ors) / len(ors) * 100
            print(f"  {book}: {avg:.2f}% avg overround ({len(ors)} events)")

    # ── Edge Distribution ────────────────────────────────────────────
    print(f"\n--- EDGE DISTRIBUTION (Proportional Method) ---")
    _print_edge_distribution(edges, "edge_pct_proportional", "Proportional")

    print(f"\n--- EDGE DISTRIBUTION (Shin's Method) ---")
    _print_edge_distribution(edges, "edge_pct_shin", "Shin's")

    # ── Method Comparison ────────────────────────────────────────────
    print(f"\n--- OVERROUND METHOD COMPARISON ---")
    print(f"{'Method':<16} {'Mean Edge%':>10} {'Median':>8} {'Std':>8} {'Max':>8} {'N>3%':>6} {'N>5%':>6}")
    print("-" * 72)
    for method_name, key in [
        ("Raw", "edge_raw"),
        ("Proportional", "edge_proportional"),
        ("Shin's", "edge_shin"),
        ("Power", "edge_power"),
        ("Odds-Ratio", "edge_odds_ratio"),
    ]:
        edge_pcts = [(e[key] / e["pm_price"] * 100) if e["pm_price"] > 0 else 0 for e in edges]
        mean_e = sum(edge_pcts) / len(edge_pcts)
        sorted_e = sorted(edge_pcts)
        median_e = sorted_e[len(sorted_e) // 2]
        std_e = math.sqrt(sum((x - mean_e) ** 2 for x in edge_pcts) / len(edge_pcts))
        max_e = max(edge_pcts)
        n_3 = sum(1 for x in edge_pcts if x > 3)
        n_5 = sum(1 for x in edge_pcts if x > 5)
        print(f"{method_name:<16} {mean_e:>+10.2f} {median_e:>+8.2f} {std_e:>8.2f} {max_e:>+8.2f} {n_3:>6} {n_5:>6}")

    # ── Shin's z analysis ────────────────────────────────────────────
    shin_zs = [e["shin_z"] for e in edges if e["shin_z"] > 0]
    if shin_zs:
        print(f"\n--- SHIN'S MODEL DIAGNOSTICS ---")
        avg_z = sum(shin_zs) / len(shin_zs)
        print(f"Average Shin's z (insider fraction): {avg_z:.4f}")
        print(f"Range: [{min(shin_zs):.4f}, {max(shin_zs):.4f}]")
        print(f"Interpretation: ~{avg_z * 100:.2f}% of trading is informed")

    # ── Favourite-Longshot Bias ──────────────────────────────────────
    print(f"\n--- FAVOURITE-LONGSHOT BIAS IN POLYMARKET ---")
    favourites = [e for e in edges if e["is_favourite"]]
    longshots = [e for e in edges if not e["is_favourite"]]

    if favourites:
        avg_fav_edge = sum(e["edge_pct_proportional"] for e in favourites) / len(favourites)
        print(f"Favourites (Poly price > 50c): n={len(favourites)}, avg edge={avg_fav_edge:+.2f}%")
    if longshots:
        avg_ls_edge = sum(e["edge_pct_proportional"] for e in longshots) / len(longshots)
        print(f"Longshots  (Poly price < 50c): n={len(longshots)}, avg edge={avg_ls_edge:+.2f}%")
    if favourites and longshots:
        fav_e = sum(e["edge_pct_proportional"] for e in favourites) / len(favourites)
        ls_e = sum(e["edge_pct_proportional"] for e in longshots) / len(longshots)
        diff = ls_e - fav_e
        print(f"Longshot premium: {diff:+.2f}% (positive = Poly overprices longshots)")
        if abs(fav_e - ls_e) > 1:
            # Check method sensitivity
            fav_shin = sum(e["edge_pct_shin"] for e in favourites) / len(favourites)
            ls_shin = sum(e["edge_pct_shin"] for e in longshots) / len(longshots)
            print(f"  After Shin's correction: fav={fav_shin:+.2f}%, longshot={ls_shin:+.2f}%")
            print(f"  Shin's reduces FLB impact by {abs(diff) - abs(ls_shin - fav_shin):.2f}%")

    # ── Price Bucket Analysis ────────────────────────────────────────
    print(f"\n--- EDGE BY PRICE BUCKET ---")
    buckets = [(0, 0.1), (0.1, 0.2), (0.2, 0.3), (0.3, 0.4), (0.4, 0.5),
               (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    print(f"{'Price Range':<14} {'Count':>6} {'Avg Edge%':>10} {'Avg |Edge|%':>12} {'N>3%':>6}")
    print("-" * 52)
    for lo, hi in buckets:
        bucket = [e for e in edges if lo <= e["pm_price"] < hi]
        if not bucket:
            continue
        avg_e = sum(e["edge_pct_proportional"] for e in bucket) / len(bucket)
        avg_abs = sum(abs(e["edge_pct_proportional"]) for e in bucket) / len(bucket)
        n3 = sum(1 for e in bucket if e["edge_pct_proportional"] > 3)
        print(f"[{lo:.1f}, {hi:.1f})     {len(bucket):>6} {avg_e:>+10.2f} {avg_abs:>12.2f} {n3:>6}")

    # ── Sport Breakdown ──────────────────────────────────────────────
    print(f"\n--- EDGE BY SPORT ---")
    print(f"{'Sport':<12} {'Count':>6} {'Avg Edge%':>10} {'Max Edge%':>10} {'Avg OR%':>8} {'N>3%':>6}")
    print("-" * 60)
    for sport, items in sorted(by_sport.items()):
        avg_e = sum(e["edge_pct_proportional"] for e in items) / len(items)
        max_e = max(e["edge_pct_proportional"] for e in items)
        avg_or = sum(e["overround"] for e in items) / len(items) * 100
        n3 = sum(1 for e in items if e["edge_pct_proportional"] > 3)
        print(f"{sport:<12} {len(items):>6} {avg_e:>+10.2f} {max_e:>+10.2f} {avg_or:>8.2f} {n3:>6}")

    # ── Top Opportunities ────────────────────────────────────────────
    print(f"\n--- TOP 20 EDGES (by proportional method) ---")
    sorted_edges = sorted(edges, key=lambda x: -x["edge_pct_proportional"])
    print(f"{'#':>3} {'Sport':<6} {'Outcome':<30} {'Poly':>6} {'Fair':>6} {'Edge%':>7} {'Book':<12} {'OR%':>6}")
    print("-" * 90)
    for i, e in enumerate(sorted_edges[:20]):
        label = f"{e['slug'][:22]} [{e['pm_outcome']}]"
        print(f"{i+1:>3} {e['sport']:<6} {label:<30} {e['pm_price']:>6.3f} "
              f"{e['fair_proportional']:>6.3f} {e['edge_pct_proportional']:>+7.1f} "
              f"{e['bookmaker']:<12} {e['overround']*100:>6.2f}")

    # ── Tradeable Opportunity Count ──────────────────────────────────
    print(f"\n--- TRADEABLE OPPORTUNITY SUMMARY ---")
    for thresh in [1, 2, 3, 5, 8, 10]:
        n_prop = sum(1 for e in edges if e["edge_pct_proportional"] > thresh)
        n_shin = sum(1 for e in edges if e["edge_pct_shin"] > thresh)
        # Only BUY-side matters (positive edge = underpriced on Poly)
        print(f"  Edge > {thresh}%: {n_prop} (proportional) / {n_shin} (Shin's)")

    # In the 5-40c range (RN1's sweet spot)
    sweet_spot = [e for e in edges if 0.05 <= e["pm_price"] <= 0.40 and e["edge_pct_proportional"] > 3]
    print(f"\n  In 5-40c range with >3% edge: {len(sweet_spot)} opportunities")
    if sweet_spot:
        avg_e = sum(e["edge_pct_proportional"] for e in sweet_spot) / len(sweet_spot)
        print(f"  Average edge in sweet spot: {avg_e:.1f}%")

    # ── Estimated Daily Opportunity ──────────────────────────────────
    print(f"\n--- ESTIMATED DAILY OPPORTUNITY ---")
    n_tradeable = sum(1 for e in edges if e["edge_pct_proportional"] > 3)
    # This is a snapshot; assume markets turn over ~3x/day for live sports
    est_daily = n_tradeable * 3
    avg_edge_tradeable = 0
    if n_tradeable > 0:
        avg_edge_tradeable = sum(
            e["edge_pct_proportional"] for e in edges if e["edge_pct_proportional"] > 3
        ) / n_tradeable
    bet_size = 15  # avg $15 bet at quarter-Kelly
    est_daily_ev = est_daily * bet_size * avg_edge_tradeable / 100
    print(f"  Current snapshot: {n_tradeable} edges > 3%")
    print(f"  Estimated daily opportunities (3x turnover): ~{est_daily}")
    print(f"  Avg edge on tradeable: {avg_edge_tradeable:.1f}%")
    print(f"  Est. daily EV at ${bet_size}/trade: ${est_daily_ev:.2f}")


def _print_edge_distribution(edges, key, label):
    """Print histogram-style edge distribution."""
    edge_pcts = [e[key] for e in edges]
    if not edge_pcts:
        print("  No data.")
        return

    mean_e = sum(edge_pcts) / len(edge_pcts)
    sorted_e = sorted(edge_pcts)
    median_e = sorted_e[len(sorted_e) // 2]
    std_e = math.sqrt(sum((x - mean_e) ** 2 for x in edge_pcts) / len(edge_pcts)) if len(edge_pcts) > 1 else 0
    max_e = max(edge_pcts)
    min_e = min(edge_pcts)

    print(f"  n={len(edge_pcts)} | mean={mean_e:+.2f}% | median={median_e:+.2f}% | "
          f"std={std_e:.2f}% | range=[{min_e:+.2f}%, {max_e:+.2f}%]")

    # Histogram bins
    bins = [(-100, -10), (-10, -5), (-5, -3), (-3, -1), (-1, 0), (0, 1), (1, 3), (3, 5), (5, 10), (10, 100)]
    print(f"  {'Bin':>12} {'Count':>6} {'Pct':>6}  Bar")
    for lo, hi in bins:
        count = sum(1 for x in edge_pcts if lo <= x < hi)
        pct = count / len(edge_pcts) * 100
        bar = "#" * int(pct / 2)
        label_str = f"[{lo:+d}%, {hi:+d}%)"
        print(f"  {label_str:>12} {count:>6} {pct:>5.1f}%  {bar}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    use_cache = "--use-cache" in sys.argv
    skip_clob = "--no-clob" in sys.argv

    print("=" * 70)
    print("POLYMARKET EDGE QUALITY ANALYSIS")
    print(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print(f"Mode: {'CACHED' if use_cache else 'LIVE'} data")
    print(f"Sports: {', '.join(SPORTS_TO_FETCH.values())}")
    print("=" * 70)

    # 1. Fetch Polymarket markets
    print("\n--- Step 1: Fetch Polymarket Sports Markets ---")
    pm_markets = fetch_polymarket_sports(use_cache=use_cache)

    # 2. Fetch sharp odds
    print("\n--- Step 2: Fetch Sharp Bookmaker Odds ---")
    odds_by_sport = {}
    for sport_key, display_name in SPORTS_TO_FETCH.items():
        events = fetch_odds_api(sport_key, use_cache=use_cache)
        if events:
            odds_by_sport[sport_key] = events
        if not use_cache:
            time.sleep(0.5)  # Rate limiting

    total_events = sum(len(v) for v in odds_by_sport.values())
    print(f"\n  Total odds events: {total_events}")

    # 3. Match and calculate edges
    print("\n--- Step 3: Match Markets & Calculate Edges ---")
    matches = match_events(pm_markets, odds_by_sport)
    print(f"  Matched {len(matches)} event pairs")

    edges = calculate_edges(matches)
    print(f"  Calculated {len(edges)} outcome edges")

    # Save edges to cache
    edges_file = CACHE_DIR / f"edges_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    with open(edges_file, "w") as f:
        json.dump(edges, f, indent=2, default=str)
    print(f"  Saved to {edges_file.name}")

    # 4. Print analysis
    print_summary(edges)

    # 5. Orderbook analysis
    if not skip_clob and edges:
        analyze_orderbooks(edges)

    # Save full report
    report_file = CACHE_DIR / f"report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.txt"
    print(f"\nReport data saved to: {CACHE_DIR}")


if __name__ == "__main__":
    main()
