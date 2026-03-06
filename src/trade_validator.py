"""Trading results validation framework for the Polymarket sports arbitrage bot.

Validates edge accuracy, detects phantom edges, audits matching quality,
checks risk limit compliance, and reports capital efficiency metrics.

Usage:
    python -m src.trade_validator --csv data/paper/my_positions.csv
"""
import argparse
import csv
import math
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Optional


# ── Phantom edge slug patterns (exotic tennis markets that had phantom edges) ─
PHANTOM_PATTERNS = [
    "first-set-winner",
    "set-handicap",
    "set-totals",
    "match-total",
]

# ── Default risk limits (from config.py defaults) ──────────────────────────
DEFAULT_MAX_POSITION_USDC = 8.0
DEFAULT_MAX_TOTAL_EXPOSURE_USDC = 400.0


# ── Data Classes ────────────────────────────────────────────────────────────

@dataclass
class PositionRow:
    """A single position row from the CSV."""
    token_id: str
    slug: str
    outcome: str
    sport: str
    market_type: str
    entry_price: float
    fair_prob: float
    edge_pct: float
    shares: float
    cost_usdc: float
    bookmaker: str
    opened_at: str
    status: str
    resolution_price: float
    payout: float
    pnl: float
    closed_at: str


@dataclass
class EdgeBucket:
    """Statistics for a single edge bucket."""
    label: str
    count: int = 0
    wins: int = 0
    total_pnl: float = 0.0
    avg_fair_prob: float = 0.0
    actual_win_rate: float = 0.0
    predicted_win_rate: float = 0.0
    calibration_gap: float = 0.0  # actual - predicted


@dataclass
class SportStats:
    """Per-sport aggregated statistics."""
    sport: str
    total: int = 0
    resolved: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    total_exposure: float = 0.0
    flagged: bool = False
    flag_reason: str = ""


@dataclass
class RiskViolation:
    """A single risk limit violation."""
    violation_type: str
    severity: str  # "critical", "warning"
    detail: str
    token_id: str = ""


@dataclass
class MatchAuditResult:
    """Result of auditing a single match."""
    token_id: str
    slug: str
    sport: str
    fuzzy_score: float
    flagged: bool
    reason: str = ""


@dataclass
class ValidationReport:
    """Full validation report containing all findings."""
    # Edge accuracy
    brier_score: float = 0.0
    edge_buckets: list = field(default_factory=list)
    edge_calibration_alert: bool = False
    edge_calibration_detail: str = ""

    # Phantom detection
    phantom_count: int = 0
    phantom_pnl: float = 0.0
    legitimate_count: int = 0
    legitimate_pnl: float = 0.0
    phantom_exposure_pct: float = 0.0
    phantom_alert: bool = False

    # Sport-level
    sport_stats: list = field(default_factory=list)
    sport_alerts: list = field(default_factory=list)

    # Matching quality
    match_audits: list = field(default_factory=list)
    match_alerts: list = field(default_factory=list)

    # Risk compliance
    risk_violations: list = field(default_factory=list)

    # Capital efficiency
    avg_days_to_resolution: float = 0.0
    median_days_to_resolution: float = 0.0
    capital_utilization: float = 0.0
    stale_positions: list = field(default_factory=list)

    # Summary
    total_positions: int = 0
    open_positions: int = 0
    resolved_positions: int = 0
    total_pnl: float = 0.0
    win_rate: float = 0.0

    @property
    def has_critical_issues(self) -> bool:
        """Return True if any critical issues were found."""
        if self.edge_calibration_alert:
            return True
        if any(v.severity == "critical" for v in self.risk_violations):
            return True
        if self.phantom_alert:
            return True
        return False


# ── Loading ─────────────────────────────────────────────────────────────────

def load_positions(csv_path: str) -> list[PositionRow]:
    """Load positions from CSV file."""
    positions = []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pos = PositionRow(
                token_id=row.get("token_id", ""),
                slug=row.get("slug", ""),
                outcome=row.get("outcome", ""),
                sport=row.get("sport", ""),
                market_type=row.get("market_type", "h2h"),
                entry_price=float(row.get("entry_price", 0)),
                fair_prob=float(row.get("fair_prob", 0)),
                edge_pct=float(row.get("edge_pct", 0)),
                shares=float(row.get("shares", 0)),
                cost_usdc=float(row.get("cost_usdc", 0)),
                bookmaker=row.get("bookmaker", ""),
                opened_at=row.get("opened_at", ""),
                status=row.get("status", "open"),
                resolution_price=float(row.get("resolution_price", 0)),
                payout=float(row.get("payout", 0)),
                pnl=float(row.get("pnl", 0)),
                closed_at=row.get("closed_at", ""),
            )
            positions.append(pos)
    return positions


