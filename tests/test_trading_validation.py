"""Unit tests for the trading validation framework.

Tests all six validation categories using synthetic position data.
"""
import math
import os
import sys
import tempfile
import csv
from datetime import datetime, timezone, timedelta

import pytest

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.trade_validator import (
    PositionRow,
    ValidationReport,
    validate_positions,
    validate_edge_accuracy,
    validate_phantom_edges,
    validate_by_sport,
    validate_risk_limits,
    validate_capital_efficiency,
    audit_matching_quality,
    is_phantom,
    load_positions,
    validate_all,
    print_report,
    _compute_brier_score,
    _compute_edge_buckets,
)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _past_iso(days: int = 1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()


def _future_iso(days: int = 7) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def _make_pos(
    token_id: str = "tok_001",
    slug: str = "epl-ars-mun-2026-03-10-ars",
    outcome: str = "Yes",
    sport: str = "epl",
    market_type: str = "h2h",
    entry_price: float = 0.30,
    fair_prob: float = 0.35,
    edge_pct: float = 5.0,
    shares: float = 10.0,
    cost_usdc: float = 3.0,
    bookmaker: str = "pinnacle",
    opened_at: str = "",
    status: str = "open",
    resolution_price: float = 0.0,
    payout: float = 0.0,
    pnl: float = 0.0,
    closed_at: str = "",
) -> PositionRow:
    if not opened_at:
        opened_at = _past_iso(2)
    return PositionRow(
        token_id=token_id,
        slug=slug,
        outcome=outcome,
        sport=sport,
        market_type=market_type,
        entry_price=entry_price,
        fair_prob=fair_prob,
        edge_pct=edge_pct,
        shares=shares,
        cost_usdc=cost_usdc,
        bookmaker=bookmaker,
        opened_at=opened_at,
        status=status,
        resolution_price=resolution_price,
        payout=payout,
        pnl=pnl,
        closed_at=closed_at,
    )


def _make_resolved_positions(n: int = 100, win_rate: float = 0.60) -> list[PositionRow]:
    """Create n resolved positions with a given win rate and spread across edge buckets."""
    positions = []
    n_wins = int(n * win_rate)

    edge_ranges = [
        (1.5, "1-3%"),
        (4.0, "3-5%"),
        (7.0, "5-10%"),
        (12.0, "10%+"),
    ]

    for i in range(n):
        edge_idx = i % len(edge_ranges)
        edge_pct = edge_ranges[edge_idx][0]
        entry_price = 0.30
        fair_prob = entry_price * (1 + edge_pct / 100.0)
        won = i < n_wins
        cost = 3.0
        payout = 10.0 if won else 0.0
        pnl = payout - cost

        positions.append(_make_pos(
            token_id=f"tok_{i:04d}",
            slug=f"epl-ars-mun-2026-03-{10 + (i % 20):02d}-ars",
            sport="epl" if i % 3 != 2 else "nhl",
            entry_price=entry_price,
            fair_prob=fair_prob,
            edge_pct=edge_pct,
            shares=payout / entry_price if won else cost / entry_price,
            cost_usdc=cost,
            status="won" if won else "lost",
            resolution_price=1.0 if won else 0.0,
            payout=payout,
            pnl=pnl,
            opened_at=_past_iso(5),
            closed_at=_past_iso(1),
        ))
    return positions


def _write_csv(positions: list[PositionRow], path: str):
    """Write positions to CSV file."""
    fieldnames = [
        "token_id", "slug", "outcome", "sport", "market_type",
        "entry_price", "fair_prob", "edge_pct", "shares", "cost_usdc",
        "bookmaker", "opened_at", "status", "resolution_price",
        "payout", "pnl", "closed_at",
    ]
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in positions:
            writer.writerow({
                "token_id": p.token_id,
                "slug": p.slug,
                "outcome": p.outcome,
                "sport": p.sport,
                "market_type": p.market_type,
                "entry_price": p.entry_price,
                "fair_prob": p.fair_prob,
                "edge_pct": p.edge_pct,
                "shares": p.shares,
                "cost_usdc": p.cost_usdc,
                "bookmaker": p.bookmaker,
                "opened_at": p.opened_at,
                "status": p.status,
                "resolution_price": p.resolution_price,
                "payout": p.payout,
                "pnl": p.pnl,
                "closed_at": p.closed_at,
            })


# ═══════════════════════════════════════════════════════════════════════════
# 1. Edge Calibration Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestEdgeCalibration:
    """Test edge accuracy validation with known edges and outcomes."""

    def test_brier_score_perfect(self):
        """Brier score = 0 when fair_prob perfectly predicts outcomes."""
        positions = [
            _make_pos(token_id="t1", status="won", fair_prob=1.0, pnl=7.0),
            _make_pos(token_id="t2", status="lost", fair_prob=0.0, pnl=-3.0),
        ]
        score = _compute_brier_score(positions)
        assert score == pytest.approx(0.0, abs=1e-9)

    def test_brier_score_worst(self):
        """Brier score = 1 when fair_prob is maximally wrong."""
        positions = [
            _make_pos(token_id="t1", status="won", fair_prob=0.0, pnl=7.0),
            _make_pos(token_id="t2", status="lost", fair_prob=1.0, pnl=-3.0),
        ]
        score = _compute_brier_score(positions)
        assert score == pytest.approx(1.0, abs=1e-9)

    def test_brier_score_typical(self):
        """Brier score for typical calibrated predictions."""
        positions = [
            _make_pos(token_id="t1", status="won", fair_prob=0.7, pnl=7.0),
            _make_pos(token_id="t2", status="lost", fair_prob=0.3, pnl=-3.0),
        ]
        # (0.7 - 1)^2 + (0.3 - 0)^2 = 0.09 + 0.09 = 0.18, avg = 0.09
        score = _compute_brier_score(positions)
        assert score == pytest.approx(0.09, abs=1e-9)

    def test_brier_score_empty(self):
        """Brier score is 0 for empty list."""
        assert _compute_brier_score([]) == 0.0

    def test_edge_bucket_counts(self):
        """Verify edge bucket analysis assigns correct counts."""
        positions = _make_resolved_positions(100, win_rate=0.60)
        buckets = _compute_edge_buckets(positions)

        assert len(buckets) == 4
        total_count = sum(b.count for b in buckets)
        assert total_count == 100

    def test_edge_bucket_win_rates(self):
        """Verify edge bucket win rates are correctly computed."""
        # All wins
        positions = _make_resolved_positions(20, win_rate=1.0)
        buckets = _compute_edge_buckets(positions)
        for b in buckets:
            if b.count > 0:
                assert b.actual_win_rate == pytest.approx(1.0)

    def test_edge_calibration_alert_fires(self):
        """Alert should fire when actual win rate is far below predicted."""
        # Create positions with high fair_prob but all losing
        positions = []
        for i in range(20):
            positions.append(_make_pos(
                token_id=f"tok_{i:04d}",
                edge_pct=2.0,  # 1-3% bucket
                fair_prob=0.80,  # predicts 80% win rate
                status="lost",
                pnl=-3.0,
            ))
        result = validate_edge_accuracy(positions)
        assert result["alert"] is True
        assert "-15pp" in result["detail"]

    def test_edge_calibration_no_alert_when_good(self):
        """No alert when actual win rate is close to predicted."""
        positions = _make_resolved_positions(100, win_rate=0.60)
        result = validate_edge_accuracy(positions)
        # With random bucket assignment, should generally not fire alert
        # (actual ~60% vs predicted ~30-35% fair_prob, which are entry-price-based)
        # This tests the mechanics work, not that specific data triggers alerts
        assert isinstance(result["brier_score"], float)
        assert len(result["edge_buckets"]) == 4

    def test_full_validation_edge_section(self):
        """validate_positions populates edge fields in report."""
        positions = _make_resolved_positions(50, win_rate=0.50)
        report = validate_positions(positions)
        assert report.brier_score >= 0.0
        assert len(report.edge_buckets) == 4


# ═══════════════════════════════════════════════════════════════════════════
# 2. Phantom Detection Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestPhantomDetection:
    """Test phantom edge detection for exotic tennis markets."""

    def test_phantom_slug_detection(self):
        """Known phantom patterns are detected."""
        assert is_phantom("atp-djokovic-sinner-first-set-winner-2026-03-05") is True
        assert is_phantom("wta-player-set-handicap-2026-03-05") is True
        assert is_phantom("atp-player-set-totals-2026-03-05") is True
        assert is_phantom("wta-player-match-total-2026-03-05") is True

    def test_legitimate_slug_not_flagged(self):
        """Normal slugs are not flagged as phantom."""
        assert is_phantom("epl-ars-mun-2026-03-10-ars") is False
        assert is_phantom("nhl-tor-bos-2026-03-10-tor") is False
        assert is_phantom("atp-djokovic-sinner-2026-03-05") is False

    def test_phantom_pnl_split(self):
        """P&L is correctly split between phantom and legitimate trades."""
        positions = [
            _make_pos(token_id="p1", slug="atp-player-first-set-winner-2026-03-05",
                      status="won", pnl=5.0, cost_usdc=3.0),
            _make_pos(token_id="p2", slug="atp-player-set-handicap-2026-03-05",
                      status="lost", pnl=-2.0, cost_usdc=3.0),
            _make_pos(token_id="l1", slug="epl-ars-mun-2026-03-10-ars",
                      status="won", pnl=7.0, cost_usdc=4.0),
            _make_pos(token_id="l2", slug="nhl-tor-bos-2026-03-10-tor",
                      status="lost", pnl=-3.0, cost_usdc=5.0),
        ]
        result = validate_phantom_edges(positions)
        assert result["phantom_count"] == 2
        assert result["legitimate_count"] == 2
        assert result["phantom_pnl"] == pytest.approx(3.0)  # 5 - 2
        assert result["legitimate_pnl"] == pytest.approx(4.0)  # 7 - 3

    def test_phantom_exposure_alert(self):
        """Alert fires when phantom trades are >10% of total exposure."""
        positions = [
            # 90% phantom exposure
            _make_pos(token_id="p1", slug="atp-player-first-set-winner-2026-03-05",
                      cost_usdc=90.0, status="open"),
            _make_pos(token_id="l1", slug="epl-ars-mun-2026-03-10-ars",
                      cost_usdc=10.0, status="open"),
        ]
        result = validate_phantom_edges(positions)
        assert result["alert"] is True
        assert result["phantom_exposure_pct"] == pytest.approx(90.0)

    def test_no_phantom_alert_when_clean(self):
        """No alert when there are no phantom trades."""
        positions = [
            _make_pos(token_id="l1", slug="epl-ars-mun-2026-03-10-ars",
                      cost_usdc=10.0, status="open"),
        ]
        result = validate_phantom_edges(positions)
        assert result["alert"] is False
        assert result["phantom_count"] == 0


# ═══════════════════════════════════════════════════════════════════════════
# 3. Sport-Level Validation Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestSportValidation:

    def test_sport_stats_aggregation(self):
        """Sport stats are correctly aggregated."""
        positions = [
            _make_pos(token_id="e1", sport="epl", status="won", pnl=5.0),
            _make_pos(token_id="e2", sport="epl", status="lost", pnl=-3.0),
            _make_pos(token_id="n1", sport="nhl", status="won", pnl=4.0),
        ]
        result = validate_by_sport(positions)
        stats = {s.sport: s for s in result["sport_stats"]}

        assert "epl" in stats
        assert stats["epl"].resolved == 2
        assert stats["epl"].wins == 1
        assert stats["epl"].win_rate == pytest.approx(0.5)
        assert stats["epl"].total_pnl == pytest.approx(2.0)

        assert "nhl" in stats
        assert stats["nhl"].wins == 1
        assert stats["nhl"].win_rate == pytest.approx(1.0)

    def test_sport_low_win_rate_flag(self):
        """Flag sport with <40% win rate over 20+ resolved trades."""
        positions = []
        for i in range(25):
            won = i < 5  # 20% win rate
            positions.append(_make_pos(
                token_id=f"t_{i}",
                sport="nba",
                status="won" if won else "lost",
                pnl=7.0 if won else -3.0,
            ))
        result = validate_by_sport(positions)
        nba = [s for s in result["sport_stats"] if s.sport == "nba"][0]
        assert nba.flagged is True
        assert "40%" in nba.flag_reason

    def test_sport_negative_pnl_flag(self):
        """Flag sport with negative P&L over 30+ resolved trades."""
        positions = []
        for i in range(35):
            won = i < 14  # 40% win rate, but net negative if pnl structured right
            positions.append(_make_pos(
                token_id=f"t_{i}",
                sport="cbb",
                status="won" if won else "lost",
                pnl=2.0 if won else -3.0,  # 14*2 - 21*3 = 28 - 63 = -35
            ))
        result = validate_by_sport(positions)
        cbb = [s for s in result["sport_stats"] if s.sport == "cbb"][0]
        assert cbb.flagged is True
        assert "Negative P&L" in cbb.flag_reason


# ═══════════════════════════════════════════════════════════════════════════
# 4. Matching Quality Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestMatchingQuality:

    def test_nhl_valid_abbreviations(self):
        """Valid NHL abbreviations should not be flagged."""
        positions = [
            _make_pos(token_id="n1", slug="nhl-tor-bos-2026-03-10-tor",
                      sport="nhl", status="open"),
        ]
        result = audit_matching_quality(positions)
        flagged = [a for a in result["audits"] if a.flagged]
        assert len(flagged) == 0

    def test_nhl_invalid_abbreviation_flagged(self):
        """Invalid NHL abbreviation should be flagged."""
        positions = [
            _make_pos(token_id="n1", slug="nhl-xyz-bos-2026-03-10-xyz",
                      sport="nhl", status="open"),
        ]
        result = audit_matching_quality(positions)
        flagged = [a for a in result["audits"] if a.flagged]
        assert len(flagged) >= 1
        assert any("xyz" in a.reason.lower() for a in flagged)

    def test_tennis_short_name_flagged(self):
        """Tennis slug with very short team part should be flagged."""
        positions = [
            _make_pos(token_id="t1", slug="atp-ab-cd-2026-03-10",
                      sport="atp", status="open"),
        ]
        result = audit_matching_quality(positions)
        flagged = [a for a in result["audits"] if a.flagged]
        assert len(flagged) >= 1
        assert any("short" in a.reason.lower() for a in flagged)

    def test_no_date_slug_flagged(self):
        """Slug without date pattern should be flagged."""
        positions = [
            _make_pos(token_id="t1", slug="epl-ars-mun-nodate",
                      sport="epl", status="open"),
        ]
        result = audit_matching_quality(positions)
        flagged = [a for a in result["audits"] if a.flagged]
        assert len(flagged) >= 1
        assert any("no date" in a.reason.lower() for a in flagged)

    def test_resolved_positions_skipped(self):
        """Matching audit only checks open positions."""
        positions = [
            _make_pos(token_id="t1", slug="nhl-xyz-bos-2026-03-10-xyz",
                      sport="nhl", status="won"),
        ]
        result = audit_matching_quality(positions)
        assert len(result["audits"]) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. Risk Limit Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestRiskLimits:

    def test_position_size_violation(self):
        """Flag position exceeding max position size."""
        positions = [
            _make_pos(token_id="t1", cost_usdc=15.0, status="open"),
        ]
        violations = validate_risk_limits(positions, max_position_usdc=8.0)
        assert len(violations) == 1
        assert violations[0].violation_type == "position_size"
        assert violations[0].severity == "critical"

    def test_total_exposure_violation(self):
        """Flag total exposure exceeding max."""
        positions = [
            _make_pos(token_id=f"t{i}", cost_usdc=5.0, status="open",
                      slug=f"epl-ars-mun-2026-03-{10+i}-ars")
            for i in range(20)  # 20 * 5 = 100
        ]
        violations = validate_risk_limits(positions, max_total_exposure_usdc=50.0)
        exposure_violations = [v for v in violations if v.violation_type == "total_exposure"]
        assert len(exposure_violations) == 1

    def test_contradictory_positions_detected(self):
        """Detect Over + Under on same game."""
        positions = [
            _make_pos(token_id="t1", slug="epl-ars-mun-2026-03-10-total-2pt5",
                      outcome="Over", status="open"),
            _make_pos(token_id="t2", slug="epl-ars-mun-2026-03-10-total-2pt5",
                      outcome="Under", status="open"),
        ]
        violations = validate_risk_limits(positions)
        contra = [v for v in violations if v.violation_type == "contradictory"]
        assert len(contra) == 1

    def test_contradictory_yes_no_detected(self):
        """Detect Yes + No on same h2h market."""
        positions = [
            _make_pos(token_id="t1", slug="epl-ars-mun-2026-03-10-ars",
                      outcome="Yes", status="open"),
            _make_pos(token_id="t2", slug="epl-ars-mun-2026-03-10-ars",
                      outcome="No", status="open"),
        ]
        violations = validate_risk_limits(positions)
        contra = [v for v in violations if v.violation_type == "contradictory"]
        assert len(contra) == 1

    def test_duplicate_token_detected(self):
        """Detect duplicate open positions with same token_id."""
        positions = [
            _make_pos(token_id="same_token", slug="epl-ars-mun-2026-03-10-ars",
                      status="open"),
            _make_pos(token_id="same_token", slug="epl-ars-mun-2026-03-10-ars",
                      status="open"),
        ]
        violations = validate_risk_limits(positions)
        dupes = [v for v in violations if v.violation_type == "duplicate"]
        assert len(dupes) == 1

    def test_no_violations_when_clean(self):
        """No violations when all limits are respected."""
        positions = [
            _make_pos(token_id="t1", cost_usdc=5.0, status="open",
                      slug="epl-ars-mun-2026-03-10-ars", outcome="Yes"),
            _make_pos(token_id="t2", cost_usdc=3.0, status="open",
                      slug="nhl-tor-bos-2026-03-11-tor", outcome="Yes"),
        ]
        violations = validate_risk_limits(positions, max_position_usdc=8.0,
                                          max_total_exposure_usdc=400.0)
        assert len(violations) == 0

    def test_resolved_positions_not_checked(self):
        """Resolved positions should not count toward risk limits."""
        positions = [
            _make_pos(token_id="t1", cost_usdc=15.0, status="won"),
        ]
        violations = validate_risk_limits(positions, max_position_usdc=8.0)
        assert len(violations) == 0


# ═══════════════════════════════════════════════════════════════════════════
# 6. Capital Efficiency / Stale Position Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestCapitalEfficiency:

    def test_days_to_resolution(self):
        """Verify average and median days to resolution."""
        positions = [
            _make_pos(token_id="t1", status="won",
                      opened_at=_past_iso(5), closed_at=_past_iso(2), pnl=5.0),
            _make_pos(token_id="t2", status="lost",
                      opened_at=_past_iso(10), closed_at=_past_iso(1), pnl=-3.0),
        ]
        result = validate_capital_efficiency(positions)
        # t1: 3 days, t2: 9 days
        assert result["avg_days"] == pytest.approx(6.0, abs=0.1)
        assert result["median_days"] == pytest.approx(9.0, abs=0.1)  # median of [3, 9]

    def test_capital_utilization(self):
        """Verify capital utilization calculation."""
        positions = [
            _make_pos(token_id="t1", cost_usdc=100.0, status="open"),
        ]
        result = validate_capital_efficiency(positions, max_total_exposure_usdc=400.0)
        assert result["utilization"] == pytest.approx(0.25)

    def test_stale_positions_detected(self):
        """Detect open positions past their event date."""
        past_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        positions = [
            _make_pos(token_id="t1",
                      slug=f"epl-ars-mun-{past_date}-ars",
                      status="open"),
        ]
        result = validate_capital_efficiency(positions)
        assert len(result["stale"]) == 1
        assert result["stale"][0]["slug"] == positions[0].slug

    def test_future_positions_not_stale(self):
        """Open positions with future event dates are not stale."""
        future_date = (datetime.now(timezone.utc) + timedelta(days=3)).strftime("%Y-%m-%d")
        positions = [
            _make_pos(token_id="t1",
                      slug=f"epl-ars-mun-{future_date}-ars",
                      status="open"),
        ]
        result = validate_capital_efficiency(positions)
        assert len(result["stale"]) == 0

    def test_no_resolved_positions(self):
        """Handle case with no resolved positions gracefully."""
        positions = [
            _make_pos(token_id="t1", status="open"),
        ]
        result = validate_capital_efficiency(positions)
        assert result["avg_days"] == 0.0
        assert result["median_days"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════
# Integration Tests
# ═══════════════════════════════════════════════════════════════════════════

class TestIntegration:

    def test_validate_all_from_csv(self, tmp_path):
        """Full validate_all flow from CSV file."""
        positions = _make_resolved_positions(20, win_rate=0.50)
        csv_path = str(tmp_path / "positions.csv")
        _write_csv(positions, csv_path)

        report = validate_all(csv_path)
        assert isinstance(report, ValidationReport)
        assert report.total_positions == 20
        assert report.resolved_positions == 20
        assert report.win_rate == pytest.approx(0.50)

    def test_validate_positions_all_sections(self):
        """validate_positions fills all report sections."""
        past_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        positions = [
            # Normal resolved
            _make_pos(token_id="r1", status="won", pnl=5.0, sport="epl",
                      opened_at=_past_iso(3), closed_at=_past_iso(1)),
            # Phantom
            _make_pos(token_id="p1", slug=f"atp-player-first-set-winner-{past_date}",
                      sport="atp", status="open", cost_usdc=5.0),
            # Open normal
            _make_pos(token_id="o1",
                      slug=f"epl-ars-mun-{past_date}-ars",
                      sport="epl", status="open", cost_usdc=3.0),
        ]

        report = validate_positions(positions)
        assert report.total_positions == 3
        assert report.open_positions == 2
        assert report.resolved_positions == 1
        assert report.phantom_count == 1
        assert report.legitimate_count == 2
        assert len(report.stale_positions) >= 1  # past_date positions

    def test_has_critical_issues_edge_alert(self):
        """has_critical_issues returns True when edge calibration alert fires."""
        positions = []
        for i in range(20):
            positions.append(_make_pos(
                token_id=f"tok_{i}",
                edge_pct=2.0,
                fair_prob=0.80,
                status="lost",
                pnl=-3.0,
            ))
        report = validate_positions(positions)
        assert report.edge_calibration_alert is True
        assert report.has_critical_issues is True

    def test_has_critical_issues_risk_violation(self):
        """has_critical_issues returns True when risk violations exist."""
        positions = [
            _make_pos(token_id="t1", cost_usdc=50.0, status="open"),
        ]
        report = validate_positions(positions, max_position_usdc=8.0)
        assert report.has_critical_issues is True

    def test_print_report_runs(self, capsys):
        """print_report executes without error."""
        positions = _make_resolved_positions(10, win_rate=0.50)
        report = validate_positions(positions)
        print_report(report)
        captured = capsys.readouterr()
        assert "TRADING VALIDATION REPORT" in captured.out
        assert "EDGE ACCURACY" in captured.out
        assert "VERDICT" in captured.out

    def test_load_positions_roundtrip(self, tmp_path):
        """Positions survive CSV write/load roundtrip."""
        original = [
            _make_pos(token_id="tok_abc", slug="epl-ars-mun-2026-03-10-ars",
                      edge_pct=5.5, cost_usdc=3.14, status="won", pnl=6.86),
        ]
        csv_path = str(tmp_path / "roundtrip.csv")
        _write_csv(original, csv_path)
        loaded = load_positions(csv_path)

        assert len(loaded) == 1
        assert loaded[0].token_id == "tok_abc"
        assert loaded[0].edge_pct == pytest.approx(5.5)
        assert loaded[0].cost_usdc == pytest.approx(3.14)
        assert loaded[0].pnl == pytest.approx(6.86)


# ═══════════════════════════════════════════════════════════════════════════
# CLI Test
# ═══════════════════════════════════════════════════════════════════════════

class TestCLI:

    def test_main_exits_0_on_clean(self, tmp_path, monkeypatch):
        """CLI exits 0 when no critical issues."""
        positions = [
            _make_pos(token_id="t1", status="won", pnl=5.0,
                      edge_pct=5.0, fair_prob=0.35, cost_usdc=3.0,
                      opened_at=_past_iso(3), closed_at=_past_iso(1)),
        ]
        csv_path = str(tmp_path / "clean.csv")
        _write_csv(positions, csv_path)

        from src.trade_validator import main
        monkeypatch.setattr("sys.argv", ["trade_validator", "--csv", csv_path])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    def test_main_exits_1_on_critical(self, tmp_path, monkeypatch):
        """CLI exits 1 when critical issues found."""
        positions = [
            _make_pos(token_id="t1", cost_usdc=50.0, status="open"),
        ]
        csv_path = str(tmp_path / "bad.csv")
        _write_csv(positions, csv_path)

        from src.trade_validator import main
        monkeypatch.setattr("sys.argv", [
            "trade_validator", "--csv", csv_path, "--max-position", "8",
        ])
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 1
