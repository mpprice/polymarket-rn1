"""Match Polymarket markets to external odds and identify mispricings.

Improvements over V1:
- Strict team matching with canonical name database + abbrev lookup
- Spread/totals market support (not just h2h)
- Match validation logging for debugging false positives
- Proper Yes/No -> team mapping for "Will X win?" questions

V3: Integrates ``edge_model.EdgeCalculator`` for rigorous fair probability
    estimation using sport-specific overround removal (Shin, Power, MWPO),
    edge confidence scoring, time decay, and proper Kelly sizing.
    Backwards-compatible: still returns legacy edge dicts that strategy.py
    expects, but enriched with additional fields.
"""
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

log = logging.getLogger(__name__)

# ── Edge Model (optional, graceful degradation) ──────────────────────────────
_edge_calculator = None
try:
    from .edge_model import EdgeCalculator, EdgeModelConfig
    _edge_calculator = EdgeCalculator(EdgeModelConfig())
    log.info("EdgeCalculator loaded — using advanced overround removal and confidence scoring")
except ImportError:
    log.debug("edge_model not available, using legacy edge calculation")


# ── Team Name Database ──────────────────────────────────────────────────────
# canonical_name -> [aliases]
TEAM_ALIASES = {
    # EPL
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
    "burnley": ["burnley fc", "bur"],
    # Bundesliga
    "fc bayern münchen": ["bayern munich", "bayern", "bay"],
    "borussia dortmund": ["dortmund", "bvb", "dor"],
    "rb leipzig": ["leipzig", "rbl", "lei"],
    "bayer leverkusen": ["leverkusen", "lev", "b04"],
    "eintracht frankfurt": ["frankfurt", "sge", "ein"],
    "vfb stuttgart": ["stuttgart", "stu", "vfb"],
    "sc freiburg": ["freiburg", "fre", "scf"],
    "vfl wolfsburg": ["wolfsburg", "wob", "wol"],
    "borussia mönchengladbach": ["gladbach", "bmg", "mon"],
    "1. fc union berlin": ["union berlin", "uni", "fcub"],
    "sv werder bremen": ["werder bremen", "werder", "wer", "svw"],
    "tsg hoffenheim": ["hoffenheim", "hof", "tsg"],
    "fc augsburg": ["augsburg", "aug", "fca"],
    "1. fc heidenheim": ["heidenheim", "hei", "fch"],
    "fc st. pauli": ["st pauli", "pau", "stp"],
    "1. fc köln": ["köln", "cologne", "kol", "eff"],
    "1. fsv mainz 05": ["mainz", "mai", "m05"],
    # La Liga
    "fc barcelona": ["barcelona", "barca", "bar", "fcb"],
    "real madrid cf": ["real madrid", "mad", "rma"],
    "club atlético de madrid": ["atletico madrid", "atletico", "atm"],
    "real sociedad": ["sociedad", "rso", "soc"],
    "real betis balompié": ["real betis", "betis", "bet", "rbb"],
    "athletic club": ["athletic bilbao", "bilbao", "ath"],
    "villarreal cf": ["villarreal", "vil", "vcf"],
    "sevilla fc": ["sevilla", "sev", "sfc"],
    "rcd mallorca": ["mallorca", "mal", "rma"],
    "getafe cf": ["getafe", "get", "gcf"],
    "rayo vallecano de madrid": ["rayo vallecano", "rayo", "ray"],
    "ca osasuna": ["osasuna", "osa"],
    "elche cf": ["elche", "elc"],
    "real oviedo": ["oviedo", "ovi"],
    "rcd espanyol": ["espanyol", "esp"],
    "valencia cf": ["valencia", "val", "vcf"],
    "deportivo alavés": ["alaves", "ala"],
    "celta de vigo": ["celta vigo", "celta", "cel"],
    # Serie A
    "ssc napoli": ["napoli", "nap"],
    "ac milan": ["milan", "acm"],
    "inter milan": ["inter", "internazionale", "int"],
    "juventus fc": ["juventus", "juve", "juv"],
    "as roma": ["roma", "rom"],
    "ss lazio": ["lazio", "laz"],
    "atalanta bc": ["atalanta", "ata"],
    "acf fiorentina": ["fiorentina", "fio"],
    "torino fc": ["torino", "tor"],
    "bologna fc": ["bologna", "bol"],
    # Ligue 1
    "paris saint-germain": ["psg", "par"],
    "olympique de marseille": ["marseille", "om", "mar"],
    "olympique lyonnais": ["lyon", "ol", "lyo"],
    "as monaco": ["monaco", "mon", "asm"],
    "losc lille": ["lille", "lil", "los"],
    "stade rennais": ["rennes", "ren", "srf"],
    "rc lens": ["lens", "rcl"],
    "ogc nice": ["nice", "nic", "ogcn"],
    # UCL / UEL extras handled via the above
    # NBA
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
    "san antonio spurs": ["spurs", "sas"],
    "portland trail blazers": ["trail blazers", "por"],
    "chicago bulls": ["bulls", "chi"],
    "charlotte hornets": ["hornets", "cha"],
    "detroit pistons": ["pistons", "det"],
    "washington wizards": ["wizards", "was"],
    "utah jazz": ["jazz", "uta"],
    # NFL
    "kansas city chiefs": ["chiefs", "kc"],
    "buffalo bills": ["bills", "buf"],
    "baltimore ravens": ["ravens", "bal"],
    "pittsburgh steelers": ["steelers", "pit"],
    "dallas cowboys": ["cowboys", "dal"],
    "detroit lions": ["lions", "det"],
    "green bay packers": ["packers", "gb"],
    "philadelphia eagles": ["eagles", "phi"],
    "san francisco 49ers": ["49ers", "niners", "sf"],
    "new england patriots": ["patriots", "ne"],
    "seattle seahawks": ["seahawks", "sea"],
    "cincinnati bengals": ["bengals", "cin"],
    "denver broncos": ["broncos", "den"],
    "los angeles rams": ["rams", "lar"],
    "miami dolphins": ["dolphins", "mia"],
    "new york jets": ["jets", "nyj"],
    "new york giants": ["giants", "nyg"],
    "las vegas raiders": ["raiders", "lv"],
    "minnesota vikings": ["vikings", "min"],
    "tampa bay buccaneers": ["buccaneers", "bucs", "tb"],
    "carolina panthers": ["panthers", "car"],
    "new orleans saints": ["saints", "no"],
    "atlanta falcons": ["falcons", "atl"],
    "arizona cardinals": ["cardinals", "ari"],
    "los angeles chargers": ["chargers", "lac"],
    "houston texans": ["texans", "hou"],
    "indianapolis colts": ["colts", "ind"],
    "jacksonville jaguars": ["jaguars", "jax"],
    "tennessee titans": ["titans", "ten"],
    "cleveland browns": ["browns", "cle"],
    "washington commanders": ["commanders", "was"],
    "chicago bears": ["bears", "chi"],
}