# ── 1. Edge Accuracy Validation ─────────────────────────────────────────────

def _compute_brier_score(resolved: list[PositionRow]) -> float:
    """Brier score: mean of (fair_prob - outcome)^2 where outcome is 1 for win, 0 for loss."""
    if not resolved:
        return 0.0
    total = 0.0
    for p in resolved:
        actual = 1.0 if p.status == "won" else 0.0
        total += (p.fair_prob - actual) ** 2
    return total / len(resolved)


def _compute_edge_buckets(resolved: list[PositionRow]) -> list[EdgeBucket]:
    """Compute win rate and P&L by edge bucket."""
    buckets_def = [
        ("1-3%", 1.0, 3.0),
        ("3-5%", 3.0, 5.0),
        ("5-10%", 5.0, 10.0),
        ("10%+", 10.0, 999.0),
    ]
    buckets = []
    for label, lo, hi in buckets_def:
        in_bucket = [p for p in resolved if lo <= p.edge_pct < hi]
        b = EdgeBucket(label=label)
        b.count = len(in_bucket)
        if b.count > 0:
            b.wins = sum(1 for p in in_bucket if p.status == "won")
            b.total_pnl = sum(p.pnl for p in in_bucket)
            b.actual_win_rate = b.wins / b.count
            b.avg_fair_prob = sum(p.fair_prob for p in in_bucket) / b.count
            b.predicted_win_rate = b.avg_fair_prob
            b.calibration_gap = b.actual_win_rate - b.predicted_win_rate
        buckets.append(b)
    return buckets


def validate_edge_accuracy(positions: list[PositionRow]) -> dict:
    """Validate edge accuracy for resolved positions."""
    resolved = [p for p in positions if p.status in ("won", "lost")]

    brier = _compute_brier_score(resolved)
    buckets = _compute_edge_buckets(resolved)

    # Flag if any bucket with 10+ trades has actual win rate < predicted - 15pp
    alert = False
    detail = ""
    for b in buckets:
        if b.count >= 10 and b.calibration_gap < -0.15:
            alert = True
            detail = (
                f"Edge bucket {b.label}: actual win rate {b.actual_win_rate:.1%} "
                f"vs predicted {b.predicted_win_rate:.1%} "
                f"(gap = {b.calibration_gap:+.1%}, threshold = -15pp)"
            )
            break

    return {
        "brier_score": brier,
        "edge_buckets": buckets,
        "alert": alert,
        "detail": detail,
    }


# ── 2. Phantom Edge Detection ──────────────────────────────────────────────

def is_phantom(slug: str) -> bool:
    """Check if a slug matches a known phantom edge pattern."""
    slug_lower = slug.lower()
    return any(pat in slug_lower for pat in PHANTOM_PATTERNS)


def validate_phantom_edges(positions: list[PositionRow]) -> dict:
    """Detect phantom edge positions and calculate split P&L."""
    phantoms = [p for p in positions if is_phantom(p.slug)]
    legit = [p for p in positions if not is_phantom(p.slug)]

    phantom_pnl = sum(p.pnl for p in phantoms if p.status in ("won", "lost"))
    legit_pnl = sum(p.pnl for p in legit if p.status in ("won", "lost"))

    total_exposure = sum(p.cost_usdc for p in positions)
    phantom_exposure = sum(p.cost_usdc for p in phantoms)
    phantom_pct = (phantom_exposure / total_exposure * 100) if total_exposure > 0 else 0.0

    return {
        "phantom_count": len(phantoms),
        "phantom_pnl": phantom_pnl,
        "legitimate_count": len(legit),
        "legitimate_pnl": legit_pnl,
        "phantom_exposure_pct": phantom_pct,
        "alert": phantom_pct > 10.0,  # Alert if >10% exposure in phantom trades
    }


# ── 3. Sport-Level Validation ──────────────────────────────────────────────

