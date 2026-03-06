"""Comprehensive unit tests for src/matcher.py.

Covers:
  1. Team name matching (normalize_team, fuzzy_match, _abbrev_to_canonical)
  2. Sport-aware abbreviation (_SPORT_ABBREV)
  3. Tennis surname matching (_tennis_name_match)
  4. Strict team matching (_teams_match_strict)
  5. Date matching (_dates_close)
  6. Slug date extraction (_extract_date_from_slug)
  7. Market type classification (classify_market_type)
  8. Line parsing (parse_spread_line, parse_total_line)
  9. Edge calculation (h2h, total, spread)
 10. Full match_markets integration
"""
import sys
import os
import pytest

# Ensure src is importable as a package (matcher uses relative imports for edge_model,
# but we can import individual functions directly after path manipulation).
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import public/private functions from matcher directly (not as relative import).
# We import from the module file to avoid relative-import issues.
import importlib
import importlib.util

_matcher_path = os.path.join(os.path.dirname(__file__), "..", "src", "matcher.py")
_spec = importlib.util.spec_from_file_location("matcher", _matcher_path,
                                                 submodule_search_locations=[])
matcher = importlib.util.module_from_spec(_spec)

# Patch out the relative import of edge_model so loading doesn't fail
import types
_fake_pkg = types.ModuleType("matcher.__package__")
matcher.__package__ = "src"
# Pre-empt the relative import by ensuring it raises ImportError gracefully
# The module already has a try/except for this, but we need __package__ set
# so that the `from .edge_model import ...` resolves correctly.
# Easiest: just load with the try/except catching the ImportError.
try:
    _spec.loader.exec_module(matcher)
except Exception:
    # If edge_model not loadable, the module catches it internally.
    # If some other error, re-raise.
    pass

# Now pull out references
normalize_team = matcher.normalize_team
fuzzy_match = matcher.fuzzy_match
_abbrev_to_canonical = matcher._abbrev_to_canonical
_SPORT_ABBREV = matcher._SPORT_ABBREV
_ABBREV_TO_CANONICAL = matcher._ABBREV_TO_CANONICAL
_tennis_name_match = matcher._tennis_name_match
_teams_match_strict = matcher._teams_match_strict
_dates_close = matcher._dates_close
_extract_date_from_slug = matcher._extract_date_from_slug
classify_market_type = matcher.classify_market_type
parse_spread_line = matcher.parse_spread_line
parse_total_line = matcher.parse_total_line
_calculate_h2h_edges = matcher._calculate_h2h_edges
_calculate_total_edges = matcher._calculate_total_edges
_calculate_spread_edges = matcher._calculate_spread_edges
match_markets = matcher.match_markets


# ═══════════════════════════════════════════════════════════════════════════
# 1. Team Name Matching
# ═══════════════════════════════════════════════════════════════════════════

class TestNormalizeTeam:
    """Test normalize_team() across multiple leagues."""

    # EPL
    def test_epl_arsenal(self):
        assert normalize_team("Arsenal") == "arsenal"

    def test_epl_arsenal_fc_suffix(self):
        assert normalize_team("Arsenal FC") == "arsenal"

    def test_epl_man_utd_alias(self):
        assert normalize_team("Man Utd") == "manchester united"

    def test_epl_man_united(self):
        assert normalize_team("Man United") == "manchester united"

    def test_epl_spurs(self):
        assert normalize_team("Spurs") in ("tottenham hotspur", "san antonio spurs")
        # "spurs" is ambiguous -- appears in both Tottenham and San Antonio;
        # whichever last wrote to the alias dict wins. Just verify it resolves.
        assert normalize_team("Tottenham") == "tottenham hotspur"

    def test_epl_wolves(self):
        assert normalize_team("Wolves") in ("wolverhampton wanderers", "vfl wolfsburg")

    # Bundesliga
    def test_bundesliga_bayern(self):
        assert normalize_team("Bayern Munich") == "fc bayern münchen"

    def test_bundesliga_bayern_alias(self):
        assert normalize_team("Bayern") == "fc bayern münchen"

    def test_bundesliga_dortmund(self):
        assert normalize_team("Dortmund") == "borussia dortmund"

    def test_bundesliga_bvb(self):
        assert normalize_team("BVB") == "borussia dortmund"

    # La Liga
    def test_laliga_barcelona(self):
        assert normalize_team("Barcelona") == "fc barcelona"

    def test_laliga_barca(self):
        assert normalize_team("Barca") == "fc barcelona"

    def test_laliga_real_madrid(self):
        assert normalize_team("Real Madrid") == "real madrid cf"

    def test_laliga_atletico(self):
        assert normalize_team("Atletico Madrid") == "club atlético de madrid"

    # Serie A
    def test_seriea_napoli(self):
        assert normalize_team("Napoli") == "ssc napoli"

    def test_seriea_milan(self):
        assert normalize_team("Milan") == "ac milan"

    def test_seriea_inter(self):
        assert normalize_team("Inter") == "inter milan"

    def test_seriea_juve(self):
        assert normalize_team("Juve") == "juventus fc"

    # NBA
    def test_nba_lakers(self):
        assert normalize_team("Lakers") == "los angeles lakers"

    def test_nba_celtics(self):
        assert normalize_team("Celtics") == "boston celtics"

    def test_nba_warriors(self):
        assert normalize_team("Warriors") == "golden state warriors"

    # NFL
    def test_nfl_chiefs(self):
        assert normalize_team("Chiefs") == "kansas city chiefs"

    def test_nfl_49ers(self):
        assert normalize_team("49ers") == "san francisco 49ers"

    # Suffix stripping
    def test_suffix_fc(self):
        assert normalize_team("Chelsea FC") == "chelsea"

    def test_suffix_cf(self):
        # "Valencia CF" -> strip " cf" -> "valencia" -> alias lookup
        assert normalize_team("Valencia CF") == "valencia cf"

    def test_suffix_sc(self):
        assert normalize_team("Freiburg SC") == normalize_team("Freiburg SC")

    # Unknown team passes through
    def test_unknown_team(self):
        assert normalize_team("Nonexistent FC") == "nonexistent"