# Build reverse lookup: alias -> canonical
_ALIAS_TO_CANONICAL: dict[str, str] = {}
# Also build abbrev -> canonical (3-4 letter codes)
_ABBREV_TO_CANONICAL: dict[str, str] = {}

for canonical, aliases in TEAM_ALIASES.items():
    canon_lower = canonical.lower()
    _ALIAS_TO_CANONICAL[canon_lower] = canonical
    for alias in aliases:
        alias_lower = alias.lower()
        _ALIAS_TO_CANONICAL[alias_lower] = canonical
        if len(alias) <= 4 and alias.isalpha():
            _ABBREV_TO_CANONICAL[alias_lower] = canonical


def normalize_team(name: str) -> str:
    """Normalize team name for matching."""
    lower = name.lower().strip()
    # Remove common suffixes
    for suffix in [" fc", " cf", " sc", " afc", " bc"]:
        if lower.endswith(suffix):
            lower = lower[:-len(suffix)].strip()
    return _ALIAS_TO_CANONICAL.get(lower, lower)


def _abbrev_to_canonical(abbrev: str) -> Optional[str]:
    """Look up a 2-4 letter abbreviation to a canonical team name."""
    return _ABBREV_TO_CANONICAL.get(abbrev.lower().strip())


def fuzzy_match(a: str, b: str, threshold: float = 0.7) -> bool:
    """Fuzzy string match using SequenceMatcher. Threshold raised from 0.6."""
    na = normalize_team(a)
    nb = normalize_team(b)
    # Exact match after normalization
    if na == nb:
        return True
    return SequenceMatcher(None, na, nb).ratio() >= threshold