def validate_by_sport(positions: list[PositionRow]) -> dict:
    """Compute per-sport win rate and P&L, flag underperformers."""
    sport_map: dict[str, list[PositionRow]] = {}
    for p in positions:
        sport = p.sport or "unknown"
        sport_map.setdefault(sport, []).append(p)

    stats_list = []
    alerts = []

    for sport, poss in sorted(sport_map.items()):
        resolved = [p for p in poss if p.status in ("won", "lost")]
        wins = sum(1 for p in resolved if p.status == "won")
        losses = len(resolved) - wins
        wr = wins / len(resolved) if resolved else 0.0
        total_pnl = sum(p.pnl for p in resolved)
        total_exp = sum(p.cost_usdc for p in poss)

        s = SportStats(
            sport=sport,
            total=len(poss),
            resolved=len(resolved),
            wins=wins,
            losses=losses,
            win_rate=wr,
            total_pnl=total_pnl,
            total_exposure=total_exp,
        )

        # Flag: win rate < 40% over 20+ resolved trades
        if len(resolved) >= 20 and wr < 0.40:
            s.flagged = True
            s.flag_reason = f"Win rate {wr:.1%} < 40% over {len(resolved)} resolved trades"
            alerts.append(f"[SPORT] {sport}: {s.flag_reason}")

        # Flag: negative P&L over 30+ resolved trades
        if len(resolved) >= 30 and total_pnl < 0:
            s.flagged = True
            s.flag_reason = f"Negative P&L ${total_pnl:.2f} over {len(resolved)} resolved trades"
            alerts.append(f"[SPORT] {sport}: {s.flag_reason}")

        stats_list.append(s)

    return {"sport_stats": stats_list, "alerts": alerts}


# ── 4. Matching Quality Audit ──────────────────────────────────────────────

def _extract_slug_teams(slug: str) -> list[str]:
    """Extract the two team abbreviations from a slug."""
    parts = slug.split("-")
    if len(parts) >= 3:
        return [parts[1], parts[2]]
    return []


def _slug_team_score(slug_team: str, bookmaker_team: str) -> float:
    """Score how well a slug team abbreviation matches a bookmaker team name.

    Returns a value between 0.0 and 1.0.
    """
    if not slug_team or not bookmaker_team:
        return 0.0

    st = slug_team.lower().strip()
    bt = bookmaker_team.lower().strip()

    # Exact substring match
    if st in bt or bt in st:
        return 1.0

    # Check if it is a known abbreviation (rough check via prefix)
    bt_words = bt.split()
    for w in bt_words:
        if w.startswith(st) or st.startswith(w):
            return 0.9

    # Fuzzy
    return SequenceMatcher(None, st, bt).ratio()


def audit_matching_quality(positions: list[PositionRow]) -> dict:
    """Audit matching quality for open positions."""
    audits = []
    alerts = []

    for p in positions:
        if p.status != "open":
            continue

        slug_teams = _extract_slug_teams(p.slug)
        if not slug_teams:
            continue

        # For each slug team, compute the best match score against the bookmaker info
        # We only have slug and bookmaker name, not the full bookmaker team names in CSV.
        # So we check internal slug consistency and flag suspicious patterns.
        sport = p.sport.lower() if p.sport else ""

        # Check: slug should have a date-like pattern if it is a real match
        has_date = bool(re.search(r'\d{4}-\d{2}-\d{2}', p.slug))

        # For NHL: check if slug teams are valid NHL abbreviations
        if sport == "nhl":
            nhl_abbrevs = {
                "ana", "ari", "bos", "buf", "cgy", "car", "chi", "col",
                "cbj", "dal", "det", "edm", "fla", "lak", "min", "mtl",
                "nsh", "njd", "nyi", "nyr", "ott", "phi", "pit", "sjs",
                "sea", "stl", "tbl", "tor", "van", "vgk", "was", "wpg",
            }
            for st in slug_teams:
                if st.lower() not in nhl_abbrevs:
                    audit = MatchAuditResult(
                        token_id=p.token_id,
                        slug=p.slug,
                        sport=sport,
                        fuzzy_score=0.0,
                        flagged=True,
                        reason=f"NHL slug team '{st}' not in known abbreviation set",
                    )
                    audits.append(audit)
                    alerts.append(f"[MATCH] {p.slug}: {audit.reason}")

        # For tennis: check surname-based matching plausibility
        elif sport in ("atp", "wta"):
            for st in slug_teams:
                # Tennis slugs should be lowercase surnames; flag if too short
                if len(st) < 3:
                    audit = MatchAuditResult(
                        token_id=p.token_id,
                        slug=p.slug,
                        sport=sport,
                        fuzzy_score=0.0,
                        flagged=True,
                        reason=f"Tennis slug team '{st}' is suspiciously short (<3 chars)",
                    )
                    audits.append(audit)
                    alerts.append(f"[MATCH] {p.slug}: {audit.reason}")

        # General: flag if no date in slug
        if not has_date:
            audit = MatchAuditResult(
                token_id=p.token_id,
                slug=p.slug,
                sport=sport,
                fuzzy_score=0.0,
                flagged=True,
                reason="Slug has no date pattern — may not be a real match market",
            )
            audits.append(audit)
            alerts.append(f"[MATCH] {p.slug}: {audit.reason}")

    return {"audits": audits, "alerts": alerts}