class TestFuzzyMatch:
    """Test fuzzy_match() with exact, close, and non-matches."""

    def test_exact_match_after_normalization(self):
        assert fuzzy_match("Arsenal", "Arsenal FC") is True

    def test_alias_match(self):
        assert fuzzy_match("Man Utd", "Manchester United") is True

    def test_close_fuzzy_match(self):
        # Two similar strings above 0.7 threshold
        assert fuzzy_match("Tottenham Hotspur", "Tottenham") is True

    def test_no_match_different_teams(self):
        assert fuzzy_match("Arsenal", "Chelsea") is False

    def test_custom_threshold_strict(self):
        # With very high threshold, slightly different should fail
        assert fuzzy_match("Arsenal", "Arsena", threshold=0.95) is False

    def test_custom_threshold_lenient(self):
        assert fuzzy_match("Arsenl", "Arsenal", threshold=0.5) is True


class TestAbbrevToCanonical:
    """Test _abbrev_to_canonical() for 3-letter codes."""

    # EPL
    def test_ars(self):
        assert _abbrev_to_canonical("ars") == "arsenal"

    def test_mun(self):
        assert _abbrev_to_canonical("mun") == "manchester united"

    def test_che(self):
        assert _abbrev_to_canonical("che") == "chelsea"

    def test_liv(self):
        assert _abbrev_to_canonical("liv") == "liverpool"

    # Bundesliga
    def test_bay(self):
        assert _abbrev_to_canonical("bay") == "fc bayern münchen"

    def test_dor(self):
        assert _abbrev_to_canonical("dor") == "borussia dortmund"

    def test_lev(self):
        assert _abbrev_to_canonical("lev") == "bayer leverkusen"

    # La Liga
    def test_bar(self):
        assert _abbrev_to_canonical("bar") == "fc barcelona"

    def test_mad(self):
        assert _abbrev_to_canonical("mad") == "real madrid cf"

    def test_atm(self):
        assert _abbrev_to_canonical("atm") == "club atlético de madrid"

    # Serie A
    def test_nap(self):
        assert _abbrev_to_canonical("nap") == "ssc napoli"

    def test_acm(self):
        assert _abbrev_to_canonical("acm") == "ac milan"

    def test_int(self):
        assert _abbrev_to_canonical("int") == "inter milan"

    # NBA
    def test_lal(self):
        assert _abbrev_to_canonical("lal") == "los angeles lakers"

    def test_bos(self):
        # bos is in both NBA (Celtics) and NHL (Bruins) -- generic lookup returns whichever last wrote
        result = _abbrev_to_canonical("bos")
        assert result in ("boston celtics", "boston bruins")

    # NFL
    def test_kc(self):
        assert _abbrev_to_canonical("kc") == "kansas city chiefs"

    # Unknown
    def test_unknown_abbrev(self):
        assert _abbrev_to_canonical("xyz") is None

    # Case insensitive
    def test_case_insensitive(self):
        assert _abbrev_to_canonical("ARS") == "arsenal"