# ── Market Type Detection ───────────────────────────────────────────────────

def classify_market_type(slug: str, question: str) -> str:
    """Classify a Polymarket market as h2h, spread, total, or exotic.

    Returns: 'h2h', 'spread', 'total', or 'exotic'
    """
    slug_l = slug.lower()
    q_l = question.lower()

    # Spread markets: slug contains "-spread-" or question mentions spread
    if "-spread-" in slug_l or "spread" in q_l:
        return "spread"

    # Totals/O-U markets
    if any(x in slug_l for x in ["-total-", "-ou-", "-over-", "-under-"]):
        return "total"
    if any(x in q_l for x in ["o/u ", "over/under", "total goals", "total points"]):
        return "total"

    # Exotic markets we skip
    if any(x in slug_l for x in ["-exact-", "-halftime-", "-more-market",
                                   "-1h-", "-2h-", "-btts-", "-corners-"]):
        return "exotic"

    # Default: h2h (moneyline / match winner)
    return "h2h"


def parse_spread_line(slug: str) -> Optional[float]:
    """Extract spread line from slug like 'epl-ars-che-2026-03-08-spread-home-2pt5'.

    Returns the spread as a float (e.g. 2.5, 1.5).
    """
    m = re.search(r'(\d+)pt(\d+)', slug)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 10.0
    m = re.search(r'spread[^0-9]*(\d+\.?\d*)', slug)
    if m:
        return float(m.group(1))
    return None


def parse_total_line(slug: str) -> Optional[float]:
    """Extract total line from slug like 'epl-ars-che-2026-03-08-total-2pt5'."""
    m = re.search(r'(\d+)pt(\d+)', slug)
    if m:
        return int(m.group(1)) + int(m.group(2)) / 10.0
    m = re.search(r'total[^0-9]*(\d+\.?\d*)', slug)
    if m:
        return float(m.group(1))
    return None


# ── Main Matching Logic ─────────────────────────────────────────────────────

def match_markets(
    poly_markets: list[dict],
    odds_events: dict[str, list[dict]],
) -> list[dict]:
    """Match Polymarket markets to external odds events.

    Supports h2h, spreads, and totals markets.
    Returns list of matched pairs with edge calculations.
    """
    matches = []
    match_log = []  # For validation

    for pm in poly_markets:
        sport = pm["sport"]
        if sport not in odds_events:
            continue

        slug = pm.get("slug", "")
        question = pm.get("question", "")

        # Classify market type
        mtype = classify_market_type(slug, question)
        if mtype == "exotic":
            continue

        # Extract team names from Polymarket slug/question
        pm_teams = _extract_teams_from_slug(slug, question)
        if not pm_teams:
            continue

        # Search for matching odds event
        for odds_event in odds_events[sport]:
            home = odds_event["home_team"]
            away = odds_event["away_team"]

            # Strict team matching
            team_match = _teams_match_strict(pm_teams, home, away, sport)
            if not team_match:
                continue

            # Check date proximity (within 2 days)
            if not _dates_close(pm.get("end_date", ""), odds_event.get("commence_time", "")):
                continue

            # Found a match - calculate edges based on market type
            edges = _calculate_edges(pm, odds_event, mtype, slug)
            if edges:
                matched = {
                    "polymarket": pm,
                    "odds_event": odds_event,
                    "edges": edges,
                    "market_type": mtype,
                    "match_detail": team_match,
                }
                matches.append(matched)
                match_log.append(f"{slug} -> {home} vs {away} [{mtype}]")
            break

    log.info("Matched %d Polymarket markets to external odds", len(matches))
    if match_log and len(match_log) <= 20:
        for m in match_log:
            log.debug("  MATCH: %s", m)
    return matches