# ── 5. Risk Limit Compliance ───────────────────────────────────────────────

def validate_risk_limits(
    positions: list[PositionRow],
    max_position_usdc: float = DEFAULT_MAX_POSITION_USDC,
    max_total_exposure_usdc: float = DEFAULT_MAX_TOTAL_EXPOSURE_USDC,
) -> list[RiskViolation]:
    """Check positions against risk limits."""
    violations = []
    open_positions = [p for p in positions if p.status == "open"]

    # Check per-position size limit
    for p in open_positions:
        if p.cost_usdc > max_position_usdc:
            violations.append(RiskViolation(
                violation_type="position_size",
                severity="critical",
                detail=f"Position ${p.cost_usdc:.2f} exceeds max ${max_position_usdc:.2f}",
                token_id=p.token_id,
            ))

    # Check total exposure
    total_exposure = sum(p.cost_usdc for p in open_positions)
    if total_exposure > max_total_exposure_usdc:
        violations.append(RiskViolation(
            violation_type="total_exposure",
            severity="critical",
            detail=f"Total exposure ${total_exposure:.2f} exceeds max ${max_total_exposure_usdc:.2f}",
        ))

    # Check contradictory positions (both sides of same game)
    slug_outcome_map: dict[str, list[str]] = {}
    for p in open_positions:
        base = re.sub(r'-(?:total|spread)-.*$', '', p.slug)
        slug_outcome_map.setdefault(base, []).append(p.outcome)

    for base_slug, outcomes in slug_outcome_map.items():
        unique = set(o.lower() for o in outcomes)
        # Flag Over+Under, Yes+No on same slug base, or multiple different teams
        if len(unique) > 1:
            # Check if they are truly contradictory
            contradictory_pairs = [
                {"over", "under"},
                {"yes", "no"},
            ]
            for pair in contradictory_pairs:
                if pair.issubset(unique):
                    violations.append(RiskViolation(
                        violation_type="contradictory",
                        severity="critical",
                        detail=f"Contradictory positions on '{base_slug}': {sorted(unique)}",
                    ))
                    break

    # Check duplicate positions (same token_id)
    seen_tokens: dict[str, int] = {}
    for p in open_positions:
        seen_tokens[p.token_id] = seen_tokens.get(p.token_id, 0) + 1
    for tid, count in seen_tokens.items():
        if count > 1:
            violations.append(RiskViolation(
                violation_type="duplicate",
                severity="critical",
                detail=f"Duplicate open position: token_id appears {count} times",
                token_id=tid,
            ))

    return violations


# ── 6. Capital Efficiency ──────────────────────────────────────────────────