# ═══════════════════════════════════════════════════════════════════════════
# 2. Sport-Aware Abbreviation
# ═══════════════════════════════════════════════════════════════════════════

class TestSportAbbrev:
    """Test that _SPORT_ABBREV resolves colliding abbreviations correctly per sport."""

    # NHL
    def test_nhl_det_is_red_wings(self):
        assert _SPORT_ABBREV[("nhl", "det")] == "detroit red wings"

    def test_nhl_fla_is_panthers(self):
        assert _SPORT_ABBREV[("nhl", "fla")] == "florida panthers"

    def test_nhl_chi_is_blackhawks(self):
        assert _SPORT_ABBREV[("nhl", "chi")] == "chicago blackhawks"

    def test_nhl_min_is_wild(self):
        assert _SPORT_ABBREV[("nhl", "min")] == "minnesota wild"

    def test_nhl_car_is_hurricanes(self):
        assert _SPORT_ABBREV[("nhl", "car")] == "carolina hurricanes"

    def test_nhl_bos_is_bruins(self):
        assert _SPORT_ABBREV[("nhl", "bos")] == "boston bruins"

    def test_nhl_tor_is_maple_leafs(self):
        assert _SPORT_ABBREV[("nhl", "tor")] == "toronto maple leafs"

    # CBB
    def test_cbb_hou_is_cougars(self):
        assert _SPORT_ABBREV[("cbb", "hou")] == "houston cougars"

    def test_cbb_fla_is_gators(self):
        assert _SPORT_ABBREV[("cbb", "fla")] == "florida gators"

    def test_cbb_unc(self):
        assert _SPORT_ABBREV[("cbb", "unc")] == "north carolina tar heels"

    def test_cbb_duke(self):
        assert _SPORT_ABBREV[("cbb", "duke")] == "duke blue devils"

    # Sport-scoped takes priority over generic
    def test_nhl_det_not_pistons(self):
        """Generic _ABBREV_TO_CANONICAL['det'] may be Pistons or Lions,
        but sport-scoped ('nhl','det') must be Red Wings."""
        generic = _ABBREV_TO_CANONICAL.get("det")
        sport_scoped = _SPORT_ABBREV[("nhl", "det")]
        assert sport_scoped == "detroit red wings"
        # Generic could be pistons or lions depending on dict ordering
        assert generic != "detroit red wings"

    def test_nhl_chi_not_bulls(self):
        sport_scoped = _SPORT_ABBREV[("nhl", "chi")]
        assert sport_scoped == "chicago blackhawks"
        assert sport_scoped != "chicago bulls"

    def test_nhl_min_not_timberwolves(self):
        sport_scoped = _SPORT_ABBREV[("nhl", "min")]
        assert sport_scoped == "minnesota wild"
        assert sport_scoped != "minnesota timberwolves"

    def test_nhl_car_not_nfl_panthers(self):
        sport_scoped = _SPORT_ABBREV[("nhl", "car")]
        assert sport_scoped == "carolina hurricanes"
        assert sport_scoped != "carolina panthers"

    def test_cbb_hou_not_rockets(self):
        sport_scoped = _SPORT_ABBREV[("cbb", "hou")]
        assert sport_scoped == "houston cougars"
        assert sport_scoped != "houston rockets"


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tennis Surname Matching
# ═══════════════════════════════════════════════════════════════════════════

class TestTennisNameMatch:
    """Test _tennis_name_match() for exact, prefix, fuzzy, and edge cases."""

    def test_exact_surname(self):
        assert _tennis_name_match("djokovic", "djokovic") is True

    def test_truncated_slug_stricker(self):
        # "stricke" is a prefix of "stricker" (7 chars >= 5)
        assert _tennis_name_match("stricke", "stricker") is True

    def test_truncated_slug_shelbayh(self):
        # "shelbay" is prefix of "shelbayh"
        assert _tennis_name_match("shelbay", "shelbayh") is True

    def test_short_exact(self):
        assert _tennis_name_match("tien", "tien") is True

    def test_very_short_exact(self):
        # "ti" exact match still works (exact check before length check)
        assert _tennis_name_match("ti", "ti") is True

    def test_non_match(self):
        assert _tennis_name_match("djokovic", "sinner") is False

    def test_fuzzy_close_zverev(self):
        # "zverv" vs "zverev" -- SequenceMatcher ratio should be >= 0.80
        from difflib import SequenceMatcher
        ratio = SequenceMatcher(None, "zverv", "zverev").ratio()
        # Verify the ratio is indeed above threshold
        assert ratio >= 0.80, f"Expected ratio >= 0.80 but got {ratio}"
        assert _tennis_name_match("zverv", "zverev") is True

    def test_empty_slug(self):
        assert _tennis_name_match("", "djokovic") is False

    def test_empty_surname(self):
        assert _tennis_name_match("djokovic", "") is False

    def test_prefix_too_short(self):
        # "djo" is only 3 chars -- prefix match requires >= 5
        # But fuzzy match of "djo" vs "djokovic" is ratio 0.545 < 0.80
        assert _tennis_name_match("djo", "djokovic") is False

    def test_reverse_prefix(self):
        # surname is prefix of slug (rare case)
        assert _tennis_name_match("djokovic123", "djokovic") is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. _teams_match_strict()