def _extract_teams_from_slug(slug: str, question: str) -> list[str]:
    """Extract team identifiers from a Polymarket slug."""
    # Slugs look like: epl-ars-mun-2026-03-01-ars
    # or: epl-ars-mun-2026-03-01-spread-home-2pt5
    parts = slug.split("-")
    if len(parts) >= 4:
        # parts[0] = sport, parts[1] = team1_abbrev, parts[2] = team2_abbrev
        return [parts[1], parts[2]]
    # Fallback: extract from question
    match = re.search(r"Will (.+?) (?:win|draw|beat)", question)
    if match:
        return [match.group(1)]
    return []


def _teams_match_strict(pm_teams: list[str], home: str, away: str, sport: str) -> Optional[dict]:
    """Strict team matching. Returns match detail dict or None.

    Uses abbreviation lookup first (most Polymarket slugs use 3-letter codes),
    then falls back to fuzzy matching with higher threshold.
    """
    home_n = normalize_team(home)
    away_n = normalize_team(away)

    matched_home = False
    matched_away = False

    for pt in pm_teams:
        pt_stripped = pt.strip().lower()

        # 1. Try abbreviation lookup (most reliable for slug-based teams)
        canonical = _abbrev_to_canonical(pt_stripped)
        if canonical:
            if canonical == home_n or canonical == normalize_team(home):
                matched_home = True
                continue
            if canonical == away_n or canonical == normalize_team(away):
                matched_away = True
                continue
            # Check if canonical fuzzy-matches (handles slight spelling differences)
            if SequenceMatcher(None, canonical.lower(), home_n.lower()).ratio() >= 0.8:
                matched_home = True
                continue
            if SequenceMatcher(None, canonical.lower(), away_n.lower()).ratio() >= 0.8:
                matched_away = True
                continue

        # 2. Try direct normalization
        pt_n = normalize_team(pt_stripped)
        if pt_n == home_n:
            matched_home = True
            continue
        if pt_n == away_n:
            matched_away = True
            continue

        # 3. Fuzzy match with high threshold (0.7)
        if fuzzy_match(pt_stripped, home, 0.7):
            matched_home = True
            continue
        if fuzzy_match(pt_stripped, away, 0.7):
            matched_away = True
            continue

    # Require BOTH teams matched for reliable pairing
    # Single-team match is too error-prone (e.g., "orl" matches Orlando
    # but the second abbrev matches a different game's team)
    if matched_home and matched_away:
        return {"home_matched": True, "away_matched": True}
    # Allow single match only if we have just one team to match (e.g., "Will X win?")
    if len(pm_teams) == 1 and (matched_home or matched_away):
        return {"home_matched": matched_home, "away_matched": matched_away}
    return None


def _dates_close(poly_end: str, odds_commence: str, max_hours: int = 48) -> bool:
    """Check if two date strings are within max_hours of each other."""
    try:
        if not poly_end or not odds_commence:
            return True  # Can't verify, assume match
        pd_dt = datetime.fromisoformat(poly_end.replace("Z", "+00:00"))
        od_dt = datetime.fromisoformat(odds_commence.replace("Z", "+00:00"))
        return abs((pd_dt - od_dt).total_seconds()) < max_hours * 3600
    except (ValueError, TypeError):
        return True


# ── Edge Calculation ────────────────────────────────────────────────────────

