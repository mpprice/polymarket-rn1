"""Common fixtures for matcher tests."""
import pytest


@pytest.fixture
def epl_odds_event():
    """Arsenal vs Manchester United EPL match."""
    return {
        "home_team": "Arsenal",
        "away_team": "Manchester United",
        "commence_time": "2026-03-01T15:00:00Z",
        "outcomes": {
            "Arsenal": {"fair_prob": 0.55, "decimal_odds": 1.75},
            "Manchester United": {"fair_prob": 0.25, "decimal_odds": 3.80},
            "Draw": {"fair_prob": 0.20, "decimal_odds": 4.50},
        },
    }


@pytest.fixture
def nhl_odds_event():
    """Detroit Red Wings vs Florida Panthers NHL match."""
    return {
        "home_team": "Detroit Red Wings",
        "away_team": "Florida Panthers",
        "commence_time": "2026-03-10T19:00:00Z",
        "outcomes": {
            "Detroit Red Wings": {"fair_prob": 0.42, "decimal_odds": 2.30},
            "Florida Panthers": {"fair_prob": 0.58, "decimal_odds": 1.68},
        },
    }


@pytest.fixture
def tennis_odds_event():
    """Djokovic vs Sinner ATP match."""
    return {
        "home_team": "Novak Djokovic",
        "away_team": "Jannik Sinner",
        "commence_time": "2026-03-05T14:00:00Z",
        "outcomes": {
            "Novak Djokovic": {"fair_prob": 0.45, "decimal_odds": 2.15},
            "Jannik Sinner": {"fair_prob": 0.55, "decimal_odds": 1.78},
        },
    }


@pytest.fixture
def nba_odds_event():
    """Lakers vs Celtics NBA match."""
    return {
        "home_team": "Los Angeles Lakers",
        "away_team": "Boston Celtics",
        "commence_time": "2026-03-05T19:30:00Z",
        "outcomes": {
            "Los Angeles Lakers": {"fair_prob": 0.48, "decimal_odds": 2.05},
            "Boston Celtics": {"fair_prob": 0.52, "decimal_odds": 1.90},
        },
        "total_outcomes": {
            "Over": {"point": 226.5, "fair_prob": 0.52},
            "Under": {"point": 226.5, "fair_prob": 0.48},
        },
        "spread_outcomes": {
            "Los Angeles Lakers": {"point": 2.5, "fair_prob": 0.52},
            "Boston Celtics": {"point": -2.5, "fair_prob": 0.48},
        },
    }


@pytest.fixture
def pm_epl_h2h():
    """Polymarket EPL h2h market for Arsenal."""
    return {
        "sport": "epl",
        "slug": "epl-ars-mun-2026-03-01-ars",
        "question": "Will Arsenal win?",
        "outcomes": ["Yes", "No"],
        "prices": [0.50, 0.50],
        "token_ids": ["token_ars_yes", "token_ars_no"],
        "end_date": "2026-03-01T17:00:00Z",
    }


@pytest.fixture
def pm_nhl_h2h():
    """Polymarket NHL h2h market for Detroit."""
    return {
        "sport": "nhl",
        "slug": "nhl-det-fla-2026-03-10-det",
        "question": "Will Detroit Red Wings win?",
        "outcomes": ["Yes", "No"],
        "prices": [0.38, 0.62],
        "token_ids": ["token_det_yes", "token_det_no"],
        "end_date": "2026-03-10T22:00:00Z",
    }


@pytest.fixture
def pm_tennis():
    """Polymarket ATP tennis market."""
    return {
        "sport": "atp",
        "slug": "atp-djokovic-sinner-2026-03-05",
        "question": "Will Novak Djokovic win?",
        "outcomes": ["Yes", "No"],
        "prices": [0.40, 0.60],
        "token_ids": ["token_djo_yes", "token_djo_no"],
        "end_date": "2026-03-15T23:59:00Z",  # tournament end, not match
    }


@pytest.fixture
def pm_nba_total():
    """Polymarket NBA totals market."""
    return {
        "sport": "nba",
        "slug": "nba-lal-bos-2026-03-05-total-226pt5",
        "question": "Will the total be over 226.5?",
        "outcomes": ["Over", "Under"],
        "prices": [0.48, 0.52],
        "token_ids": ["token_over", "token_under"],
        "end_date": "2026-03-05T22:00:00Z",
    }


@pytest.fixture
def pm_epl_spread():
    """Polymarket EPL spread market."""
    return {
        "sport": "epl",
        "slug": "epl-ars-che-2026-03-08-spread-home-2pt5",
        "question": "Will Arsenal cover -2.5?",
        "outcomes": ["Yes", "No"],
        "prices": [0.35, 0.65],
        "token_ids": ["token_spread_yes", "token_spread_no"],
        "end_date": "2026-03-08T17:00:00Z",
    }