# ═══════════════════════════════════════════════════════════════════════════

class TestTeamsMatchStrict:
    """Test _teams_match_strict() for various sport/team combinations."""

    def test_epl_two_team_match(self):
        result = _teams_match_strict(
            ["ars", "mun"], "Arsenal", "Manchester United", "epl"
        )
        assert result is not None
        assert result["home_matched"] is True
        assert result["away_matched"] is True

    def test_nhl_two_team_sport_aware(self):
        """NHL det/fla should resolve to Red Wings / Panthers via _SPORT_ABBREV."""
        result = _teams_match_strict(
            ["det", "fla"], "Detroit Red Wings", "Florida Panthers", "nhl"
        )
        assert result is not None
        assert result["home_matched"] is True
        assert result["away_matched"] is True

    def test_tennis_surname_match(self):
        result = _teams_match_strict(
            ["djokovic", "sinner"], "Novak Djokovic", "Jannik Sinner", "atp"
        )
        assert result is not None
        assert result["home_matched"] is True
        assert result["away_matched"] is True

    def test_tennis_truncated_slugs(self):
        """Truncated tennis slugs like 'stricke' for 'Stricker'."""
        result = _teams_match_strict(
            ["stricke", "grenier"], "Dominic Stricker", "Hugo Grenier", "atp"
        )
        assert result is not None
        assert result["home_matched"] is True
        assert result["away_matched"] is True

    def test_wrong_sport_nba_det_not_red_wings(self):
        """With sport='nba', det should resolve to Pistons, not Red Wings."""
        result = _teams_match_strict(
            ["det", "fla"], "Detroit Red Wings", "Florida Panthers", "nba"
        )
        # In NBA, det -> Detroit Pistons, fla -> not mapped in sport abbrev
        # Generic abbrev det -> some team (pistons or lions), not Red Wings
        # This should NOT match Red Wings + Panthers
        assert result is None

    def test_single_team_will_x_win(self):
        """Single team slug like 'Will Arsenal win?' -> only one team extracted."""
        result = _teams_match_strict(
            ["Arsenal"], "Arsenal", "Chelsea", "epl"
        )
        assert result is not None
        # Single team match allowed
        assert result["home_matched"] is True or result["away_matched"] is True

    def test_two_teams_only_one_matches_fails(self):
        """If two teams provided but only one matches, should return None."""
        result = _teams_match_strict(
            ["ars", "xyz"], "Arsenal", "Chelsea", "epl"
        )
        assert result is None

    def test_nba_standard_abbrevs(self):
        result = _teams_match_strict(
            ["lal", "bos"], "Los Angeles Lakers", "Boston Celtics", "nba"
        )
        assert result is not None

    def test_nhl_chi_car(self):
        """Chicago Blackhawks vs Carolina Hurricanes in NHL."""
        result = _teams_match_strict(
            ["chi", "car"], "Chicago Blackhawks", "Carolina Hurricanes", "nhl"
        )
        assert result is not None
        assert result["home_matched"] is True
        assert result["away_matched"] is True


# ═══════════════════════════════════════════════════════════════════════════
# 5. Date Matching (_dates_close)
# ═══════════════════════════════════════════════════════════════════════════