def _calculate_edges(pm: dict, odds_event: dict, mtype: str, slug: str) -> list[dict]:
    """Calculate edge for each outcome based on market type."""
    if mtype == "h2h":
        return _calculate_h2h_edges(pm, odds_event)
    elif mtype == "spread":
        return _calculate_spread_edges(pm, odds_event, slug)
    elif mtype == "total":
        return _calculate_total_edges(pm, odds_event, slug)
    return []


def _calculate_h2h_edges(pm: dict, odds_event: dict) -> list[dict]:
    """Calculate edge for h2h (moneyline) markets.

    V3: When the EdgeCalculator is available, uses sport-specific overround
    removal (Shin for soccer, MWPO for US sports, etc.) and enriches edge
    dicts with confidence, decay, and EV metrics. Falls back to the legacy
    proportional normalization if the module is unavailable.
    """
    edges = []
    outcomes = odds_event.get("outcomes", {})
    sport = pm.get("sport", "")

    # ── Advanced path: use EdgeCalculator ─────────────────────────────────
    if _edge_calculator is not None:
        # Build decimal-odds map from the odds_event outcomes
        odds_map: dict[str, float] = {}
        for name, data in outcomes.items():
            dec_odds = data.get("decimal_odds", 0)
            if dec_odds > 0:
                odds_map[name] = dec_odds

        if odds_map:
            # Compute hours to start for time-decay
            hours_to_start = _hours_until(odds_event.get("commence_time", ""))

            # For each PM outcome, find matching fair prob via the advanced model,
            # then let EdgeCalculator do overround removal + confidence + decay
            for i, pm_outcome in enumerate(pm.get("outcomes", [])):
                pm_price = pm["prices"][i] if i < len(pm["prices"]) else None
                token_id = pm["token_ids"][i] if i < len(pm["token_ids"]) else None
                if pm_price is None or token_id is None:
                    continue

                # First resolve which odds outcome this PM outcome maps to
                fair_prob = _find_fair_prob_advanced(
                    pm_outcome, pm.get("question", ""), outcomes, odds_map,
                    odds_event["home_team"], odds_event["away_team"], sport,
                )
                if fair_prob is None:
                    continue

                result = _edge_calculator.calculate_edge_from_fair_prob(
                    outcome=pm_outcome,
                    token_id=token_id,
                    pm_price=pm_price,
                    fair_prob=fair_prob,
                    market_type="h2h",
                    sport=sport,
                    hours_to_start=hours_to_start,
                    liquidity_usd=pm.get("liquidity", 0),
                )
                if result is not None:
                    edges.append(_edge_calculator.result_to_legacy_dict(result))

            return [e for e in edges if abs(e["edge_pct"]) > 1.0]

    # ── Legacy path (proportional normalization) ──────────────────────────
    for i, pm_outcome in enumerate(pm.get("outcomes", [])):
        pm_price = pm["prices"][i] if i < len(pm["prices"]) else None
        token_id = pm["token_ids"][i] if i < len(pm["token_ids"]) else None
        if pm_price is None or token_id is None:
            continue

        fair_prob = _find_fair_prob(pm_outcome, pm.get("question", ""), outcomes,
                                    odds_event["home_team"], odds_event["away_team"])
        if fair_prob is None:
            continue

        edge = fair_prob - pm_price
        edge_pct = (edge / pm_price * 100) if pm_price > 0 else 0

        edges.append({
            "outcome": pm_outcome,
            "token_id": token_id,
            "polymarket_price": pm_price,
            "fair_prob": fair_prob,
            "edge": edge,
            "edge_pct": edge_pct,
            "side": "BUY" if edge > 0 else "SELL",
            "market_type": "h2h",
        })

    return [e for e in edges if abs(e["edge_pct"]) > 1.0]  # Filter noise (raised from 0.5)