def _parse_iso(s: str) -> Optional[datetime]:
    """Parse an ISO datetime string."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def validate_capital_efficiency(
    positions: list[PositionRow],
    max_total_exposure_usdc: float = DEFAULT_MAX_TOTAL_EXPOSURE_USDC,
) -> dict:
    """Compute capital efficiency metrics."""
    resolved = [p for p in positions if p.status in ("won", "lost")]
    open_pos = [p for p in positions if p.status == "open"]
    now = datetime.now(timezone.utc)

    # Days-to-resolution distribution
    days_list = []
    for p in resolved:
        opened = _parse_iso(p.opened_at)
        closed = _parse_iso(p.closed_at)
        if opened and closed:
            delta = (closed - opened).total_seconds() / 86400.0
            days_list.append(delta)

    avg_days = sum(days_list) / len(days_list) if days_list else 0.0
    sorted_days = sorted(days_list)
    median_days = (
        sorted_days[len(sorted_days) // 2] if sorted_days else 0.0
    )

    # Capital utilization
    total_exposure = sum(p.cost_usdc for p in open_pos)
    utilization = (total_exposure / max_total_exposure_usdc) if max_total_exposure_usdc > 0 else 0.0

    # Stale positions: open positions whose slug contains a date that is in the past
    stale = []
    for p in open_pos:
        m = re.search(r'(\d{4}-\d{2}-\d{2})', p.slug)
        if m:
            try:
                event_date = datetime.fromisoformat(m.group(1) + "T23:59:59+00:00")
                if event_date < now:
                    hours_stale = (now - event_date).total_seconds() / 3600.0
                    stale.append({
                        "token_id": p.token_id,
                        "slug": p.slug,
                        "event_date": m.group(1),
                        "hours_past": round(hours_stale, 1),
                    })
            except (ValueError, TypeError):
                pass

    return {
        "avg_days": avg_days,
        "median_days": median_days,
        "utilization": utilization,
        "stale": stale,
        "days_list": days_list,
    }


# ── Main Validation ────────────────────────────────────────────────────────

def validate_all(
    positions_csv_path: str,
    max_position_usdc: float = DEFAULT_MAX_POSITION_USDC,
    max_total_exposure_usdc: float = DEFAULT_MAX_TOTAL_EXPOSURE_USDC,
) -> ValidationReport:
    """Run all validations and return a comprehensive report."""
    positions = load_positions(positions_csv_path)
    return validate_positions(positions, max_position_usdc, max_total_exposure_usdc)


def validate_positions(
    positions: list[PositionRow],
    max_position_usdc: float = DEFAULT_MAX_POSITION_USDC,
    max_total_exposure_usdc: float = DEFAULT_MAX_TOTAL_EXPOSURE_USDC,
) -> ValidationReport:
    """Run all validations on a list of PositionRow objects."""
    report = ValidationReport()

    resolved = [p for p in positions if p.status in ("won", "lost")]
    open_pos = [p for p in positions if p.status == "open"]
    wins = [p for p in resolved if p.status == "won"]

    report.total_positions = len(positions)
    report.open_positions = len(open_pos)
    report.resolved_positions = len(resolved)
    report.total_pnl = sum(p.pnl for p in resolved)
    report.win_rate = len(wins) / len(resolved) if resolved else 0.0

    # 1. Edge accuracy
    edge = validate_edge_accuracy(positions)
    report.brier_score = edge["brier_score"]
    report.edge_buckets = edge["edge_buckets"]
    report.edge_calibration_alert = edge["alert"]
    report.edge_calibration_detail = edge["detail"]

    # 2. Phantom detection
    phantom = validate_phantom_edges(positions)
    report.phantom_count = phantom["phantom_count"]
    report.phantom_pnl = phantom["phantom_pnl"]
    report.legitimate_count = phantom["legitimate_count"]
    report.legitimate_pnl = phantom["legitimate_pnl"]
    report.phantom_exposure_pct = phantom["phantom_exposure_pct"]
    report.phantom_alert = phantom["alert"]

    # 3. Sport-level
    sport = validate_by_sport(positions)
    report.sport_stats = sport["sport_stats"]
    report.sport_alerts = sport["alerts"]

    # 4. Matching quality
    match = audit_matching_quality(positions)
    report.match_audits = match["audits"]
    report.match_alerts = match["alerts"]

    # 5. Risk compliance
    report.risk_violations = validate_risk_limits(
        positions, max_position_usdc, max_total_exposure_usdc
    )

    # 6. Capital efficiency
    cap = validate_capital_efficiency(positions, max_total_exposure_usdc)
    report.avg_days_to_resolution = cap["avg_days"]
    report.median_days_to_resolution = cap["median_days"]
    report.capital_utilization = cap["utilization"]
    report.stale_positions = cap["stale"]

    return report


def print_report(report: ValidationReport):
    """Print a human-readable validation report."""
    sep = "=" * 72

    print(f"\n{sep}")
    print("  TRADING VALIDATION REPORT")
    print(f"{sep}")
    print(f"  Total positions:    {report.total_positions}")
    print(f"  Open:               {report.open_positions}")
    print(f"  Resolved:           {report.resolved_positions}")
    print(f"  Win rate:           {report.win_rate:.1%}")
    print(f"  Realized P&L:       ${report.total_pnl:,.2f}")

    # 1. Edge accuracy
    print(f"\n{'─' * 72}")
    print("  1. EDGE ACCURACY")
    print(f"{'─' * 72}")
    print(f"  Brier score:        {report.brier_score:.4f}")
    if report.edge_buckets:
        print(f"  {'Bucket':<10} {'Count':>6} {'Wins':>6} {'WinRate':>8} "
              f"{'Predicted':>10} {'Gap':>8} {'P&L':>10}")
        for b in report.edge_buckets:
            if b.count > 0:
                print(f"  {b.label:<10} {b.count:>6} {b.wins:>6} "
                      f"{b.actual_win_rate:>7.1%} {b.predicted_win_rate:>9.1%} "
                      f"{b.calibration_gap:>+7.1%} ${b.total_pnl:>9.2f}")
    if report.edge_calibration_alert:
        print(f"  ** ALERT: {report.edge_calibration_detail}")

    # 2. Phantom edges
    print(f"\n{'─' * 72}")
    print("  2. PHANTOM EDGE DETECTION")
    print(f"{'─' * 72}")
    print(f"  Phantom trades:     {report.phantom_count} (P&L: ${report.phantom_pnl:,.2f})")
    print(f"  Legitimate trades:  {report.legitimate_count} (P&L: ${report.legitimate_pnl:,.2f})")
    print(f"  Phantom exposure:   {report.phantom_exposure_pct:.1f}% of total")
    if report.phantom_alert:
        print(f"  ** ALERT: Phantom trades exceed 10% of total exposure")

    # 3. Sport-level
    print(f"\n{'─' * 72}")
    print("  3. SPORT-LEVEL VALIDATION")
    print(f"{'─' * 72}")
    if report.sport_stats:
        print(f"  {'Sport':<10} {'Total':>6} {'Resolved':>9} {'Wins':>6} "
              f"{'WinRate':>8} {'P&L':>10} {'Flag':>6}")
        for s in sorted(report.sport_stats, key=lambda x: -x.total):
            flag = "!!" if s.flagged else ""
            wr_str = f"{s.win_rate:.1%}" if s.resolved > 0 else "n/a"
            print(f"  {s.sport:<10} {s.total:>6} {s.resolved:>9} {s.wins:>6} "
                  f"{wr_str:>8} ${s.total_pnl:>9.2f} {flag:>6}")
    for alert in report.sport_alerts:
        print(f"  ** ALERT: {alert}")

    # 4. Matching quality
    print(f"\n{'─' * 72}")
    print("  4. MATCHING QUALITY AUDIT")
    print(f"{'─' * 72}")
    flagged_audits = [a for a in report.match_audits if a.flagged]
    print(f"  Flagged matches:    {len(flagged_audits)}")
    for a in flagged_audits[:10]:
        print(f"    {a.slug}: {a.reason}")
    for alert in report.match_alerts[:5]:
        print(f"  ** ALERT: {alert}")

    # 5. Risk compliance
    print(f"\n{'─' * 72}")
    print("  5. RISK LIMIT COMPLIANCE")
    print(f"{'─' * 72}")
    if not report.risk_violations:
        print("  All risk limits OK")
    else:
        for v in report.risk_violations:
            sev = "CRITICAL" if v.severity == "critical" else "WARNING"
            print(f"  [{sev}] {v.violation_type}: {v.detail}")

    # 6. Capital efficiency
    print(f"\n{'─' * 72}")
    print("  6. CAPITAL EFFICIENCY")
    print(f"{'─' * 72}")
    print(f"  Avg days to resolution:    {report.avg_days_to_resolution:.1f}")
    print(f"  Median days to resolution: {report.median_days_to_resolution:.1f}")
    print(f"  Capital utilization:       {report.capital_utilization:.1%}")
    if report.stale_positions:
        print(f"  Stale positions (past event date): {len(report.stale_positions)}")
        for sp in report.stale_positions[:5]:
            print(f"    {sp['slug']}: event {sp['event_date']}, {sp['hours_past']:.0f}h past")

    # Final verdict
    print(f"\n{sep}")
    if report.has_critical_issues:
        print("  VERDICT: CRITICAL ISSUES FOUND")
    else:
        print("  VERDICT: ALL CHECKS PASSED")
    print(f"{sep}\n")


# ── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Validate Polymarket trading results")
    parser.add_argument("--csv", required=True, help="Path to my_positions.csv")
    parser.add_argument("--max-position", type=float, default=DEFAULT_MAX_POSITION_USDC,
                        help=f"Max position size in USDC (default: {DEFAULT_MAX_POSITION_USDC})")
    parser.add_argument("--max-exposure", type=float, default=DEFAULT_MAX_TOTAL_EXPOSURE_USDC,
                        help=f"Max total exposure in USDC (default: {DEFAULT_MAX_TOTAL_EXPOSURE_USDC})")
    args = parser.parse_args()

    report = validate_all(args.csv, args.max_position, args.max_exposure)
    print_report(report)

    if report.has_critical_issues:
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main()