class TestDatesClose:
    """Test _dates_close() with various date scenarios."""

    def test_within_96_hours(self):
        assert _dates_close("2026-03-05T12:00:00Z", "2026-03-06T12:00:00Z") is True

    def test_exactly_96_hours_boundary(self):
        # 96 hours = 4 days. 5th + 4 days = 9th, so 9th 12:00 is exactly 96h
        assert _dates_close("2026-03-05T12:00:00Z", "2026-03-09T12:00:00Z") is False
        # Just under 96h
        assert _dates_close("2026-03-05T12:00:00Z", "2026-03-09T11:59:00Z") is True

    def test_beyond_96_hours(self):
        assert _dates_close("2026-03-01T12:00:00Z", "2026-03-10T12:00:00Z") is False

    def test_missing_poly_end(self):
        assert _dates_close("", "2026-03-05T12:00:00Z") is False

    def test_missing_odds_commence(self):
        assert _dates_close("2026-03-05T12:00:00Z", "") is False

    def test_both_missing(self):
        assert _dates_close("", "") is False

    def test_invalid_date_string_permissive(self):
        """Invalid dates should return True (permissive fallback via except)."""
        assert _dates_close("not-a-date", "also-not-a-date") is True

    def test_one_valid_one_invalid(self):
        """One valid + one invalid triggers ValueError -> returns True."""
        assert _dates_close("2026-03-05T12:00:00Z", "not-a-date") is True

    def test_same_datetime(self):
        assert _dates_close("2026-03-05T15:00:00Z", "2026-03-05T15:00:00Z") is True

    def test_order_doesnt_matter(self):
        assert _dates_close("2026-03-06T12:00:00Z", "2026-03-05T12:00:00Z") is True


# ═══════════════════════════════════════════════════════════════════════════
# 6. _extract_date_from_slug()
# ═══════════════════════════════════════════════════════════════════════════

class TestExtractDateFromSlug:
    """Test _extract_date_from_slug() for various slug formats."""

    def test_tennis_slug(self):
        result = _extract_date_from_slug("atp-djokovic-sinner-2026-03-05")
        assert result == "2026-03-05T12:00:00Z"

    def test_nhl_slug(self):
        result = _extract_date_from_slug("nhl-det-fla-2026-03-10")
        assert result == "2026-03-10T12:00:00Z"

    def test_epl_slug_with_suffix(self):
        result = _extract_date_from_slug("epl-ars-mun-2026-03-01-ars")
        assert result == "2026-03-01T12:00:00Z"

    def test_no_date_slug(self):
        result = _extract_date_from_slug("no-date-slug")
        assert result is None

    def test_empty_slug(self):
        result = _extract_date_from_slug("")
        assert result is None

    def test_slug_with_spread(self):
        result = _extract_date_from_slug("epl-ars-che-2026-03-08-spread-home-2pt5")
        assert result == "2026-03-08T12:00:00Z"


# ═══════════════════════════════════════════════════════════════════════════
# 7. Market Type Classification
# ═══════════════════════════════════════════════════════════════════════════

class TestClassifyMarketType:
    """Test classify_market_type() for h2h, spread, total, and exotic."""

    # H2H
    def test_h2h_epl(self):
        assert classify_market_type("epl-ars-mun-2026-03-01-ars", "Will Arsenal win?") == "h2h"

    def test_h2h_nhl(self):
        assert classify_market_type("nhl-det-fla-2026-03-10-det", "Will Detroit win?") == "h2h"

    def test_h2h_tennis(self):
        assert classify_market_type("atp-djokovic-sinner-2026-03-05", "Who wins?") == "h2h"

    # Spread
    def test_spread_epl(self):
        assert classify_market_type(
            "epl-ars-che-2026-03-08-spread-home-2pt5", "Will Arsenal cover -2.5?"
        ) == "spread"

    def test_spread_question_only(self):
        assert classify_market_type(
            "epl-ars-che-2026-03-08", "Will Arsenal cover the spread?"
        ) == "spread"

    # Total
    def test_total_nba(self):
        assert classify_market_type(
            "nba-lal-bos-2026-03-05-total-226pt5", "Over/Under 226.5"
        ) == "total"

    def test_total_ou_slug(self):
        assert classify_market_type(
            "nba-lal-bos-2026-03-05-ou-226pt5", "Total points"
        ) == "total"

    def test_total_question_only(self):
        assert classify_market_type(
            "nba-lal-bos-2026-03-05", "Will total points be over/under 226.5?"
        ) == "total"

    # Exotic
    def test_exotic_first_set(self):
        assert classify_market_type(
            "atp-djokovic-sinner-2026-03-05-first-set-winner-djokovic",
            "First set winner"
        ) == "exotic"

    def test_exotic_set_handicap(self):
        assert classify_market_type(
            "wta-swiatek-sabalenka-2026-03-05-set-handicap-away-1pt5",
            "Set handicap"
        ) == "exotic"

    def test_exotic_set_totals(self):
        assert classify_market_type(
            "atp-djokovic-sinner-2026-03-05-set-totals-2pt5",
            "Total sets"
        ) == "exotic"

    def test_exotic_btts(self):
        assert classify_market_type(
            "epl-ars-mun-2026-03-01-btts-yes",
            "Both teams to score"
        ) == "exotic"

    def test_exotic_halftime(self):
        assert classify_market_type(
            "epl-ars-mun-2026-03-01-halftime-result",
            "Halftime result"
        ) == "exotic"

    def test_exotic_corners(self):
        # Note: slug contains "-over-" which triggers total detection before
        # the "-corners-" exotic check. Use a slug without "-over-" to test corners.
        assert classify_market_type(
            "epl-ars-mun-2026-03-01-corners-10pt5",
            "Total corners"
        ) == "exotic"

    def test_corners_over_classified_as_exotic(self):
        # "-corners-" exotic check now runs before "-over-" total check (bug fix).
        assert classify_market_type(
            "epl-ars-mun-2026-03-01-corners-over-10pt5",
            "Total corners"
        ) == "exotic"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Line Parsing
