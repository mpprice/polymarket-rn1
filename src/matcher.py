"""Match Polymarket markets to external odds and identify mispricings."""
import logging
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional

log = logging.getLogger(__name__)


# Common team name normalization
TEAM_ALIASES = {
    "manchester united": ["man utd", "man united", "mufc"],
    "manchester city": ["man city", "mcfc"],
    "tottenham hotspur": ["tottenham", "spurs"],
    "wolverhampton wanderers": ["wolves"],
    "brighton and hove albion": ["brighton"],
    "west ham united": ["west ham"],
    "nottingham forest": ["nott forest", "nottm forest"],
    "newcastle united": ["newcastle"],
    "leicester city": ["leicester"],
    "fc bayern münchen": ["bayern munich", "bayern"],
    "borussia dortmund": ["dortmund", "bvb"],
    "rb leipzig": ["leipzig"],
    "bayer leverkusen": ["leverkusen"],
    "fc barcelona": ["barcelona", "barca"],
    "real madrid cf": ["real madrid"],
    "club atlético de madrid": ["atletico madrid", "atletico"],
    "ssc napoli": ["napoli"],
    "ac milan": ["milan"],
    "inter milan": ["inter", "internazionale"],
    "juventus fc": ["juventus", "juve"],
    "paris saint-germain": ["psg"],
    "olympique de marseille": ["marseille", "om"],
}

# Build reverse lookup
_ALIAS_TO_CANONICAL = {}
for canonical, aliases in TEAM_ALIASES.items():
    _ALIAS_TO_CANONICAL[canonical.lower()] = canonical
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def normalize_team(name: str) -> str:
    """Normalize team name for matching."""
    lower = name.lower().strip()
    # Remove common suffixes
    for suffix in [" fc", " cf", " sc", " afc"]:
        lower = lower.replace(suffix, "")
    lower = lower.strip()
    return _ALIAS_TO_CANONICAL.get(lower, lower)


def fuzzy_match(a: str, b: str, threshold: float = 0.6) -> bool:
    """Fuzzy string match using SequenceMatcher."""
    return SequenceMatcher(None, normalize_team(a), normalize_team(b)).ratio() >= threshold


def match_markets(
    poly_markets: list[dict],
    odds_events: dict[str, list[dict]],
) -> list[dict]:
    """Match Polymarket markets to external odds events.

    Returns list of matched pairs with edge calculations.
    """
    matches = []

    for pm in poly_markets:
        sport = pm["sport"]
        if sport not in odds_events:
            continue

        slug = pm.get("slug", "")
        question = pm.get("question", "")

        # Extract team names from Polymarket slug/question
        pm_teams = _extract_teams_from_slug(slug, question)
        if not pm_teams:
            continue

        # Search for matching odds event
        for odds_event in odds_events[sport]:
            home = odds_event["home_team"]
            away = odds_event["away_team"]

            # Check if teams match
            if not _teams_match(pm_teams, home, away):
                continue

            # Check date proximity (within 2 days)
            if not _dates_close(pm.get("end_date", ""), odds_event.get("commence_time", "")):
                continue

            # Found a match - calculate edges
            edges = _calculate_edges(pm, odds_event)
            if edges:
                matches.append({
                    "polymarket": pm,
                    "odds_event": odds_event,
                    "edges": edges,
                })
            break

    log.info("Matched %d Polymarket markets to external odds", len(matches))
    return matches


def _extract_teams_from_slug(slug: str, question: str) -> list[str]:
    """Extract team identifiers from a Polymarket slug."""
    # Slugs look like: epl-ars-mun-2026-03-01-ars
    # or question: "Will Arsenal FC win on 2026-03-01?"
    parts = slug.split("-")
    if len(parts) >= 4:
        return [parts[1], parts[2]]  # team abbreviations
    # Fallback: extract from question
    match = re.search(r"Will (.+?) (?:win|draw)", question)
    if match:
        return [match.group(1)]
    return []


def _teams_match(pm_teams: list[str], home: str, away: str) -> bool:
    """Check if Polymarket teams match an odds event."""
    home_n = normalize_team(home)
    away_n = normalize_team(away)
    for pt in pm_teams:
        pt_n = normalize_team(pt)
        if fuzzy_match(pt_n, home_n, 0.5) or fuzzy_match(pt_n, away_n, 0.5):
            return True
        # Try 3-letter abbreviation match
        if len(pt) <= 4:
            if pt.lower() in home_n.lower()[:4] or pt.lower() in away_n.lower()[:4]:
                return True
    return False


def _dates_close(poly_end: str, odds_commence: str, max_hours: int = 48) -> bool:
    """Check if two date strings are within max_hours of each other."""
    try:
        if not poly_end or not odds_commence:
            return True  # Can't verify, assume match
        # Parse ISO dates
        pd = datetime.fromisoformat(poly_end.replace("Z", "+00:00"))
        od = datetime.fromisoformat(odds_commence.replace("Z", "+00:00"))
        return abs((pd - od).total_seconds()) < max_hours * 3600
    except (ValueError, TypeError):
        return True


def _calculate_edges(pm: dict, odds_event: dict) -> list[dict]:
    """Calculate edge for each outcome.

    Edge = fair_prob - polymarket_price (positive = buy opportunity)
    """
    edges = []
    outcomes = odds_event.get("outcomes", {})

    for i, pm_outcome in enumerate(pm.get("outcomes", [])):
        pm_price = pm["prices"][i] if i < len(pm["prices"]) else None
        token_id = pm["token_ids"][i] if i < len(pm["token_ids"]) else None
        if pm_price is None or token_id is None:
            continue

        # Find matching odds outcome
        fair_prob = _find_fair_prob(pm_outcome, pm.get("question", ""), outcomes)
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
        })

    return [e for e in edges if abs(e["edge_pct"]) > 0.5]  # Filter noise


def _find_fair_prob(pm_outcome: str, question: str, odds_outcomes: dict) -> Optional[float]:
    """Map a Polymarket outcome to a fair probability from odds data."""
    pm_lower = pm_outcome.lower()

    # Direct match
    for name, data in odds_outcomes.items():
        if fuzzy_match(pm_lower, name.lower(), 0.6):
            return data.get("fair_prob")

    # "Yes"/"No" on "Will X win?" questions
    if pm_lower == "yes":
        team_match = re.search(r"Will (.+?) (?:win|beat)", question)
        if team_match:
            team = team_match.group(1)
            for name, data in odds_outcomes.items():
                if fuzzy_match(team, name, 0.5):
                    return data.get("fair_prob")

    if pm_lower == "no":
        team_match = re.search(r"Will (.+?) (?:win|beat)", question)
        if team_match:
            team = team_match.group(1)
            for name, data in odds_outcomes.items():
                if fuzzy_match(team, name, 0.5):
                    return 1.0 - data.get("fair_prob", 0.5)

    # Draw
    if pm_lower == "draw":
        for name, data in odds_outcomes.items():
            if name.lower() == "draw":
                return data.get("fair_prob")

    return None