def _calculate_spread_edges(pm: dict, odds_event: dict, slug: str) -> list[dict]:
    """Calculate edge for spread (handicap) markets.

    V3: Uses EdgeCalculator for confidence scoring and time decay when available.

    Polymarket spread slugs: ...-spread-home-2pt5 or ...-spread-away-1pt5
    The Odds API spreads: outcomes with point values like +2.5, -2.5
    """
    edges = []
    spread_outcomes = odds_event.get("spread_outcomes", {})
    if not spread_outcomes:
        return edges

    pm_spread = parse_spread_line(slug)
    if pm_spread is None:
        return edges

    # Determine if this is home or away spread
    is_home = "-home-" in slug.lower()
    is_away = "-away-" in slug.lower()
    sport = pm.get("sport", "")
    hours_to_start = _hours_until(odds_event.get("commence_time", "")) if _edge_calculator else 24.0

    for i, pm_outcome in enumerate(pm.get("outcomes", [])):
        pm_price = pm["prices"][i] if i < len(pm["prices"]) else None
        token_id = pm["token_ids"][i] if i < len(pm["token_ids"]) else None
        if pm_price is None or token_id is None:
            continue

        fair_prob = _find_spread_fair_prob(
            pm_outcome, pm_spread, is_home, is_away,
            spread_outcomes, odds_event["home_team"], odds_event["away_team"]
        )
        if fair_prob is None:
            continue

        # Use EdgeCalculator if available for confidence + decay enrichment
        if _edge_calculator is not None:
            result = _edge_calculator.calculate_edge_from_fair_prob(
                outcome=pm_outcome,
                token_id=token_id,
                pm_price=pm_price,
                fair_prob=fair_prob,
                market_type="spread",
                sport=sport,
                hours_to_start=hours_to_start,
                liquidity_usd=pm.get("liquidity", 0),
                line=pm_spread,
            )
            if result is not None:
                edges.append(_edge_calculator.result_to_legacy_dict(result))
            continue

        # Legacy fallback
        edge = fair_prob - pm_price
        edge_pct = (edge / pm_price * 100) if pm_price > 0 else 0

        edges.append({
            "outcome": pm_outcome,
            "token_id": token_id,
            "polymarket_price": pm_price,
            "fair_prob": fair_prob,
            "edge": edge,
            "edge_pct": edge_pct,
            "side": "BUY" if edge > 0 else "SELL",
            "market_type": "spread",
            "line": pm_spread,
        })

    return [e for e in edges if abs(e["edge_pct"]) > 1.0]


def _calculate_total_edges(pm: dict, odds_event: dict, slug: str) -> list[dict]:
    """Calculate edge for totals (over/under) markets.

    V3: Uses EdgeCalculator for confidence scoring and time decay when available.
    """
    edges = []
    total_outcomes = odds_event.get("total_outcomes", {})
    if not total_outcomes:
        return edges

    pm_total = parse_total_line(slug)
    if pm_total is None:
        return edges

    sport = pm.get("sport", "")
    hours_to_start = _hours_until(odds_event.get("commence_time", "")) if _edge_calculator else 24.0

    for i, pm_outcome in enumerate(pm.get("outcomes", [])):
        pm_price = pm["prices"][i] if i < len(pm["prices"]) else None
        token_id = pm["token_ids"][i] if i < len(pm["token_ids"]) else None
        if pm_price is None or token_id is None:
            continue

        fair_prob = _find_total_fair_prob(pm_outcome, pm_total, total_outcomes)
        if fair_prob is None:
            continue

        # Use EdgeCalculator if available
        if _edge_calculator is not None:
            result = _edge_calculator.calculate_edge_from_fair_prob(
                outcome=pm_outcome,
                token_id=token_id,
                pm_price=pm_price,
                fair_prob=fair_prob,
                market_type="total",
                sport=sport,
                hours_to_start=hours_to_start,
                liquidity_usd=pm.get("liquidity", 0),
                line=pm_total,
            )
            if result is not None:
                edges.append(_edge_calculator.result_to_legacy_dict(result))
            continue

        # Legacy fallback
        edge = fair_prob - pm_price
        edge_pct = (edge / pm_price * 100) if pm_price > 0 else 0

        edges.append({
            "outcome": pm_outcome,
            "token_id": token_id,
            "polymarket_price": pm_price,
            "fair_prob": fair_prob,
            "edge": edge,
            "edge_pct": edge_pct,
            "side": "BUY" if edge > 0 else "SELL",
            "market_type": "total",
            "line": pm_total,
        })

    return [e for e in edges if abs(e["edge_pct"]) > 1.0]