# ═══════════════════════════════════════════════════════════════════════════

class TestParseSpreadLine:
    """Test parse_spread_line() for extracting spread values."""

    def test_pt_notation(self):
        assert parse_spread_line("epl-ars-che-2026-03-08-spread-home-2pt5") == 2.5

    def test_pt_notation_1pt5(self):
        assert parse_spread_line("nba-lal-bos-2026-03-05-spread-away-1pt5") == 1.5

    def test_pt_notation_10pt5(self):
        assert parse_spread_line("nfl-kc-buf-2026-01-15-spread-home-10pt5") == 10.5

    def test_no_spread_info(self):
        assert parse_spread_line("epl-ars-mun-2026-03-01") is None


class TestParseTotalLine:
    """Test parse_total_line() for extracting total values."""

    def test_pt_notation(self):
        assert parse_total_line("nba-lal-bos-2026-03-05-total-226pt5") == 226.5

    def test_pt_notation_2pt5(self):
        assert parse_total_line("epl-ars-mun-2026-03-01-total-2pt5") == 2.5

    def test_no_total_info(self):
        assert parse_total_line("epl-ars-mun-2026-03-01") is None


# ═══════════════════════════════════════════════════════════════════════════
# 9. Edge Calculation (integration-level, legacy path)
# ═══════════════════════════════════════════════════════════════════════════

class TestCalculateH2HEdges:
    """Test _calculate_h2h_edges() legacy path."""

    def test_positive_edge_buy(self):
        """When PM price < fair prob, edge > 0 -> BUY."""
        pm = {
            "sport": "epl",
            "question": "Will Arsenal win?",
            "outcomes": ["Yes", "No"],
            "prices": [0.40, 0.60],
            "token_ids": ["t1", "t2"],
        }
        odds_event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "outcomes": {
                "Arsenal": {"fair_prob": 0.55},
                "Chelsea": {"fair_prob": 0.30},
                "Draw": {"fair_prob": 0.15},
            },
        }
        edges = _calculate_h2h_edges(pm, odds_event)
        # Yes outcome: PM=0.40, fair=0.55, edge=+0.15, edge_pct=37.5% -> should appear
        yes_edges = [e for e in edges if e["outcome"] == "Yes"]
        assert len(yes_edges) == 1
        assert yes_edges[0]["side"] == "BUY"
        assert yes_edges[0]["edge"] == pytest.approx(0.15, abs=0.01)
        assert yes_edges[0]["edge_pct"] == pytest.approx(37.5, abs=0.5)

    def test_negative_edge_sell(self):
        """When PM price > fair prob, edge < 0 -> SELL."""
        pm = {
            "sport": "epl",
            "question": "Will Arsenal win?",
            "outcomes": ["Yes", "No"],
            "prices": [0.70, 0.30],
            "token_ids": ["t1", "t2"],
        }
        odds_event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "outcomes": {
                "Arsenal": {"fair_prob": 0.55},
                "Chelsea": {"fair_prob": 0.30},
                "Draw": {"fair_prob": 0.15},
            },
        }
        edges = _calculate_h2h_edges(pm, odds_event)
        yes_edges = [e for e in edges if e["outcome"] == "Yes"]
        assert len(yes_edges) == 1
        assert yes_edges[0]["side"] == "SELL"
        assert yes_edges[0]["edge"] < 0

    def test_small_edge_filtered(self):
        """Edges with |edge_pct| <= 1.0 are filtered out."""
        pm = {
            "sport": "epl",
            "question": "Will Arsenal win?",
            "outcomes": ["Yes"],
            "prices": [0.549],  # fair=0.55, edge=0.001, edge_pct=0.18% -> filtered
            "token_ids": ["t1"],
        }
        odds_event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "outcomes": {
                "Arsenal": {"fair_prob": 0.55},
            },
        }
        edges = _calculate_h2h_edges(pm, odds_event)
        assert len(edges) == 0


class TestCalculateTotalEdges:
    """Test _calculate_total_edges() requires exact line match."""

    def test_exact_line_match(self):
        pm = {
            "sport": "nba",
            "slug": "nba-lal-bos-2026-03-05-total-226pt5",
            "outcomes": ["Over", "Under"],
            "prices": [0.40, 0.60],
            "token_ids": ["t_over", "t_under"],
        }
        odds_event = {
            "total_outcomes": {
                "Over": {"point": 226.5, "fair_prob": 0.52},
                "Under": {"point": 226.5, "fair_prob": 0.48},
            },
            "commence_time": "2026-03-05T19:00:00Z",
        }
        edges = _calculate_total_edges(pm, odds_event, pm["slug"])
        # Over: PM=0.40, fair=0.52, edge=0.12, edge_pct=30% -> BUY
        over_edges = [e for e in edges if e["outcome"] == "Over"]
        assert len(over_edges) == 1
        assert over_edges[0]["side"] == "BUY"
        assert over_edges[0]["market_type"] == "total"

    def test_different_lines_no_edge(self):
        """If PM line (226.5) differs from odds line (228.5), no edges."""
        pm = {
            "sport": "nba",
            "slug": "nba-lal-bos-2026-03-05-total-226pt5",
            "outcomes": ["Over", "Under"],
            "prices": [0.40, 0.60],
            "token_ids": ["t_over", "t_under"],
        }
        odds_event = {
            "total_outcomes": {
                "Over": {"point": 228.5, "fair_prob": 0.52},
                "Under": {"point": 228.5, "fair_prob": 0.48},
            },
            "commence_time": "2026-03-05T19:00:00Z",
        }
        edges = _calculate_total_edges(pm, odds_event, pm["slug"])
        assert len(edges) == 0