# ── Helper: hours until event ──────────────────────────────────────────────

def _hours_until(commence_time_iso: str) -> float:
    """Parse an ISO-8601 commence_time and return hours until that time.

    Returns 24.0 (neutral default) if parsing fails.
    """
    if not commence_time_iso:
        return 24.0
    try:
        ct = datetime.fromisoformat(commence_time_iso.replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        delta = (ct - now).total_seconds() / 3600.0
        return max(delta, 0.0)
    except (ValueError, TypeError):
        return 24.0


# ── Advanced Fair Prob (using EdgeCalculator overround removal) ──────────

def _find_fair_prob_advanced(
    pm_outcome: str,
    question: str,
    odds_outcomes: dict,
    odds_decimal_map: dict[str, float],
    home_team: str,
    away_team: str,
    sport: str,
) -> Optional[float]:
    """Map a PM outcome to a fair probability using the EdgeCalculator's
    sport-specific overround removal.

    This replaces the legacy ``_find_fair_prob`` which reads pre-computed
    ``fair_prob`` fields (computed via proportional normalization in
    ``OddsClient``). Instead, we re-derive fair probabilities using the
    correct method for the sport.
    """
    if _edge_calculator is None:
        return _find_fair_prob(pm_outcome, question, odds_outcomes, home_team, away_team)

    from .edge_model import OverroundRemoval, SPORT_OVERROUND_DEFAULTS, EdgeModelConfig

    pm_lower = pm_outcome.lower().strip()

    # Build ordered lists of names + implied probs from decimal odds
    names = list(odds_decimal_map.keys())
    implied = [1.0 / odds_decimal_map[n] if odds_decimal_map[n] > 0 else 0.0 for n in names]

    # Get sport-specific overround method
    method = SPORT_OVERROUND_DEFAULTS.get(sport, "proportional")
    fair_list = OverroundRemoval.remove(implied, method=method)

    fair_map: dict[str, float] = {}
    for i, name in enumerate(names):
        if i < len(fair_list):
            fair_map[name] = fair_list[i]

    # Direct match (team name outcome like "Mavericks", "Arsenal")
    for name, fp in fair_map.items():
        if fuzzy_match(pm_lower, name.lower(), 0.7):
            return fp

    # Draw
    if pm_lower == "draw":
        for name, fp in fair_map.items():
            if name.lower() == "draw":
                return fp
        return None

    # "Yes"/"No" on "Will X win?" questions
    import re as _re
    team_match = _re.search(r"Will (.+?) (?:win|beat)", question)
    if not team_match:
        return None

    question_team = team_match.group(1).strip()

    matched_odds_team = None
    for name in fair_map:
        if fuzzy_match(question_team, name, 0.6):
            matched_odds_team = name
            break
    if not matched_odds_team:
        if fuzzy_match(question_team, home_team, 0.6):
            for name in fair_map:
                if fuzzy_match(name, home_team, 0.6):
                    matched_odds_team = name
                    break
        elif fuzzy_match(question_team, away_team, 0.6):
            for name in fair_map:
                if fuzzy_match(name, away_team, 0.6):
                    matched_odds_team = name
                    break

    if not matched_odds_team:
        return None

    team_prob = fair_map.get(matched_odds_team)
    if team_prob is None:
        return None

    if pm_lower == "yes":
        return team_prob
    elif pm_lower == "no":
        return 1.0 - team_prob

    return None


# ── Fair Probability Finders (legacy) ─────────────────────────────────────

def _find_fair_prob(pm_outcome: str, question: str, odds_outcomes: dict,
                    home_team: str, away_team: str) -> Optional[float]:
    """Map a Polymarket h2h outcome to a fair probability from odds data.

    Improved: uses home/away team info for robust Yes/No mapping.
    """
    pm_lower = pm_outcome.lower().strip()

    # Direct match (team name outcome like "Mavericks", "Arsenal")
    for name, data in odds_outcomes.items():
        if fuzzy_match(pm_lower, name.lower(), 0.7):
            return data.get("fair_prob")

    # Draw
    if pm_lower == "draw":
        for name, data in odds_outcomes.items():
            if name.lower() == "draw":
                return data.get("fair_prob")
        return None

    # "Yes"/"No" on "Will X win?" questions
    team_match = re.search(r"Will (.+?) (?:win|beat)", question)
    if not team_match:
        return None

    question_team = team_match.group(1).strip()

    # Figure out which odds team corresponds to the question team
    matched_odds_team = None
    for name in odds_outcomes:
        if fuzzy_match(question_team, name, 0.6):
            matched_odds_team = name
            break
    # Try matching against home/away directly
    if not matched_odds_team:
        if fuzzy_match(question_team, home_team, 0.6):
            # Find the home team in odds outcomes
            for name in odds_outcomes:
                if fuzzy_match(name, home_team, 0.6):
                    matched_odds_team = name
                    break
        elif fuzzy_match(question_team, away_team, 0.6):
            for name in odds_outcomes:
                if fuzzy_match(name, away_team, 0.6):
                    matched_odds_team = name
                    break

    if not matched_odds_team:
        return None

    team_prob = odds_outcomes[matched_odds_team].get("fair_prob")
    if team_prob is None:
        return None

    if pm_lower == "yes":
        return team_prob
    elif pm_lower == "no":
        return 1.0 - team_prob

    return None


def _find_spread_fair_prob(
    pm_outcome: str, pm_spread: float, is_home: bool, is_away: bool,
    spread_outcomes: dict, home_team: str, away_team: str,
) -> Optional[float]:
    """Find fair probability for a spread market outcome.

    Spread outcomes from The Odds API look like:
    {"Home Team": {"point": -2.5, "fair_prob": 0.45}, "Away Team": {"point": 2.5, ...}}
    """
    pm_lower = pm_outcome.lower().strip()

    # Determine which team this spread refers to
    if is_home:
        target_team = home_team
    elif is_away:
        target_team = away_team
    else:
        return None

    # Find matching spread outcome
    for name, data in spread_outcomes.items():
        if not fuzzy_match(name, target_team, 0.6):
            continue

        odds_point = data.get("point", 0)
        fair_prob = data.get("fair_prob")
        if fair_prob is None:
            continue

        # Require exact spread line match to avoid phantom edges
        # (e.g. -1.5 vs -2.5 creates 30-65% fake edges)
        if abs(abs(odds_point) - pm_spread) > 0.01:
            continue

        # Map Yes/No or team name outcomes
        if pm_lower in ("yes", target_team.lower(), normalize_team(target_team).lower()):
            return fair_prob
        elif pm_lower == "no":
            return 1.0 - fair_prob

    return None


def _find_total_fair_prob(
    pm_outcome: str, pm_total: float, total_outcomes: dict,
) -> Optional[float]:
    """Find fair probability for a totals (over/under) market."""
    pm_lower = pm_outcome.lower().strip()

    for name, data in total_outcomes.items():
        odds_point = data.get("point", 0)
        fair_prob = data.get("fair_prob")
        if fair_prob is None:
            continue

        # Require exact total line match to avoid phantom edges
        # (e.g. O/U 1.5 vs 2.5 creates 30-37% fake edges)
        if abs(odds_point - pm_total) > 0.01:
            continue

        name_lower = name.lower()
        if pm_lower in ("over", "yes") and name_lower == "over":
            return fair_prob
        elif pm_lower in ("under", "no") and name_lower == "under":
            return fair_prob

    return None