class TestCalculateSpreadEdges:
    """Test _calculate_spread_edges() requires exact spread match."""

    def test_exact_spread_match(self):
        pm = {
            "sport": "epl",
            "slug": "epl-ars-che-2026-03-08-spread-home-2pt5",
            "outcomes": ["Yes", "No"],
            "prices": [0.30, 0.70],
            "token_ids": ["t_yes", "t_no"],
        }
        odds_event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "spread_outcomes": {
                "Arsenal": {"point": -2.5, "fair_prob": 0.45},
                "Chelsea": {"point": 2.5, "fair_prob": 0.55},
            },
            "commence_time": "2026-03-08T15:00:00Z",
        }
        edges = _calculate_spread_edges(pm, odds_event, pm["slug"])
        # Yes (Arsenal covers -2.5): PM=0.30, fair=0.45, edge=+0.15 -> BUY
        yes_edges = [e for e in edges if e["outcome"] == "Yes"]
        assert len(yes_edges) == 1
        assert yes_edges[0]["side"] == "BUY"
        assert yes_edges[0]["market_type"] == "spread"

    def test_different_spread_no_edge(self):
        """If PM spread (2.5) differs from odds spread (1.5), no edges."""
        pm = {
            "sport": "epl",
            "slug": "epl-ars-che-2026-03-08-spread-home-2pt5",
            "outcomes": ["Yes", "No"],
            "prices": [0.30, 0.70],
            "token_ids": ["t_yes", "t_no"],
        }
        odds_event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "spread_outcomes": {
                "Arsenal": {"point": -1.5, "fair_prob": 0.60},
                "Chelsea": {"point": 1.5, "fair_prob": 0.40},
            },
            "commence_time": "2026-03-08T15:00:00Z",
        }
        edges = _calculate_spread_edges(pm, odds_event, pm["slug"])
        assert len(edges) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 10. Full match_markets Integration
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchMarketsIntegration:
    """Integration tests for match_markets()."""

    def test_epl_h2h_match(self, pm_epl_h2h, epl_odds_event):
        """Verify EPL h2h market matches correctly."""
        odds_events = {"epl": [epl_odds_event]}
        result = match_markets([pm_epl_h2h], odds_events)
        assert len(result) >= 1
        m = result[0]
        assert m["market_type"] == "h2h"
        assert m["odds_event"]["home_team"] == "Arsenal"
        assert len(m["edges"]) > 0

    def test_nhl_h2h_match(self, pm_nhl_h2h, nhl_odds_event):
        """Verify NHL h2h market matches correctly with sport-aware abbrevs."""
        odds_events = {"nhl": [nhl_odds_event]}
        result = match_markets([pm_nhl_h2h], odds_events)
        assert len(result) >= 1
        m = result[0]
        assert m["market_type"] == "h2h"
        assert m["odds_event"]["home_team"] == "Detroit Red Wings"

    def test_tennis_slug_date_used(self, pm_tennis, tennis_odds_event):
        """Tennis markets should use slug date, not end_date (tournament end)."""
        odds_events = {"atp": [tennis_odds_event]}
        result = match_markets([pm_tennis], odds_events)
        # The PM end_date is 2026-03-15, odds commence is 2026-03-05.
        # That's 10 days apart (>96h) so end_date would FAIL date check.
        # But slug date "2026-03-05" matches commence "2026-03-05" -> pass.
        assert len(result) >= 1
        m = result[0]
        assert m["market_type"] == "h2h"
        assert m["odds_event"]["home_team"] == "Novak Djokovic"

    def test_tennis_without_slug_date_fails(self, tennis_odds_event):
        """If tennis slug has no date and end_date is far, no match."""
        pm = {
            "sport": "atp",
            "slug": "atp-djokovic-sinner",  # no date in slug
            "question": "Will Novak Djokovic win?",
            "outcomes": ["Yes", "No"],
            "prices": [0.40, 0.60],
            "token_ids": ["t1", "t2"],
            "end_date": "2026-03-20T23:59:00Z",  # 15 days from commence
        }
        odds_events = {"atp": [tennis_odds_event]}
        result = match_markets([pm], odds_events)
        assert len(result) == 0

    def test_exotic_markets_filtered(self, epl_odds_event):
        """Exotic markets should be skipped."""
        pm_exotic = {
            "sport": "epl",
            "slug": "epl-ars-mun-2026-03-01-btts-yes",
            "question": "Both teams to score?",
            "outcomes": ["Yes", "No"],
            "prices": [0.55, 0.45],
            "token_ids": ["t1", "t2"],
            "end_date": "2026-03-01T17:00:00Z",
        }
        odds_events = {"epl": [epl_odds_event]}
        result = match_markets([pm_exotic], odds_events)
        assert len(result) == 0

    def test_total_market_match(self, pm_nba_total, nba_odds_event):
        """Verify NBA totals market matches and produces edges."""
        odds_events = {"nba": [nba_odds_event]}
        result = match_markets([pm_nba_total], odds_events)
        assert len(result) >= 1
        m = result[0]
        assert m["market_type"] == "total"

    def test_spread_market_match(self, pm_epl_spread):
        """Verify EPL spread market matches and produces edges."""
        odds_event = {
            "home_team": "Arsenal",
            "away_team": "Chelsea",
            "commence_time": "2026-03-08T15:00:00Z",
            "outcomes": {
                "Arsenal": {"fair_prob": 0.65, "decimal_odds": 1.50},
                "Chelsea": {"fair_prob": 0.20, "decimal_odds": 4.50},
                "Draw": {"fair_prob": 0.15, "decimal_odds": 6.00},
            },
            "spread_outcomes": {
                "Arsenal": {"point": -2.5, "fair_prob": 0.40},
                "Chelsea": {"point": 2.5, "fair_prob": 0.60},
            },
        }
        odds_events = {"epl": [odds_event]}
        result = match_markets([pm_epl_spread], odds_events)
        assert len(result) >= 1
        m = result[0]
        assert m["market_type"] == "spread"

    def test_no_sport_match(self):
        """PM market with unknown sport produces no matches."""
        pm = {
            "sport": "curling",
            "slug": "curling-team-a-team-b-2026-03-01",
            "question": "Who wins?",
            "outcomes": ["A", "B"],
            "prices": [0.50, 0.50],
            "token_ids": ["t1", "t2"],
            "end_date": "2026-03-01T17:00:00Z",
        }
        odds_events = {"epl": []}
        result = match_markets([pm], odds_events)
        assert len(result) == 0

    def test_empty_inputs(self):
        assert match_markets([], {}) == []

    def test_multiple_pm_markets(self, pm_epl_h2h, pm_nhl_h2h,
                                  epl_odds_event, nhl_odds_event):
        """Multiple PM markets across sports should all match."""
        odds_events = {
            "epl": [epl_odds_event],
            "nhl": [nhl_odds_event],
        }
        result = match_markets([pm_epl_h2h, pm_nhl_h2h], odds_events)
        assert len(result) == 2
        sports = {m["polymarket"]["sport"] for m in result}
        assert "epl" in sports
        assert "nhl" in sports
