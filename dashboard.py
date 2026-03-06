#!/usr/bin/env python3
"""Polymarket Arb Bot - Paper Trading Dashboard (Enhanced).

Single-file Flask dashboard for monitoring the Polymarket sports arbitrage
paper trading bot. Reads data from CSV/JSON files and bot.log.

Features:
- Summary cards with extended stats (Sharpe, drawdown, profit factor, etc.)
- Cumulative PnL, daily PnL, rolling win rate, equity curve charts
- Edge distribution histogram and calibration chart
- Sport heatmap with color-coded metrics
- Recent activity feed with timeline
- Live Polymarket links for every position
- Sortable tables with unrealized PnL
- Learning agent deep stats
- Mobile-responsive design

Usage:
    python dashboard.py
    python dashboard.py --port 8080
"""
import argparse
import csv
import json
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from flask import Flask, jsonify, Response, request

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
POSITIONS_FILE = DATA_DIR / "my_positions.csv"
TRADES_FILE = DATA_DIR / "my_trades.csv"
LEARNING_FILE = DATA_DIR / "learning_history.json"
LOG_FILE = BASE_DIR / "bot.log"

STARTING_CAPITAL = 500.0  # $500 test wallet


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> list[dict]:
    """Read a CSV file and return a list of dicts. Returns [] if missing."""
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _safe_int(val, default=0):
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return default


def _bot_status() -> str:
    """Return 'Running' if bot.log was modified within last 10 minutes."""
    if not LOG_FILE.exists():
        return "Stopped"
    mtime = os.path.getmtime(LOG_FILE)
    if time.time() - mtime < 600:
        return "Running"
    return "Stopped"


def _is_live_trading() -> bool:
    """Check if the bot is in live mode (not paper/dry-run)."""
    if not LOG_FILE.exists():
        return False
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        # Check last 200 lines for DRY RUN indicator
        for line in lines[-200:]:
            if "[DRY RUN]" in line:
                return False
            if "dry_run=False" in line:
                return True
        return False  # default paper
    except Exception:
        return False


def _rn1_tracker_status() -> dict:
    """Return RN1 tracker status: alive, last_poll, trades info."""
    summary_path = DATA_DIR / "rn1_live_summary.json"
    log_path = BASE_DIR / "rn1_tracker.log"

    result = {"alive": False, "last_poll": None, "last_poll_ago": None,
              "trades_last_5m": 0, "trades_last_15m": 0, "active_markets": 0}

    # Check summary file freshness
    if summary_path.exists():
        age = time.time() - os.path.getmtime(summary_path)
        result["alive"] = age < 120  # alive if updated in last 2 min
        result["last_poll_ago"] = int(age)
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            result["last_poll"] = data.get("last_poll")
            result["trades_last_5m"] = data.get("trades_last_5m", 0)
            result["trades_last_15m"] = data.get("trades_last_15m", 0)
            result["active_markets"] = len(data.get("active_markets", []))
        except Exception:
            pass

    return result


def _read_log_lines(n: int = 80) -> list[str]:
    if not LOG_FILE.exists():
        return ["(no log file found)"]
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [l.rstrip() for l in lines[-n:]]
    except Exception as e:
        return [f"Error reading log: {e}"]


def _load_learning() -> dict:
    if not LEARNING_FILE.exists():
        return {}
    try:
        with open(LEARNING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_resolved() -> list[dict]:
    """Return resolved positions sorted by closed_at."""
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]
    resolved.sort(key=lambda p: p.get("closed_at", ""))
    return resolved


def _polymarket_url(slug: str) -> str:
    """Build a Polymarket event URL from a market slug.

    Sports market slugs: {sport}-{team1}-{team2}-YYYY-MM-DD[-suffix]
    Always use the base event slug (up to the date). Polymarket redirects
    /event/{base} to the correct /sports/{league}/{base} page.
    """
    if not slug:
        return "#"
    import re
    m = re.match(r'^([a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})', slug)
    if m:
        return f"https://polymarket.com/event/{m.group(1)}"
    return f"https://polymarket.com/event/{slug}"


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/summary")
def api_summary():
    positions = _read_csv(POSITIONS_FILE)
    open_pos = [p for p in positions if p.get("status", "").lower() == "open"]
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]

    total_pnl = sum(_safe_float(p.get("pnl")) for p in resolved)
    wins = sum(1 for p in resolved if p.get("status", "").lower() == "won")
    losses = sum(1 for p in resolved if p.get("status", "").lower() == "lost")
    total_resolved = wins + losses
    win_rate = (wins / total_resolved * 100) if total_resolved > 0 else 0.0

    total_exposure = sum(_safe_float(p.get("cost_usdc")) for p in open_pos)
    total_trades = len(positions)

    best_trade_pnl = max((_safe_float(p.get("pnl")) for p in resolved), default=0.0)

    rn1 = _rn1_tracker_status()
    return jsonify({
        "bot_status": _bot_status(),
        "live_trading": _is_live_trading(),
        "rn1_tracker": rn1,
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "wins": wins,
        "losses": losses,
        "open_count": len(open_pos),
        "total_exposure": round(total_exposure, 2),
        "total_trades": total_trades,
        "best_trade": round(best_trade_pnl, 2),
        "starting_capital": STARTING_CAPITAL,
        "utc_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/positions")
def api_positions():
    positions = _read_csv(POSITIONS_FILE)
    open_pos = [p for p in positions if p.get("status", "").lower() == "open"]
    # Add polymarket URL and time held
    now = datetime.now(timezone.utc)
    for p in open_pos:
        p["polymarket_url"] = _polymarket_url(p.get("slug", ""))
        opened = p.get("opened_at", "")
        if opened:
            try:
                dt = datetime.fromisoformat(opened)
                delta = now - dt
                hours = delta.total_seconds() / 3600
                if hours < 1:
                    p["time_held"] = f"{int(delta.total_seconds()/60)}m"
                elif hours < 24:
                    p["time_held"] = f"{hours:.1f}h"
                else:
                    p["time_held"] = f"{delta.days}d {int(hours%24)}h"
            except Exception:
                p["time_held"] = ""
        else:
            p["time_held"] = ""
        # Expected payout if wins
        shares = _safe_float(p.get("shares"))
        cost = _safe_float(p.get("cost_usdc"))
        p["expected_payout"] = round(shares, 2)
        p["expected_profit"] = round(shares - cost, 2)
    open_pos.sort(key=lambda p: _safe_float(p.get("cost_usdc")), reverse=True)
    return jsonify(open_pos)


@app.route("/api/resolved")
def api_resolved():
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]
    for p in resolved:
        p["polymarket_url"] = _polymarket_url(p.get("slug", ""))
    resolved.sort(key=lambda p: p.get("closed_at", ""), reverse=True)
    return jsonify(resolved[:100])


@app.route("/api/log")
def api_log():
    return jsonify({"lines": _read_log_lines(80)})


@app.route("/api/sports")
def api_sports():
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]

    sports: dict[str, dict] = {}
    for p in resolved:
        sport = p.get("sport", "unknown")
        if sport not in sports:
            sports[sport] = {"sport": sport, "count": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                             "total_cost": 0.0, "total_edge": 0.0}
        sports[sport]["count"] += 1
        if p.get("status", "").lower() == "won":
            sports[sport]["wins"] += 1
        else:
            sports[sport]["losses"] += 1
        sports[sport]["pnl"] += _safe_float(p.get("pnl"))
        sports[sport]["total_cost"] += _safe_float(p.get("cost_usdc"))
        sports[sport]["total_edge"] += _safe_float(p.get("edge_pct"))

    result = sorted(sports.values(), key=lambda s: s["pnl"], reverse=True)
    for r in result:
        r["pnl"] = round(r["pnl"], 2)
        total = r["wins"] + r["losses"]
        r["win_rate"] = round(r["wins"] / total * 100, 1) if total > 0 else 0.0
        r["avg_edge"] = round(r["total_edge"] / r["count"], 1) if r["count"] > 0 else 0.0
        r["roi"] = round(r["pnl"] / r["total_cost"] * 100, 1) if r["total_cost"] > 0 else 0.0
        del r["total_cost"]
        del r["total_edge"]
    return jsonify(result)


@app.route("/api/market_types")
def api_market_types():
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]

    types: dict[str, dict] = {}
    for p in resolved:
        mt = p.get("market_type", "unknown")
        if mt not in types:
            types[mt] = {"market_type": mt, "count": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        types[mt]["count"] += 1
        if p.get("status", "").lower() == "won":
            types[mt]["wins"] += 1
        else:
            types[mt]["losses"] += 1
        types[mt]["pnl"] += _safe_float(p.get("pnl"))

    result = sorted(types.values(), key=lambda s: s["pnl"], reverse=True)
    for r in result:
        r["pnl"] = round(r["pnl"], 2)
        total = r["wins"] + r["losses"]
        r["win_rate"] = round(r["wins"] / total * 100, 1) if total > 0 else 0.0
    return jsonify(result)


@app.route("/api/learning")
def api_learning():
    data = _load_learning()
    if not data:
        return jsonify({"available": False})

    trades = data.get("trades", [])
    trade_count = data.get("trade_count", len(trades))

    if not trades:
        return jsonify({"available": True, "trade_count": trade_count})

    wins = sum(1 for t in trades if t.get("won"))
    win_rate = (wins / len(trades) * 100) if trades else 0.0

    # Sport stats with confidence
    sport_stats: dict[str, dict] = {}
    for t in trades:
        s = t.get("sport", "unknown")
        if s not in sport_stats:
            sport_stats[s] = {"wins": 0, "total": 0, "edges": [], "pnls": []}
        sport_stats[s]["total"] += 1
        if t.get("won"):
            sport_stats[s]["wins"] += 1
        sport_stats[s]["edges"].append(_safe_float(t.get("edge_pct")))
        sport_stats[s]["pnls"].append(_safe_float(t.get("pnl")))

    best_sport = ""
    best_sport_wr = 0.0
    sport_detail = []
    for s, v in sport_stats.items():
        wr = v["wins"] / v["total"] * 100 if v["total"] > 0 else 0
        confident = v["total"] >= 20
        sport_detail.append({
            "sport": s,
            "total": v["total"],
            "wins": v["wins"],
            "win_rate": round(wr, 1),
            "avg_edge": round(sum(v["edges"]) / len(v["edges"]), 1) if v["edges"] else 0,
            "total_pnl": round(sum(v["pnls"]), 2),
            "confident": confident,
        })
        if wr > best_sport_wr and v["total"] >= 3:
            best_sport_wr = wr
            best_sport = s

    # Market type stats
    mt_stats: dict[str, dict] = {}
    for t in trades:
        m = t.get("market_type", "unknown")
        if m not in mt_stats:
            mt_stats[m] = {"wins": 0, "total": 0}
        mt_stats[m]["total"] += 1
        if t.get("won"):
            mt_stats[m]["wins"] += 1

    best_mt = ""
    best_mt_wr = 0.0
    for m, v in mt_stats.items():
        wr = v["wins"] / v["total"] * 100 if v["total"] > 0 else 0
        if wr > best_mt_wr and v["total"] >= 3:
            best_mt_wr = wr
            best_mt = m

    # Edge adjustment history
    edge_adjustments = data.get("edge_adjustments", [])

    return jsonify({
        "available": True,
        "trade_count": trade_count,
        "win_rate": round(win_rate, 1),
        "best_sport": best_sport,
        "best_sport_wr": round(best_sport_wr, 1),
        "best_market_type": best_mt,
        "best_mt_wr": round(best_mt_wr, 1),
        "sport_detail": sorted(sport_detail, key=lambda x: x["total_pnl"], reverse=True),
        "edge_adjustments": edge_adjustments[-50:] if edge_adjustments else [],
    })


@app.route("/api/pnl_series")
def api_pnl_series():
    """Cumulative PnL series for line chart."""
    resolved = _get_resolved()
    if not resolved:
        return jsonify([])

    series = []
    cum_pnl = 0.0
    for i, p in enumerate(resolved):
        pnl = _safe_float(p.get("pnl"))
        cum_pnl += pnl
        closed = p.get("closed_at", "")
        series.append({
            "trade_num": i + 1,
            "date": closed[:10] if closed else "",
            "pnl": round(pnl, 2),
            "cumulative_pnl": round(cum_pnl, 2),
            "equity": round(STARTING_CAPITAL + cum_pnl, 2),
        })
    return jsonify(series)


@app.route("/api/daily_pnl")
def api_daily_pnl():
    """Daily PnL aggregation."""
    resolved = _get_resolved()
    if not resolved:
        return jsonify([])

    daily: dict[str, dict] = {}
    for p in resolved:
        closed = p.get("closed_at", "")
        date = closed[:10] if closed else "unknown"
        if date not in daily:
            daily[date] = {"date": date, "pnl": 0.0, "trades": 0, "wins": 0}
        daily[date]["pnl"] += _safe_float(p.get("pnl"))
        daily[date]["trades"] += 1
        if p.get("status", "").lower() == "won":
            daily[date]["wins"] += 1

    result = sorted(daily.values(), key=lambda d: d["date"])
    for r in result:
        r["pnl"] = round(r["pnl"], 2)
    return jsonify(result)


@app.route("/api/calibration")
def api_calibration():
    """Calibration data: predicted vs actual win rate by entry price bucket."""
    resolved = _get_resolved()
    if not resolved:
        return jsonify([])

    buckets = {
        "0-10c": {"min": 0, "max": 0.10, "predicted_total": 0.0, "wins": 0, "count": 0},
        "10-20c": {"min": 0.10, "max": 0.20, "predicted_total": 0.0, "wins": 0, "count": 0},
        "20-40c": {"min": 0.20, "max": 0.40, "predicted_total": 0.0, "wins": 0, "count": 0},
        "40-60c": {"min": 0.40, "max": 0.60, "predicted_total": 0.0, "wins": 0, "count": 0},
        "60-80c": {"min": 0.60, "max": 0.80, "predicted_total": 0.0, "wins": 0, "count": 0},
        "80-100c": {"min": 0.80, "max": 1.00, "predicted_total": 0.0, "wins": 0, "count": 0},
    }

    for p in resolved:
        entry = _safe_float(p.get("entry_price"))
        fair = _safe_float(p.get("fair_prob"))
        won = p.get("status", "").lower() == "won"
        for name, b in buckets.items():
            if b["min"] <= entry < b["max"] or (b["max"] == 1.0 and entry == 1.0):
                b["count"] += 1
                b["predicted_total"] += fair
                if won:
                    b["wins"] += 1
                break

    result = []
    for name, b in buckets.items():
        if b["count"] > 0:
            result.append({
                "bucket": name,
                "predicted_wr": round(b["predicted_total"] / b["count"] * 100, 1),
                "actual_wr": round(b["wins"] / b["count"] * 100, 1),
                "count": b["count"],
            })
    return jsonify(result)


@app.route("/api/stats")
def api_stats():
    """Extended performance statistics."""
    resolved = _get_resolved()
    positions = _read_csv(POSITIONS_FILE)
    open_pos = [p for p in positions if p.get("status", "").lower() == "open"]

    if not resolved:
        return jsonify({
            "sharpe": 0.0, "max_dd_dollar": 0.0, "max_dd_pct": 0.0,
            "profit_factor": 0.0, "avg_winner": 0.0, "avg_loser": 0.0,
            "best_trade": None, "worst_trade": None,
            "current_streak": {"type": "none", "count": 0},
            "avg_hold_time_hours": 0.0, "roi": 0.0,
            "total_capital_deployed": 0.0, "open_exposure": 0.0,
            "total_resolved": 0, "win_count": 0, "loss_count": 0,
        })

    pnls = [_safe_float(p.get("pnl")) for p in resolved]
    wins_pnl = [x for x in pnls if x > 0]
    loss_pnl = [x for x in pnls if x <= 0]

    # Sharpe ratio (annualized, assuming ~1 trade/day average)
    mean_pnl = sum(pnls) / len(pnls) if pnls else 0
    if len(pnls) > 1:
        var = sum((x - mean_pnl) ** 2 for x in pnls) / (len(pnls) - 1)
        std_pnl = math.sqrt(var) if var > 0 else 0.001
    else:
        std_pnl = 0.001
    sharpe = (mean_pnl / std_pnl) * math.sqrt(365) if std_pnl > 0 else 0.0

    # Max drawdown
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for pnl in pnls:
        cum += pnl
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = (max_dd / (STARTING_CAPITAL + peak) * 100) if (STARTING_CAPITAL + peak) > 0 else 0.0

    # Profit factor
    gross_wins = sum(wins_pnl)
    gross_losses = abs(sum(loss_pnl))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float('inf') if gross_wins > 0 else 0.0

    # Average winner / loser
    avg_winner = (sum(wins_pnl) / len(wins_pnl)) if wins_pnl else 0.0
    avg_loser = (sum(loss_pnl) / len(loss_pnl)) if loss_pnl else 0.0

    # Best / worst trade
    best_idx = pnls.index(max(pnls))
    worst_idx = pnls.index(min(pnls))
    best_trade = {
        "slug": resolved[best_idx].get("slug", ""),
        "pnl": round(pnls[best_idx], 2),
        "sport": resolved[best_idx].get("sport", ""),
        "url": _polymarket_url(resolved[best_idx].get("slug", "")),
    }
    worst_trade = {
        "slug": resolved[worst_idx].get("slug", ""),
        "pnl": round(pnls[worst_idx], 2),
        "sport": resolved[worst_idx].get("sport", ""),
        "url": _polymarket_url(resolved[worst_idx].get("slug", "")),
    }

    # Current streak
    streak_type = "won" if resolved[-1].get("status", "").lower() == "won" else "lost"
    streak_count = 0
    for p in reversed(resolved):
        if p.get("status", "").lower() == streak_type:
            streak_count += 1
        else:
            break

    # Average hold time
    hold_times = []
    for p in resolved:
        opened = p.get("opened_at", "")
        closed = p.get("closed_at", "")
        if opened and closed:
            try:
                dt_open = datetime.fromisoformat(opened)
                dt_close = datetime.fromisoformat(closed)
                hold_times.append((dt_close - dt_open).total_seconds() / 3600)
            except Exception:
                pass
    avg_hold = (sum(hold_times) / len(hold_times)) if hold_times else 0.0

    # ROI
    total_deployed = sum(_safe_float(p.get("cost_usdc")) for p in resolved)
    total_pnl = sum(pnls)
    roi = (total_pnl / total_deployed * 100) if total_deployed > 0 else 0.0

    open_exposure = sum(_safe_float(p.get("cost_usdc")) for p in open_pos)

    return jsonify({
        "sharpe": round(sharpe, 2),
        "max_dd_dollar": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float('inf') else "Inf",
        "avg_winner": round(avg_winner, 2),
        "avg_loser": round(avg_loser, 2),
        "best_trade": best_trade,
        "worst_trade": worst_trade,
        "current_streak": {"type": streak_type, "count": streak_count},
        "avg_hold_time_hours": round(avg_hold, 1),
        "roi": round(roi, 1),
        "total_capital_deployed": round(total_deployed, 2),
        "open_exposure": round(open_exposure, 2),
        "total_resolved": len(resolved),
        "win_count": len(wins_pnl),
        "loss_count": len(loss_pnl),
    })


@app.route("/api/activity")
def api_activity():
    """Recent activity feed from trades CSV."""
    trades = _read_csv(TRADES_FILE)
    if not trades:
        return jsonify([])

    # Sort by timestamp descending, take last 30
    trades.sort(key=lambda t: t.get("timestamp", ""), reverse=True)
    recent = trades[:30]

    feed = []
    for t in recent:
        trade_type = t.get("type", "UNKNOWN")
        slug = t.get("slug", "")
        outcome = t.get("outcome", "")
        sport = t.get("sport", "")
        entry = _safe_float(t.get("entry_price"))
        cost = _safe_float(t.get("cost_usdc"))
        pnl = _safe_float(t.get("pnl"))
        status = t.get("status", "")
        timestamp = t.get("timestamp", "")

        if trade_type == "OPEN":
            desc = f"Opened: {sport.upper()} {slug} [{outcome}] @ {entry:.3f} (${cost:.2f})"
            icon = "open"
        elif trade_type in ("WON", "LOST", "RESOLVED"):
            won = trade_type == "WON" or status.lower() == "won"
            result = "WON" if won else "LOST"
            desc = f"Resolved: {sport.upper()} {slug} [{outcome}] {result} {'+' if pnl >= 0 else ''}{pnl:.2f}"
            icon = "won" if won else "lost"
        elif trade_type == "MERGE":
            desc = f"Merge: {slug} merged for ${pnl:.2f} profit"
            icon = "merge"
        else:
            desc = f"{trade_type}: {slug} [{outcome}]"
            icon = "other"

        feed.append({
            "timestamp": timestamp,
            "type": icon,
            "description": desc,
            "pnl": round(pnl, 2),
            "slug": slug,
            "url": _polymarket_url(slug),
        })

    return jsonify(feed)


@app.route("/api/edge_distribution")
def api_edge_distribution():
    """Edge % distribution at entry for all trades."""
    positions = _read_csv(POSITIONS_FILE)
    if not positions:
        return jsonify([])

    edges = [_safe_float(p.get("edge_pct")) for p in positions]
    if not edges:
        return jsonify([])

    # Create buckets
    bucket_defs = [
        ("0-5%", 0, 5), ("5-10%", 5, 10), ("10-15%", 10, 15),
        ("15-20%", 15, 20), ("20-30%", 20, 30), ("30-50%", 30, 50),
        ("50%+", 50, 999),
    ]
    result = []
    for name, lo, hi in bucket_defs:
        count = sum(1 for e in edges if lo <= e < hi)
        result.append({"bucket": name, "count": count})
    return jsonify(result)


@app.route("/api/sport_heatmap")
def api_sport_heatmap():
    """Sport x metric heatmap data."""
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]
    open_pos = [p for p in positions if p.get("status", "").lower() == "open"]

    sports: dict[str, dict] = {}
    # Include open positions in counts
    for p in positions:
        sport = p.get("sport", "unknown")
        if sport not in sports:
            sports[sport] = {"sport": sport, "total": 0, "wins": 0, "losses": 0,
                             "pnl": 0.0, "cost": 0.0, "edge_sum": 0.0, "open": 0}
        sports[sport]["total"] += 1
        sports[sport]["edge_sum"] += _safe_float(p.get("edge_pct"))
        status = p.get("status", "").lower()
        if status == "won":
            sports[sport]["wins"] += 1
            sports[sport]["pnl"] += _safe_float(p.get("pnl"))
            sports[sport]["cost"] += _safe_float(p.get("cost_usdc"))
        elif status == "lost":
            sports[sport]["losses"] += 1
            sports[sport]["pnl"] += _safe_float(p.get("pnl"))
            sports[sport]["cost"] += _safe_float(p.get("cost_usdc"))
        elif status == "open":
            sports[sport]["open"] += 1

    result = []
    for s, v in sports.items():
        resolved_count = v["wins"] + v["losses"]
        result.append({
            "sport": s,
            "count": v["total"],
            "open": v["open"],
            "win_rate": round(v["wins"] / resolved_count * 100, 1) if resolved_count > 0 else None,
            "avg_edge": round(v["edge_sum"] / v["total"], 1) if v["total"] > 0 else 0,
            "pnl": round(v["pnl"], 2),
            "roi": round(v["pnl"] / v["cost"] * 100, 1) if v["cost"] > 0 else None,
        })
    result.sort(key=lambda x: x["count"], reverse=True)
    return jsonify(result)


# ---------------------------------------------------------------------------
# RN1 Insights API
# ---------------------------------------------------------------------------

_rn1_analyzer_cache = None

def _get_rn1_analyzer():
    """Lazily load the RN1 analyzer (cached)."""
    global _rn1_analyzer_cache
    if _rn1_analyzer_cache is None:
        try:
            from src.rn1_analyzer import RN1Analyzer
            _rn1_analyzer_cache = RN1Analyzer()
        except Exception as e:
            return None
    return _rn1_analyzer_cache


@app.route("/api/rn1")
def api_rn1():
    """RN1 pattern insights endpoint."""
    analyzer = _get_rn1_analyzer()
    if analyzer is None:
        return jsonify({"error": "RN1 analyzer not available"}), 503

    return jsonify({
        "top_sports": analyzer.top_sports_by_profit()[:15],
        "entry_price_distribution": analyzer.entry_price_distribution(),
        "merge_stats": {
            "count": analyzer.merge_patterns().get("count", 0),
            "total_usdc": analyzer.merge_patterns().get("total_usdc", 0),
            "avg_size": analyzer.merge_patterns().get("avg_size", 0),
            "unique_slugs": analyzer.merge_patterns().get("unique_slugs", 0),
        },
        "market_types": analyzer.market_type_preferences(),
        "time_of_day": analyzer.time_of_day_patterns(),
        "holding_periods": analyzer.holding_period_analysis(),
        "position_sizing": analyzer.position_sizing_patterns().get("overall", {}),
        "record_counts": analyzer.patterns.get("record_counts", {}),
        "computed_at": analyzer.patterns.get("computed_at", ""),
    })


# ---------------------------------------------------------------------------
# RN1 Live Activity API
# ---------------------------------------------------------------------------

RN1_LIVE_SUMMARY = DATA_DIR / "rn1_live_summary.json"
RN1_LIVE_TRADES = DATA_DIR / "rn1_live_trades.json"


def _read_json_file(path: Path) -> dict | list | None:
    """Safely read a JSON file, return None on any error."""
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


@app.route("/api/rn1_live")
def api_rn1_live():
    """RN1 live activity endpoint — market discovery data.

    Returns active market count, hot markets, and last 15 activity events.
    Activity events show type/slug/timestamp only — NOT trade details we'd copy.
    """
    summary = _read_json_file(RN1_LIVE_SUMMARY)
    trades_raw = _read_json_file(RN1_LIVE_TRADES)

    if summary is None:
        summary = {
            "last_poll": None,
            "active_markets": [],
            "hot_markets": [],
            "new_markets": [],
            "trades_last_5m": 0,
            "trades_last_15m": 0,
            "total_buffered": 0,
        }

    # Extract last 15 activity events (type/slug/timestamp only — no direction)
    recent_activity = []
    if isinstance(trades_raw, list):
        # Sort by timestamp descending, take last 15
        sorted_trades = sorted(trades_raw, key=lambda t: t.get("timestamp", 0), reverse=True)
        for t in sorted_trades[:15]:
            recent_activity.append({
                "type": t.get("type", ""),
                "slug": t.get("slug", ""),
                "title": t.get("title", ""),
                "timestamp": t.get("timestamp", 0),
                "datetime": t.get("datetime", ""),
                "usdc_size": round(t.get("usdc_size", 0) or 0, 2),
            })

    # Check if tracker is alive (summary updated in last 2 minutes)
    tracker_alive = False
    if summary.get("last_poll"):
        try:
            from datetime import datetime as _dt
            last = _dt.fromisoformat(summary["last_poll"].replace("Z", "+00:00"))
            age = (datetime.now(timezone) - last).total_seconds() if hasattr(timezone, '__call__') else 999
            # Simpler: check file mtime
            if RN1_LIVE_SUMMARY.exists():
                age = time.time() - os.path.getmtime(RN1_LIVE_SUMMARY)
                tracker_alive = age < 120
        except Exception:
            if RN1_LIVE_SUMMARY.exists():
                tracker_alive = (time.time() - os.path.getmtime(RN1_LIVE_SUMMARY)) < 120

    # Check which of our open positions overlap with RN1 active markets
    rn1_active_set = set(summary.get("active_markets", []))
    our_positions = _read_csv(POSITIONS_FILE)
    overlap_slugs = []
    for p in our_positions:
        status = p.get("status", "").lower()
        slug = p.get("slug", "")
        if status in ("open", "pending") and slug in rn1_active_set:
            overlap_slugs.append(slug)

    return jsonify({
        "tracker_alive": tracker_alive,
        "active_market_count": len(summary.get("active_markets", [])),
        "active_markets": summary.get("active_markets", []),
        "hot_markets": summary.get("hot_markets", []),
        "new_markets": summary.get("new_markets", []),
        "trades_last_5m": summary.get("trades_last_5m", 0),
        "trades_last_15m": summary.get("trades_last_15m", 0),
        "total_buffered": summary.get("total_buffered", 0),
        "recent_activity": recent_activity,
        "our_positions_in_rn1_markets": overlap_slugs,
    })


# ---------------------------------------------------------------------------
# HTML Dashboard
# ---------------------------------------------------------------------------

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Polymarket Arb Bot - Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root {
    --bg: #1a1a2e;
    --card-bg: #16213e;
    --card-border: #0f3460;
    --text: #e0e0e0;
    --text-muted: #8892a4;
    --green: #00d4aa;
    --red: #ff6b6b;
    --yellow: #ffd93d;
    --blue: #4fc3f7;
    --purple: #b388ff;
    --orange: #ffab40;
    --header-bg: #0f3460;
    --hover-bg: rgba(79,195,247,0.05);
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .mono { font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; }
  a { color: var(--blue); text-decoration: none; }
  a:hover { text-decoration: underline; color: var(--green); }

  /* Header */
  .header {
    background: var(--header-bg);
    padding: 16px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    border-bottom: 2px solid var(--green);
    position: sticky;
    top: 0;
    z-index: 100;
  }
  .header h1 { font-size: 20px; font-weight: 700; color: #fff; letter-spacing: 0.5px; }
  .header h1 span { color: var(--green); }
  .header-right { display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  .badge {
    padding: 4px 12px; border-radius: 12px; font-size: 12px;
    font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;
  }
  .badge-paper { background: var(--yellow); color: #1a1a2e; }

  /* Traffic Light System */
  .traffic-lights {
    display: flex;
    gap: 14px;
    align-items: center;
    background: rgba(0,0,0,0.3);
    padding: 6px 14px;
    border-radius: 8px;
    border: 1px solid var(--card-border);
  }
  .tl-item {
    display: flex;
    align-items: center;
    gap: 6px;
    cursor: default;
  }
  .tl-dot {
    width: 12px;
    height: 12px;
    border-radius: 50%;
    display: inline-block;
    box-shadow: 0 0 4px rgba(0,0,0,0.3);
  }
  .tl-green { background: var(--green); box-shadow: 0 0 8px rgba(0,212,170,0.6); }
  .tl-yellow { background: var(--yellow); box-shadow: 0 0 8px rgba(255,217,61,0.5); }
  .tl-red { background: #ff4444; box-shadow: 0 0 6px rgba(255,68,68,0.4); }
  .tl-label { font-size: 11px; font-weight: 600; color: var(--text-muted); text-transform: uppercase; letter-spacing: 0.5px; }
  .tl-detail { font-size: 11px; color: var(--text); font-family: 'Cascadia Code', monospace; }
  .badge-running { background: var(--green); color: #1a1a2e; }
  .badge-stopped { background: var(--red); color: #fff; }
  .utc-time { color: var(--text-muted); font-size: 13px; }
  .last-updated { color: var(--text-muted); font-size: 11px; text-align: right; margin-bottom: 8px; }

  /* Tab navigation */
  .tab-nav {
    display: flex; gap: 4px; margin-bottom: 20px;
    overflow-x: auto; padding-bottom: 4px;
  }
  .tab-btn {
    background: var(--card-bg); border: 1px solid var(--card-border);
    color: var(--text-muted); padding: 8px 18px; border-radius: 8px 8px 0 0;
    cursor: pointer; font-size: 13px; font-weight: 600; white-space: nowrap;
    transition: all 0.2s;
  }
  .tab-btn:hover { color: var(--text); background: rgba(15,52,96,0.8); }
  .tab-btn.active { color: var(--green); border-bottom-color: var(--bg); background: var(--bg); }
  .tab-content { display: none; }
  .tab-content.active { display: block; }

  /* Container */
  .container { max-width: 1500px; margin: 0 auto; padding: 20px; }

  /* Summary cards */
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }
  .card {
    background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: 10px; padding: 14px; text-align: center;
    transition: transform 0.15s, box-shadow 0.15s;
  }
  .card:hover { transform: translateY(-2px); box-shadow: 0 4px 16px rgba(0,0,0,0.3); }
  .card-label {
    font-size: 11px; color: var(--text-muted); text-transform: uppercase;
    letter-spacing: 1px; margin-bottom: 6px;
  }
  .card-value {
    font-size: 24px; font-weight: 700;
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  }
  .card-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
  .pnl-pos { color: var(--green); }
  .pnl-neg { color: var(--red); }

  /* Sections */
  .section {
    background: var(--card-bg); border: 1px solid var(--card-border);
    border-radius: 10px; padding: 20px; margin-bottom: 20px;
  }
  .section h2 {
    font-size: 15px; font-weight: 600; margin-bottom: 14px;
    color: var(--blue); border-bottom: 1px solid var(--card-border); padding-bottom: 8px;
  }

  /* Tables */
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th {
    text-align: left; padding: 8px 10px; color: var(--text-muted);
    font-weight: 600; font-size: 11px; text-transform: uppercase;
    letter-spacing: 0.5px; border-bottom: 1px solid var(--card-border);
    cursor: pointer; user-select: none; white-space: nowrap;
  }
  th:hover { color: var(--blue); }
  th .sort-arrow { font-size: 10px; margin-left: 3px; opacity: 0.5; }
  th.sorted .sort-arrow { opacity: 1; color: var(--green); }
  td {
    padding: 7px 10px; border-bottom: 1px solid rgba(15,52,96,0.5);
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; font-size: 12px;
  }
  td.slug-col {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  td.slug-col a { color: var(--blue); }
  td.slug-col a:hover { color: var(--green); }
  tr:hover { background: var(--hover-bg); }
  tr.row-won { background: rgba(0,212,170,0.08); }
  tr.row-lost { background: rgba(255,107,107,0.08); }
  .edge-high { color: var(--green); font-weight: 600; }
  .edge-mid { color: var(--yellow); }
  .edge-low { color: var(--text); }

  /* Charts grid */
  .charts-grid {
    display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 20px;
  }
  .charts-grid-3 {
    display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 20px; margin-bottom: 20px;
  }
  @media (max-width: 1200px) { .charts-grid-3 { grid-template-columns: 1fr 1fr; } }
  @media (max-width: 900px) {
    .charts-grid { grid-template-columns: 1fr; }
    .charts-grid-3 { grid-template-columns: 1fr; }
    .cards { grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); }
  }
  @media (max-width: 600px) {
    .container { padding: 10px; }
    .header { padding: 12px 16px; }
    .cards { grid-template-columns: repeat(2, 1fr); gap: 8px; }
    .card { padding: 10px; }
    .card-value { font-size: 20px; }
    .tab-btn { padding: 6px 12px; font-size: 12px; }
  }
  .chart-container { position: relative; height: 280px; }
  .chart-container-lg { position: relative; height: 350px; }

  /* Activity feed */
  .activity-feed { max-height: 500px; overflow-y: auto; }
  .activity-item {
    display: flex; gap: 12px; padding: 10px 0;
    border-bottom: 1px solid rgba(15,52,96,0.5);
    align-items: flex-start;
  }
  .activity-item:last-child { border-bottom: none; }
  .activity-icon {
    width: 32px; height: 32px; border-radius: 50%; display: flex;
    align-items: center; justify-content: center; font-size: 14px;
    flex-shrink: 0;
  }
  .activity-icon.open { background: rgba(79,195,247,0.2); color: var(--blue); }
  .activity-icon.won { background: rgba(0,212,170,0.2); color: var(--green); }
  .activity-icon.lost { background: rgba(255,107,107,0.2); color: var(--red); }
  .activity-icon.merge { background: rgba(179,136,255,0.2); color: var(--purple); }
  .activity-icon.other { background: rgba(255,217,61,0.2); color: var(--yellow); }
  .activity-body { flex: 1; min-width: 0; }
  .activity-desc { font-size: 13px; word-break: break-word; }
  .activity-time { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
  .activity-pnl { font-size: 13px; font-weight: 600; white-space: nowrap; }

  /* Heatmap table */
  .heatmap td { text-align: center; font-weight: 600; }
  .heatmap td.positive { color: var(--green); background: rgba(0,212,170,0.1); }
  .heatmap td.negative { color: var(--red); background: rgba(255,107,107,0.1); }
  .heatmap td.neutral { color: var(--text-muted); }

  /* Stats grid */
  .stats-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 14px;
  }
  .stat-box {
    background: rgba(15,52,96,0.4); border-radius: 8px; padding: 16px;
    text-align: center;
  }
  .stat-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; letter-spacing: 0.5px; }
  .stat-value { font-size: 24px; font-weight: 700; font-family: 'Cascadia Code', monospace; }
  .stat-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }

  /* Log */
  .log-box {
    background: #0d1117; border: 1px solid #30363d; border-radius: 6px;
    padding: 12px; max-height: 500px; overflow-y: auto;
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 11px; line-height: 1.5;
  }
  .log-line { white-space: pre-wrap; word-break: break-all; }
  .log-order { color: var(--blue); }
  .log-resolved { color: var(--green); }
  .log-error { color: var(--red); font-weight: 600; }

  /* Learning */
  .learning-grid {
    display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 14px;
  }
  .learn-stat {
    background: rgba(15,52,96,0.4); border-radius: 8px; padding: 14px; text-align: center;
  }
  .learn-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; }
  .learn-value { font-size: 22px; font-weight: 700; font-family: 'Cascadia Code', monospace; }

  /* Empty state */
  .empty { text-align: center; color: var(--text-muted); padding: 30px; font-size: 14px; }

  /* Responsive table wrapper */
  .table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--card-border); border-radius: 3px; }

  /* Polymarket link button */
  .pm-link {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    background: rgba(79,195,247,0.15); color: var(--blue); font-size: 11px;
    font-family: -apple-system, sans-serif; transition: background 0.2s;
  }
  .pm-link:hover { background: rgba(79,195,247,0.3); text-decoration: none; }

  /* Streak badge */
  .streak-win { color: var(--green); }
  .streak-loss { color: var(--red); }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div>
    <h1><span>&#9670;</span> Polymarket Arb Bot <span>|</span> Dashboard</h1>
  </div>
  <div class="header-right">
    <!-- Traffic Light System -->
    <div class="traffic-lights">
      <div class="tl-item" id="tl-bot" title="Bot Status">
        <span class="tl-dot tl-red"></span>
        <span class="tl-label">Bot</span>
        <span class="tl-detail" id="tl-bot-detail">Stopped</span>
      </div>
      <div class="tl-item" id="tl-mode" title="Trading Mode">
        <span class="tl-dot tl-yellow"></span>
        <span class="tl-label">Mode</span>
        <span class="tl-detail" id="tl-mode-detail">Paper</span>
      </div>
      <div class="tl-item" id="tl-rn1" title="RN1 Tracker">
        <span class="tl-dot tl-red"></span>
        <span class="tl-label">RN1</span>
        <span class="tl-detail" id="tl-rn1-detail">Offline</span>
      </div>
    </div>
    <span id="utc-time" class="utc-time"></span>
  </div>
</div>

<div class="container">
  <div class="last-updated" id="last-updated">Loading...</div>

  <!-- Summary Cards (always visible) -->
  <div class="cards" id="summary-cards">
    <div class="card">
      <div class="card-label">Total P&amp;L</div>
      <div class="card-value" id="card-pnl">$0.00</div>
      <div class="card-sub" id="card-wl"></div>
    </div>
    <div class="card">
      <div class="card-label">Win Rate</div>
      <div class="card-value" id="card-wr">0%</div>
    </div>
    <div class="card">
      <div class="card-label">Sharpe Ratio</div>
      <div class="card-value" id="card-sharpe">--</div>
      <div class="card-sub">annualized</div>
    </div>
    <div class="card">
      <div class="card-label">Open Positions</div>
      <div class="card-value" id="card-open">0</div>
    </div>
    <div class="card">
      <div class="card-label">Exposure</div>
      <div class="card-value" id="card-exposure">$0</div>
    </div>
    <div class="card">
      <div class="card-label">Total Trades</div>
      <div class="card-value" id="card-trades">0</div>
    </div>
    <div class="card">
      <div class="card-label">Max Drawdown</div>
      <div class="card-value pnl-neg" id="card-dd">$0</div>
      <div class="card-sub" id="card-dd-pct"></div>
    </div>
    <div class="card">
      <div class="card-label">ROI</div>
      <div class="card-value" id="card-roi">0%</div>
    </div>
  </div>

  <!-- Tab Navigation -->
  <div class="tab-nav">
    <button class="tab-btn active" onclick="switchTab('overview')">Overview</button>
    <button class="tab-btn" onclick="switchTab('performance')">Performance</button>
    <button class="tab-btn" onclick="switchTab('positions')">Positions</button>
    <button class="tab-btn" onclick="switchTab('analytics')">Analytics</button>
    <button class="tab-btn" onclick="switchTab('rn1')">RN1 Insights</button>
    <button class="tab-btn" onclick="switchTab('rn1live')">RN1 Live</button>
    <button class="tab-btn" onclick="switchTab('learning')">Learning Agent</button>
    <button class="tab-btn" onclick="switchTab('log')">Bot Log</button>
  </div>

  <!-- TAB: Overview -->
  <div class="tab-content active" id="tab-overview">
    <!-- Extended Stats -->
    <div class="section">
      <h2>Performance Statistics</h2>
      <div class="stats-grid" id="ext-stats-grid">
        <div class="stat-box">
          <div class="stat-label">Profit Factor</div>
          <div class="stat-value" id="stat-pf">--</div>
          <div class="stat-sub">gross wins / gross losses</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Avg Winner</div>
          <div class="stat-value pnl-pos" id="stat-avg-win">$0</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Avg Loser</div>
          <div class="stat-value pnl-neg" id="stat-avg-loss">$0</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Current Streak</div>
          <div class="stat-value" id="stat-streak">--</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Avg Hold Time</div>
          <div class="stat-value" id="stat-hold">--</div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Best Trade</div>
          <div class="stat-value pnl-pos" id="stat-best">$0</div>
          <div class="stat-sub" id="stat-best-slug"></div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Worst Trade</div>
          <div class="stat-value pnl-neg" id="stat-worst">$0</div>
          <div class="stat-sub" id="stat-worst-slug"></div>
        </div>
        <div class="stat-box">
          <div class="stat-label">Capital Deployed</div>
          <div class="stat-value" id="stat-deployed">$0</div>
        </div>
      </div>
    </div>

    <!-- Activity Feed -->
    <div class="section">
      <h2>Recent Activity</h2>
      <div class="activity-feed" id="activity-feed">
        <div class="empty">No activity yet</div>
      </div>
    </div>

    <!-- Sport Heatmap -->
    <div class="section">
      <h2>Sport Heatmap</h2>
      <div class="table-wrap">
        <table class="heatmap" id="heatmap-table">
          <thead>
            <tr>
              <th>Sport</th><th>Total</th><th>Open</th><th>Win Rate</th>
              <th>Avg Edge</th><th>P&amp;L</th><th>ROI</th>
            </tr>
          </thead>
          <tbody id="heatmap-tbody"></tbody>
        </table>
        <div class="empty" id="heatmap-empty" style="display:none;">No data yet</div>
      </div>
    </div>
  </div>

  <!-- TAB: Performance -->
  <div class="tab-content" id="tab-performance">
    <!-- Equity Curve -->
    <div class="section">
      <h2>Equity Curve</h2>
      <div class="chart-container-lg"><canvas id="equityChart"></canvas></div>
      <div class="empty" id="equity-empty" style="display:none;">No resolved trades yet -- charts will appear after positions resolve</div>
    </div>

    <div class="charts-grid">
      <!-- Cumulative PnL -->
      <div class="section">
        <h2>Cumulative P&amp;L</h2>
        <div class="chart-container"><canvas id="cumPnlChart"></canvas></div>
      </div>
      <!-- Daily PnL -->
      <div class="section">
        <h2>Daily P&amp;L</h2>
        <div class="chart-container"><canvas id="dailyPnlChart"></canvas></div>
      </div>
    </div>

    <div class="charts-grid">
      <!-- Rolling Win Rate -->
      <div class="section">
        <h2>Rolling Win Rate (20-trade)</h2>
        <div class="chart-container"><canvas id="rollingWrChart"></canvas></div>
      </div>
      <!-- Edge Distribution -->
      <div class="section">
        <h2>Edge % Distribution at Entry</h2>
        <div class="chart-container"><canvas id="edgeDistChart"></canvas></div>
      </div>
    </div>

    <!-- Calibration -->
    <div class="section">
      <h2>Calibration: Predicted vs Actual Win Rate</h2>
      <div class="chart-container"><canvas id="calibrationChart"></canvas></div>
      <div class="empty" id="calibration-empty" style="display:none;">Not enough resolved trades for calibration</div>
    </div>
  </div>

  <!-- TAB: Positions -->
  <div class="tab-content" id="tab-positions">
    <!-- Open Positions -->
    <div class="section">
      <h2>Open Positions (<span id="open-count-header">0</span>)</h2>
      <div class="table-wrap">
        <table id="open-table">
          <thead>
            <tr>
              <th onclick="sortTable('open-table',0)">Market <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',1)">Outcome <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',2)">Sport <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',3)">Type <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',4)">Entry <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',5)">Fair Prob <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',6)">Edge% <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',7)">Shares <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',8)">Cost <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',9)">Exp. Profit <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('open-table',10)">Time Held <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th>Link</th>
            </tr>
          </thead>
          <tbody id="open-tbody"></tbody>
        </table>
        <div class="empty" id="open-empty" style="display:none;">No open positions</div>
      </div>
    </div>

    <!-- Resolved Positions -->
    <div class="section">
      <h2>Resolved Positions (Last 100)</h2>
      <div class="table-wrap">
        <table id="resolved-table">
          <thead>
            <tr>
              <th onclick="sortTable('resolved-table',0)">Market <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',1)">Outcome <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',2)">Sport <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',3)">Type <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',4)">Entry <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',5)">Resolution <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',6)">Shares <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',7)">Cost <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',8)">Payout <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',9)">PnL <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',10)">Status <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th onclick="sortTable('resolved-table',11)">Closed <span class="sort-arrow">&#9650;&#9660;</span></th>
              <th>Link</th>
            </tr>
          </thead>
          <tbody id="resolved-tbody"></tbody>
        </table>
        <div class="empty" id="resolved-empty" style="display:none;">No resolved positions yet</div>
      </div>
    </div>
  </div>

  <!-- TAB: Analytics -->
  <div class="tab-content" id="tab-analytics">
    <!-- P&L by Sport -->
    <div class="charts-grid">
      <div class="section">
        <h2>P&amp;L by Sport</h2>
        <div class="chart-container"><canvas id="sportChart"></canvas></div>
        <div class="table-wrap" style="margin-top:14px;">
          <table>
            <thead><tr><th>Sport</th><th>Trades</th><th>W</th><th>L</th><th>Win Rate</th><th>Avg Edge</th><th>PnL</th><th>ROI</th></tr></thead>
            <tbody id="sport-tbody"></tbody>
          </table>
        </div>
      </div>
      <div class="section">
        <h2>P&amp;L by Market Type</h2>
        <div class="chart-container"><canvas id="mtChart"></canvas></div>
        <div class="table-wrap" style="margin-top:14px;">
          <table>
            <thead><tr><th>Type</th><th>Trades</th><th>Win Rate</th><th>PnL</th></tr></thead>
            <tbody id="mt-tbody"></tbody>
          </table>
        </div>
      </div>
    </div>
  </div>

  <!-- TAB: RN1 Insights -->
  <div class="tab-content" id="tab-rn1">
    <div class="charts-grid">
      <div class="section">
        <h2>RN1 Sport Allocation (by USDC Volume)</h2>
        <div class="chart-container"><canvas id="rn1SportChart"></canvas></div>
        <div class="table-wrap" style="margin-top:14px;">
          <table>
            <thead><tr><th>Sport</th><th>Buy USDC</th><th>Buys</th><th>Merges</th><th>Redeems</th><th>Est. Profit</th></tr></thead>
            <tbody id="rn1-sport-tbody"></tbody>
          </table>
        </div>
      </div>
      <div class="section">
        <h2>RN1 Entry Price Distribution</h2>
        <div class="chart-container"><canvas id="rn1PriceChart"></canvas></div>
      </div>
    </div>
    <div class="charts-grid">
      <div class="section">
        <h2>RN1 Market Type Preferences</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>Type</th><th>Trades</th><th>USDC</th><th>% of Trades</th><th>Avg Price</th></tr></thead>
            <tbody id="rn1-mtype-tbody"></tbody>
          </table>
        </div>
      </div>
      <div class="section">
        <h2>RN1 Merge Stats</h2>
        <div id="rn1-merge-stats" class="learning-grid">
          <div class="empty">Loading...</div>
        </div>
      </div>
    </div>
    <div class="charts-grid">
      <div class="section">
        <h2>RN1 Activity by Hour (UTC)</h2>
        <div class="chart-container"><canvas id="rn1HourChart"></canvas></div>
      </div>
      <div class="section">
        <h2>RN1 Holding Periods</h2>
        <div class="chart-container"><canvas id="rn1HoldChart"></canvas></div>
      </div>
    </div>
    <div class="section">
      <h2>RN1 Summary</h2>
      <div id="rn1-summary" class="learning-grid">
        <div class="empty">Loading RN1 data...</div>
      </div>
    </div>
  </div>

  <!-- TAB: RN1 Live Activity -->
  <div class="tab-content" id="tab-rn1live">
    <div class="cards" id="rn1live-cards">
      <div class="card">
        <div class="card-label">Tracker Status</div>
        <div class="card-value" id="rn1l-status">--</div>
      </div>
      <div class="card">
        <div class="card-label">Active Markets (15m)</div>
        <div class="card-value" id="rn1l-active">0</div>
      </div>
      <div class="card">
        <div class="card-label">Hot Markets</div>
        <div class="card-value" id="rn1l-hot">0</div>
      </div>
      <div class="card">
        <div class="card-label">New Markets (5m)</div>
        <div class="card-value" id="rn1l-new">0</div>
      </div>
      <div class="card">
        <div class="card-label">Trades (5m / 15m)</div>
        <div class="card-value" id="rn1l-trades">0 / 0</div>
      </div>
      <div class="card">
        <div class="card-label">Our Positions in RN1 Markets</div>
        <div class="card-value" id="rn1l-overlap">0</div>
      </div>
    </div>
    <div class="two-col">
      <div class="section">
        <h2>Hot Markets (High RN1 Activity)</h2>
        <div id="rn1l-hot-list" class="empty">No hot markets</div>
      </div>
      <div class="section">
        <h2>New Markets (RN1 Just Entered)</h2>
        <div id="rn1l-new-list" class="empty">No new markets</div>
      </div>
    </div>
    <div class="section">
      <h2>Recent RN1 Activity (Last 15 Events)</h2>
      <div class="table-wrap">
        <table>
          <thead><tr>
            <th>Time (UTC)</th>
            <th>Type</th>
            <th>Market</th>
            <th>Volume ($)</th>
          </tr></thead>
          <tbody id="rn1l-activity-tbody">
            <tr><td colspan="4" class="empty">No activity data</td></tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="section">
      <h2>All Active Markets (15m)</h2>
      <div id="rn1l-active-list" class="empty">No active markets</div>
    </div>
  </div>

  <!-- TAB: Learning Agent -->
  <div class="tab-content" id="tab-learning">
    <div class="section" id="learning-section">
      <h2>Learning Agent Stats</h2>
      <div class="learning-grid" id="learning-grid">
        <div class="empty">Learning agent not active or no data yet</div>
      </div>
    </div>
    <div class="section" id="learning-sport-detail" style="display:none;">
      <h2>Sport Performance Detail (Learning Agent)</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr><th>Sport</th><th>Trades</th><th>Wins</th><th>Win Rate</th><th>Avg Edge</th><th>PnL</th><th>Confident?</th></tr>
          </thead>
          <tbody id="learning-sport-tbody"></tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- TAB: Log -->
  <div class="tab-content" id="tab-log">
    <div class="section">
      <h2>Recent Bot Log (last 80 lines)</h2>
      <div class="log-box" id="log-box"></div>
    </div>
  </div>
</div>

<script>
// --- State ---
let lastUpdated = Date.now();
let sportChart = null, mtChart = null;
let equityChart = null, cumPnlChart = null, dailyPnlChart = null;
let rollingWrChart = null, edgeDistChart = null, calibrationChart = null;
const chartDefaults = {
  responsive: true, maintainAspectRatio: false,
  plugins: { legend: { display: false } },
};
const axisDefaults = {
  x: { ticks: { color: '#8892a4', font: { size: 11 } }, grid: { display: false } },
  y: { ticks: { color: '#8892a4' }, grid: { color: 'rgba(15,52,96,0.5)' } }
};

// --- Tab switching ---
function switchTab(name) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  // Find matching button
  document.querySelectorAll('.tab-btn').forEach(el => {
    if (el.textContent.toLowerCase().replace(/\s/g,'').includes(name.replace(/\s/g,''))) {
      el.classList.add('active');
    }
  });
}

// --- Helpers ---
function fmt$(v) {
  const n = parseFloat(v) || 0;
  return (n >= 0 ? '+$' : '-$') + Math.abs(n).toFixed(2);
}
function pnlClass(v) { return (parseFloat(v) || 0) >= 0 ? 'pnl-pos' : 'pnl-neg'; }
function edgeClass(v) {
  const n = parseFloat(v) || 0;
  if (n >= 10) return 'edge-high';
  if (n >= 5) return 'edge-mid';
  return 'edge-low';
}
function shortSlug(s) { return !s ? '' : (s.length > 35 ? s.substring(0, 32) + '...' : s); }
function shortTime(t) { return !t ? '' : t.replace('T', ' ').substring(0, 19); }
function relativeTime(t) {
  if (!t) return '';
  try {
    const d = new Date(t);
    const now = new Date();
    const diff = (now - d) / 1000;
    if (diff < 60) return Math.floor(diff) + 's ago';
    if (diff < 3600) return Math.floor(diff/60) + 'm ago';
    if (diff < 86400) return Math.floor(diff/3600) + 'h ago';
    return Math.floor(diff/86400) + 'd ago';
  } catch(e) { return ''; }
}

async function fetchJSON(url) {
  try { const r = await fetch(url); return await r.json(); }
  catch(e) { console.error('Fetch error:', url, e); return null; }
}

// --- Table sorting ---
let sortState = {};
function sortTable(tableId, colIdx) {
  const table = document.getElementById(tableId);
  const tbody = table.querySelector('tbody');
  const rows = Array.from(tbody.querySelectorAll('tr'));
  if (rows.length === 0) return;

  const key = tableId + '-' + colIdx;
  const asc = sortState[key] === 'asc' ? 'desc' : 'asc';
  sortState[key] = asc;

  rows.sort((a, b) => {
    let va = a.cells[colIdx]?.textContent?.trim() || '';
    let vb = b.cells[colIdx]?.textContent?.trim() || '';
    // Try numeric
    const na = parseFloat(va.replace(/[$%+,]/g, ''));
    const nb = parseFloat(vb.replace(/[$%+,]/g, ''));
    if (!isNaN(na) && !isNaN(nb)) {
      return asc === 'asc' ? na - nb : nb - na;
    }
    return asc === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
  });

  rows.forEach(r => tbody.appendChild(r));
}

// --- Destroy chart helper ---
function destroyChart(chart) { if (chart) chart.destroy(); return null; }

// --- Main refresh ---
async function refreshAll() {
  const [summary, positions, resolved, log, sports, mtypes, learning,
         pnlSeries, dailyPnl, edgeDist, calibration, stats, activity, heatmap, rn1, rn1live] =
    await Promise.all([
      fetchJSON('/api/summary'),
      fetchJSON('/api/positions'),
      fetchJSON('/api/resolved'),
      fetchJSON('/api/log'),
      fetchJSON('/api/sports'),
      fetchJSON('/api/market_types'),
      fetchJSON('/api/learning'),
      fetchJSON('/api/pnl_series'),
      fetchJSON('/api/daily_pnl'),
      fetchJSON('/api/edge_distribution'),
      fetchJSON('/api/calibration'),
      fetchJSON('/api/stats'),
      fetchJSON('/api/activity'),
      fetchJSON('/api/sport_heatmap'),
      fetchJSON('/api/rn1'),
      fetchJSON('/api/rn1_live'),
    ]);

  lastUpdated = Date.now();

  // === Summary Cards ===
  if (summary) {
    const pnlEl = document.getElementById('card-pnl');
    pnlEl.textContent = fmt$(summary.total_pnl);
    pnlEl.className = 'card-value ' + pnlClass(summary.total_pnl);
    document.getElementById('card-wl').textContent = summary.wins + 'W / ' + summary.losses + 'L';
    document.getElementById('card-wr').textContent = summary.win_rate + '%';
    document.getElementById('card-open').textContent = summary.open_count;
    document.getElementById('card-exposure').textContent = '$' + summary.total_exposure.toFixed(2);
    document.getElementById('card-trades').textContent = summary.total_trades;
    document.getElementById('utc-time').textContent = summary.utc_now;

    // === Traffic Lights ===
    // Bot status
    const tlBotDot = document.querySelector('#tl-bot .tl-dot');
    const tlBotDetail = document.getElementById('tl-bot-detail');
    if (summary.bot_status === 'Running') {
      tlBotDot.className = 'tl-dot tl-green';
      tlBotDetail.textContent = 'Running';
    } else {
      tlBotDot.className = 'tl-dot tl-red';
      tlBotDetail.textContent = 'Stopped';
    }

    // Trading mode
    const tlModeDot = document.querySelector('#tl-mode .tl-dot');
    const tlModeDetail = document.getElementById('tl-mode-detail');
    if (summary.live_trading) {
      tlModeDot.className = 'tl-dot tl-green';
      tlModeDetail.textContent = 'LIVE';
      tlModeDetail.style.color = 'var(--green)';
      tlModeDetail.style.fontWeight = '700';
    } else {
      tlModeDot.className = 'tl-dot tl-yellow';
      tlModeDetail.textContent = 'Paper';
      tlModeDetail.style.color = 'var(--yellow)';
      tlModeDetail.style.fontWeight = '600';
    }

    // RN1 tracker
    const tlRn1Dot = document.querySelector('#tl-rn1 .tl-dot');
    const tlRn1Detail = document.getElementById('tl-rn1-detail');
    if (summary.rn1_tracker && summary.rn1_tracker.alive) {
      tlRn1Dot.className = 'tl-dot tl-green';
      const ago = summary.rn1_tracker.last_poll_ago;
      const mkts = summary.rn1_tracker.active_markets;
      const t5 = summary.rn1_tracker.trades_last_5m;
      tlRn1Detail.textContent = ago + 's ago | ' + mkts + ' mkts | ' + t5 + ' trades/5m';
    } else {
      tlRn1Dot.className = 'tl-dot tl-red';
      tlRn1Detail.textContent = 'Offline';
    }
  }

  // === Extended Stats ===
  if (stats) {
    const sharpeEl = document.getElementById('card-sharpe');
    sharpeEl.textContent = stats.total_resolved > 0 ? stats.sharpe.toFixed(2) : '--';
    sharpeEl.className = 'card-value ' + (stats.sharpe >= 0 ? 'pnl-pos' : 'pnl-neg');

    document.getElementById('card-dd').textContent = '-$' + stats.max_dd_dollar.toFixed(2);
    document.getElementById('card-dd-pct').textContent = stats.max_dd_pct.toFixed(1) + '%';

    const roiEl = document.getElementById('card-roi');
    roiEl.textContent = stats.roi.toFixed(1) + '%';
    roiEl.className = 'card-value ' + (stats.roi >= 0 ? 'pnl-pos' : 'pnl-neg');

    document.getElementById('stat-pf').textContent = stats.profit_factor === 'Inf' ? 'Inf' : stats.profit_factor.toFixed(2);
    document.getElementById('stat-avg-win').textContent = '+$' + stats.avg_winner.toFixed(2);
    document.getElementById('stat-avg-loss').textContent = '-$' + Math.abs(stats.avg_loser).toFixed(2);

    const streakEl = document.getElementById('stat-streak');
    if (stats.current_streak.count > 0) {
      const isWin = stats.current_streak.type === 'won';
      streakEl.textContent = stats.current_streak.count + (isWin ? 'W' : 'L');
      streakEl.className = 'stat-value ' + (isWin ? 'streak-win' : 'streak-loss');
    } else {
      streakEl.textContent = '--';
    }

    const holdEl = document.getElementById('stat-hold');
    if (stats.avg_hold_time_hours > 0) {
      if (stats.avg_hold_time_hours < 1) holdEl.textContent = Math.round(stats.avg_hold_time_hours * 60) + 'm';
      else if (stats.avg_hold_time_hours < 24) holdEl.textContent = stats.avg_hold_time_hours.toFixed(1) + 'h';
      else holdEl.textContent = (stats.avg_hold_time_hours / 24).toFixed(1) + 'd';
    } else { holdEl.textContent = '--'; }

    if (stats.best_trade) {
      document.getElementById('stat-best').textContent = fmt$(stats.best_trade.pnl);
      const bestSub = document.getElementById('stat-best-slug');
      bestSub.innerHTML = stats.best_trade.slug ?
        `<a href="${stats.best_trade.url}" target="_blank" class="pm-link">${shortSlug(stats.best_trade.slug)}</a>` : '';
    }
    if (stats.worst_trade) {
      document.getElementById('stat-worst').textContent = fmt$(stats.worst_trade.pnl);
      const worstSub = document.getElementById('stat-worst-slug');
      worstSub.innerHTML = stats.worst_trade.slug ?
        `<a href="${stats.worst_trade.url}" target="_blank" class="pm-link">${shortSlug(stats.worst_trade.slug)}</a>` : '';
    }
    document.getElementById('stat-deployed').textContent = '$' + stats.total_capital_deployed.toFixed(2);
  }

  // === Open Positions ===
  if (positions) {
    const tbody = document.getElementById('open-tbody');
    const empty = document.getElementById('open-empty');
    document.getElementById('open-count-header').textContent = positions.length;
    if (positions.length === 0) {
      tbody.innerHTML = ''; empty.style.display = 'block';
    } else {
      empty.style.display = 'none';
      tbody.innerHTML = positions.map(p => `<tr>
        <td class="slug-col" title="${p.slug || ''}"><a href="${p.polymarket_url}" target="_blank">${shortSlug(p.slug)}</a></td>
        <td>${p.outcome || ''}</td>
        <td>${p.sport || ''}</td>
        <td>${p.market_type || ''}</td>
        <td>${parseFloat(p.entry_price||0).toFixed(3)}</td>
        <td>${parseFloat(p.fair_prob||0).toFixed(3)}</td>
        <td class="${edgeClass(p.edge_pct)}">${parseFloat(p.edge_pct||0).toFixed(1)}%</td>
        <td>${parseFloat(p.shares||0).toFixed(1)}</td>
        <td>$${parseFloat(p.cost_usdc||0).toFixed(2)}</td>
        <td class="pnl-pos">+$${parseFloat(p.expected_profit||0).toFixed(2)}</td>
        <td>${p.time_held || ''}</td>
        <td><a href="${p.polymarket_url}" target="_blank" class="pm-link">View</a></td>
      </tr>`).join('');
    }
  }

  // === Resolved Positions ===
  if (resolved) {
    const tbody = document.getElementById('resolved-tbody');
    const empty = document.getElementById('resolved-empty');
    if (resolved.length === 0) {
      tbody.innerHTML = ''; empty.style.display = 'block';
    } else {
      empty.style.display = 'none';
      tbody.innerHTML = resolved.map(p => {
        const cls = p.status && p.status.toLowerCase() === 'won' ? 'row-won' : 'row-lost';
        const pnl = parseFloat(p.pnl||0);
        return `<tr class="${cls}">
          <td class="slug-col" title="${p.slug || ''}"><a href="${p.polymarket_url}" target="_blank">${shortSlug(p.slug)}</a></td>
          <td>${p.outcome || ''}</td>
          <td>${p.sport || ''}</td>
          <td>${p.market_type || ''}</td>
          <td>${parseFloat(p.entry_price||0).toFixed(3)}</td>
          <td>${parseFloat(p.resolution_price||0).toFixed(3)}</td>
          <td>${parseFloat(p.shares||0).toFixed(1)}</td>
          <td>$${parseFloat(p.cost_usdc||0).toFixed(2)}</td>
          <td>$${parseFloat(p.payout||0).toFixed(2)}</td>
          <td class="${pnlClass(pnl)}">${fmt$(pnl)}</td>
          <td>${(p.status||'').toUpperCase()}</td>
          <td>${shortTime(p.closed_at)}</td>
          <td><a href="${p.polymarket_url}" target="_blank" class="pm-link">View</a></td>
        </tr>`;
      }).join('');
    }
  }

  // === Activity Feed ===
  if (activity && activity.length > 0) {
    const feed = document.getElementById('activity-feed');
    const icons = { open: '&#9654;', won: '&#10003;', lost: '&#10007;', merge: '&#8644;', other: '&#9679;' };
    feed.innerHTML = activity.map(a => `
      <div class="activity-item">
        <div class="activity-icon ${a.type}">${icons[a.type] || icons.other}</div>
        <div class="activity-body">
          <div class="activity-desc">${a.description}
            ${a.slug ? `<a href="${a.url}" target="_blank" class="pm-link" style="margin-left:6px;">View</a>` : ''}
          </div>
          <div class="activity-time">${shortTime(a.timestamp)} (${relativeTime(a.timestamp)})</div>
        </div>
        ${a.pnl !== 0 ? `<div class="activity-pnl ${pnlClass(a.pnl)}">${fmt$(a.pnl)}</div>` : ''}
      </div>
    `).join('');
  }

  // === Sport Heatmap ===
  if (heatmap && heatmap.length > 0) {
    document.getElementById('heatmap-empty').style.display = 'none';
    const tbody = document.getElementById('heatmap-tbody');
    tbody.innerHTML = heatmap.map(h => {
      const wrClass = h.win_rate !== null ? (h.win_rate >= 55 ? 'positive' : h.win_rate <= 45 ? 'negative' : 'neutral') : 'neutral';
      const pnlCls = h.pnl >= 0 ? 'positive' : 'negative';
      const roiCls = h.roi !== null ? (h.roi >= 0 ? 'positive' : 'negative') : 'neutral';
      return `<tr>
        <td style="text-align:left;font-weight:700;">${h.sport}</td>
        <td class="neutral">${h.count}</td>
        <td class="neutral">${h.open}</td>
        <td class="${wrClass}">${h.win_rate !== null ? h.win_rate + '%' : '--'}</td>
        <td class="neutral">${h.avg_edge}%</td>
        <td class="${pnlCls}">${fmt$(h.pnl)}</td>
        <td class="${roiCls}">${h.roi !== null ? h.roi + '%' : '--'}</td>
      </tr>`;
    }).join('');
  } else {
    document.getElementById('heatmap-empty').style.display = 'block';
    document.getElementById('heatmap-tbody').innerHTML = '';
  }

  // === Equity Curve ===
  if (pnlSeries && pnlSeries.length > 0) {
    document.getElementById('equity-empty').style.display = 'none';
    const startingCapital = (summary && summary.starting_capital) || 500;
    equityChart = destroyChart(equityChart);
    const ctx = document.getElementById('equityChart').getContext('2d');
    const labels = ['Start', ...pnlSeries.map(d => d.trade_num ? '#' + d.trade_num : d.date)];
    const eqData = [startingCapital, ...pnlSeries.map(d => d.equity)];
    equityChart = new Chart(ctx, {
      type: 'line',
      data: {
        labels: labels,
        datasets: [{
          label: 'Equity ($)',
          data: eqData,
          borderColor: '#4fc3f7', backgroundColor: 'rgba(79,195,247,0.1)',
          fill: true, tension: 0.3, pointRadius: 2, borderWidth: 2,
        }]
      },
      options: {
        ...chartDefaults,
        plugins: { legend: { display: true, labels: { color: '#8892a4' } } },
        scales: {
          x: { ...axisDefaults.x, ticks: { ...axisDefaults.x.ticks, maxTicksLimit: 20 } },
          y: { ...axisDefaults.y, ticks: { ...axisDefaults.y.ticks, callback: v => '$' + v } }
        }
      }
    });

    // Cumulative PnL
    cumPnlChart = destroyChart(cumPnlChart);
    const ctx2 = document.getElementById('cumPnlChart').getContext('2d');
    cumPnlChart = new Chart(ctx2, {
      type: 'line',
      data: {
        labels: pnlSeries.map(d => '#' + d.trade_num),
        datasets: [{
          label: 'Cumulative P&L ($)',
          data: pnlSeries.map(d => d.cumulative_pnl),
          borderColor: '#00d4aa', backgroundColor: 'rgba(0,212,170,0.1)',
          fill: true, tension: 0.3, pointRadius: 2, borderWidth: 2,
        }]
      },
      options: { ...chartDefaults, scales: {
        x: { ...axisDefaults.x, ticks: { ...axisDefaults.x.ticks, maxTicksLimit: 15 } },
        y: { ...axisDefaults.y, ticks: { ...axisDefaults.y.ticks, callback: v => '$' + v } }
      }}
    });

    // Rolling win rate
    if (pnlSeries.length >= 5) {
      rollingWrChart = destroyChart(rollingWrChart);
      const ctx4 = document.getElementById('rollingWrChart').getContext('2d');
      const window = 20;
      const wr = [];
      const wrLabels = [];
      for (let i = 0; i < pnlSeries.length; i++) {
        const start = Math.max(0, i - window + 1);
        const slice = pnlSeries.slice(start, i + 1);
        const wins = slice.filter(d => d.pnl > 0).length;
        wr.push((wins / slice.length * 100).toFixed(1));
        wrLabels.push('#' + (i+1));
      }
      rollingWrChart = new Chart(ctx4, {
        type: 'line',
        data: {
          labels: wrLabels,
          datasets: [{
            label: 'Rolling Win Rate %',
            data: wr,
            borderColor: '#ffd93d', backgroundColor: 'rgba(255,217,61,0.1)',
            fill: true, tension: 0.3, pointRadius: 1, borderWidth: 2,
          }]
        },
        options: { ...chartDefaults, scales: {
          x: { ...axisDefaults.x, ticks: { ...axisDefaults.x.ticks, maxTicksLimit: 15 } },
          y: { ...axisDefaults.y, min: 0, max: 100, ticks: { ...axisDefaults.y.ticks, callback: v => v + '%' } }
        }}
      });
    }
  } else {
    document.getElementById('equity-empty').style.display = 'block';
  }

  // === Daily PnL ===
  if (dailyPnl && dailyPnl.length > 0) {
    dailyPnlChart = destroyChart(dailyPnlChart);
    const ctx3 = document.getElementById('dailyPnlChart').getContext('2d');
    dailyPnlChart = new Chart(ctx3, {
      type: 'bar',
      data: {
        labels: dailyPnl.map(d => d.date),
        datasets: [{
          label: 'Daily P&L ($)',
          data: dailyPnl.map(d => d.pnl),
          backgroundColor: dailyPnl.map(d => d.pnl >= 0 ? '#00d4aa' : '#ff6b6b'),
          borderRadius: 3,
        }]
      },
      options: { ...chartDefaults, scales: {
        x: { ...axisDefaults.x },
        y: { ...axisDefaults.y, ticks: { ...axisDefaults.y.ticks, callback: v => '$' + v } }
      }}
    });
  }

  // === Edge Distribution ===
  if (edgeDist && edgeDist.length > 0) {
    edgeDistChart = destroyChart(edgeDistChart);
    const ctx5 = document.getElementById('edgeDistChart').getContext('2d');
    edgeDistChart = new Chart(ctx5, {
      type: 'bar',
      data: {
        labels: edgeDist.map(d => d.bucket),
        datasets: [{
          label: 'Count',
          data: edgeDist.map(d => d.count),
          backgroundColor: '#b388ff',
          borderRadius: 4,
        }]
      },
      options: { ...chartDefaults, scales: {
        x: { ...axisDefaults.x },
        y: { ...axisDefaults.y, beginAtZero: true }
      }}
    });
  }

  // === Calibration Chart ===
  if (calibration && calibration.length > 0) {
    document.getElementById('calibration-empty').style.display = 'none';
    calibrationChart = destroyChart(calibrationChart);
    const ctx6 = document.getElementById('calibrationChart').getContext('2d');
    calibrationChart = new Chart(ctx6, {
      type: 'bar',
      data: {
        labels: calibration.map(d => d.bucket + ' (n=' + d.count + ')'),
        datasets: [
          {
            label: 'Predicted Win Rate %',
            data: calibration.map(d => d.predicted_wr),
            backgroundColor: 'rgba(79,195,247,0.7)',
            borderRadius: 4,
          },
          {
            label: 'Actual Win Rate %',
            data: calibration.map(d => d.actual_wr),
            backgroundColor: 'rgba(0,212,170,0.7)',
            borderRadius: 4,
          }
        ]
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: { legend: { display: true, labels: { color: '#8892a4' } } },
        scales: {
          x: { ...axisDefaults.x },
          y: { ...axisDefaults.y, min: 0, max: 100, ticks: { ...axisDefaults.y.ticks, callback: v => v + '%' } }
        }
      }
    });
  } else {
    document.getElementById('calibration-empty').style.display = 'block';
  }

  // === Sports Breakdown (Analytics tab) ===
  if (sports) {
    const tbody = document.getElementById('sport-tbody');
    tbody.innerHTML = sports.map(s => `<tr>
      <td class="slug-col">${s.sport}</td>
      <td>${s.count}</td>
      <td class="pnl-pos">${s.wins}</td>
      <td class="pnl-neg">${s.losses}</td>
      <td>${s.win_rate}%</td>
      <td>${s.avg_edge}%</td>
      <td class="${pnlClass(s.pnl)}">${fmt$(s.pnl)}</td>
      <td class="${pnlClass(s.roi)}">${s.roi}%</td>
    </tr>`).join('');

    const labels = sports.map(s => s.sport);
    const data = sports.map(s => s.pnl);
    const colors = data.map(v => v >= 0 ? '#00d4aa' : '#ff6b6b');
    sportChart = destroyChart(sportChart);
    const ctx = document.getElementById('sportChart').getContext('2d');
    sportChart = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets: [{ label: 'P&L ($)', data, backgroundColor: colors, borderRadius: 4 }] },
      options: { ...chartDefaults, scales: {
        x: { ...axisDefaults.x },
        y: { ...axisDefaults.y, ticks: { ...axisDefaults.y.ticks, callback: v => '$'+v } }
      }}
    });
  }

  // === Market Types (Analytics tab) ===
  if (mtypes) {
    const tbody = document.getElementById('mt-tbody');
    tbody.innerHTML = mtypes.map(m => `<tr>
      <td>${m.market_type}</td>
      <td>${m.count}</td>
      <td>${m.win_rate}%</td>
      <td class="${pnlClass(m.pnl)}">${fmt$(m.pnl)}</td>
    </tr>`).join('');

    const labels = mtypes.map(m => m.market_type);
    const data = mtypes.map(m => m.pnl);
    const colors = data.map(v => v >= 0 ? '#00d4aa' : '#ff6b6b');
    mtChart = destroyChart(mtChart);
    const ctx = document.getElementById('mtChart').getContext('2d');
    mtChart = new Chart(ctx, {
      type: 'bar',
      data: { labels, datasets: [{ label: 'P&L ($)', data, backgroundColor: colors, borderRadius: 4 }] },
      options: { ...chartDefaults, scales: {
        x: { ...axisDefaults.x },
        y: { ...axisDefaults.y, ticks: { ...axisDefaults.y.ticks, callback: v => '$'+v } }
      }}
    });
  }

  // === Learning Agent ===
  if (learning && learning.available) {
    const grid = document.getElementById('learning-grid');
    let html = `
      <div class="learn-stat">
        <div class="learn-label">Trades Tracked</div>
        <div class="learn-value">${learning.trade_count || 0}</div>
      </div>
      <div class="learn-stat">
        <div class="learn-label">Overall Win Rate</div>
        <div class="learn-value">${learning.win_rate || 0}%</div>
      </div>`;
    if (learning.best_sport) {
      html += `<div class="learn-stat">
        <div class="learn-label">Best Sport</div>
        <div class="learn-value" style="font-size:16px;">${learning.best_sport}</div>
        <div class="card-sub">${learning.best_sport_wr}% win rate</div>
      </div>`;
    }
    if (learning.best_market_type) {
      html += `<div class="learn-stat">
        <div class="learn-label">Best Market Type</div>
        <div class="learn-value" style="font-size:16px;">${learning.best_market_type}</div>
        <div class="card-sub">${learning.best_mt_wr}% win rate</div>
      </div>`;
    }
    grid.innerHTML = html;

    // Sport detail table
    if (learning.sport_detail && learning.sport_detail.length > 0) {
      document.getElementById('learning-sport-detail').style.display = 'block';
      const tbody = document.getElementById('learning-sport-tbody');
      tbody.innerHTML = learning.sport_detail.map(s => `<tr>
        <td>${s.sport}</td>
        <td>${s.total}</td>
        <td>${s.wins}</td>
        <td>${s.win_rate}%</td>
        <td>${s.avg_edge}%</td>
        <td class="${pnlClass(s.total_pnl)}">${fmt$(s.total_pnl)}</td>
        <td>${s.confident ? '<span style="color:var(--green);">Yes ('+s.total+')</span>' : '<span style="color:var(--text-muted);">No ('+s.total+')</span>'}</td>
      </tr>`).join('');
    }
  }

  // === Log ===
  if (log && log.lines) {
    const box = document.getElementById('log-box');
    box.innerHTML = log.lines.map(line => {
      let cls = 'log-line';
      if (/ORDER/i.test(line)) cls += ' log-order';
      else if (/RESOLVED/i.test(line)) cls += ' log-resolved';
      else if (/ERROR/i.test(line)) cls += ' log-error';
      const escaped = line.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
      return `<div class="${cls}">${escaped}</div>`;
    }).join('');
    box.scrollTop = box.scrollHeight;
  }

  // === RN1 Insights ===
  if (rn1 && !rn1.error) {
    // Sport allocation chart + table
    if (rn1.top_sports && rn1.top_sports.length > 0) {
      const sportLabels = rn1.top_sports.slice(0,10).map(s => s.sport);
      const sportData = rn1.top_sports.slice(0,10).map(s => s.buy_usdc || 0);
      const sportColors = ['#00d2ff','#4caf50','#ff9800','#e91e63','#9c27b0',
                           '#00bcd4','#ffeb3b','#ff5722','#3f51b5','#8bc34a'];

      const ctx1 = document.getElementById('rn1SportChart');
      if (ctx1) {
        if (window._rn1SportChart) window._rn1SportChart.destroy();
        window._rn1SportChart = new Chart(ctx1, {
          type: 'doughnut',
          data: {
            labels: sportLabels,
            datasets: [{data: sportData, backgroundColor: sportColors}]
          },
          options: {responsive:true, maintainAspectRatio:false,
                    plugins:{legend:{position:'right',labels:{color:'#c8d6e5',font:{size:11}}}}}
        });
      }

      const stb = document.getElementById('rn1-sport-tbody');
      if (stb) {
        stb.innerHTML = rn1.top_sports.slice(0,15).map(s => `<tr>
          <td>${s.sport}</td>
          <td>$${(s.buy_usdc||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
          <td>${s.buy_count||0}</td>
          <td>${s.merge_count||0}</td>
          <td>${s.redeem_count||0}</td>
          <td class="${(s.estimated_profit||0)>=0?'pnl-pos':'pnl-neg'}">$${(s.estimated_profit||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
        </tr>`).join('');
      }
    }

    // Entry price distribution chart
    if (rn1.entry_price_distribution) {
      const buckets = Object.entries(rn1.entry_price_distribution);
      const priceLabels = buckets.map(b => b[0]);
      const priceCounts = buckets.map(b => b[1].count);
      const ctx2 = document.getElementById('rn1PriceChart');
      if (ctx2) {
        if (window._rn1PriceChart) window._rn1PriceChart.destroy();
        window._rn1PriceChart = new Chart(ctx2, {
          type: 'bar',
          data: {
            labels: priceLabels,
            datasets: [{label:'Trades', data:priceCounts,
                       backgroundColor: priceCounts.map((c,i) => {
                         const idx = parseInt(priceLabels[i]);
                         return (idx >= 5 && idx <= 35) ? '#00d2ff' : '#1e3a5f';
                       })}]
          },
          options: {...chartDefaults, scales: axisDefaults,
                   plugins:{legend:{display:false},
                           title:{display:true, text:'Sweet spot: 5-40c (highlighted)',
                                  color:'#8892a4', font:{size:12}}}}
        });
      }
    }

    // Market type table
    if (rn1.market_types) {
      const mtb = document.getElementById('rn1-mtype-tbody');
      if (mtb) {
        mtb.innerHTML = Object.entries(rn1.market_types).map(([k,v]) => `<tr>
          <td>${k}</td>
          <td>${v.count}</td>
          <td>$${(v.usdc||0).toLocaleString(undefined,{maximumFractionDigits:0})}</td>
          <td>${v.pct_of_trades}%</td>
          <td>${(v.avg_price||0).toFixed(3)}</td>
        </tr>`).join('');
      }
    }

    // Merge stats
    if (rn1.merge_stats) {
      const ms = rn1.merge_stats;
      const msEl = document.getElementById('rn1-merge-stats');
      if (msEl) {
        msEl.innerHTML = `
          <div class="learn-stat"><div class="learn-label">Total Merges</div><div class="learn-value">${ms.count}</div></div>
          <div class="learn-stat"><div class="learn-label">Total USDC Merged</div><div class="learn-value">$${(ms.total_usdc||0).toLocaleString(undefined,{maximumFractionDigits:0})}</div></div>
          <div class="learn-stat"><div class="learn-label">Avg Merge Size</div><div class="learn-value">$${(ms.avg_size||0).toFixed(2)}</div></div>
          <div class="learn-stat"><div class="learn-label">Unique Slugs Merged</div><div class="learn-value">${ms.unique_slugs||0}</div></div>
        `;
      }
    }

    // Hour chart
    if (rn1.time_of_day && rn1.time_of_day.by_hour_utc) {
      const hours = Object.entries(rn1.time_of_day.by_hour_utc);
      const hLabels = hours.map(h => h[0] + ':00');
      const hCounts = hours.map(h => h[1].count);
      const ctx3 = document.getElementById('rn1HourChart');
      if (ctx3) {
        if (window._rn1HourChart) window._rn1HourChart.destroy();
        window._rn1HourChart = new Chart(ctx3, {
          type: 'bar',
          data: {labels: hLabels, datasets:[{label:'Trades', data:hCounts, backgroundColor:'#4caf50'}]},
          options: {...chartDefaults, scales: axisDefaults, plugins:{legend:{display:false}}}
        });
      }
    }

    // Holding period chart
    if (rn1.holding_periods && rn1.holding_periods.buckets) {
      const hb = Object.entries(rn1.holding_periods.buckets);
      const ctx4 = document.getElementById('rn1HoldChart');
      if (ctx4) {
        if (window._rn1HoldChart) window._rn1HoldChart.destroy();
        window._rn1HoldChart = new Chart(ctx4, {
          type: 'bar',
          data: {labels: hb.map(b=>b[0]), datasets:[{label:'Slugs', data:hb.map(b=>b[1]), backgroundColor:'#ff9800'}]},
          options: {...chartDefaults, scales: axisDefaults, plugins:{legend:{display:false}}}
        });
      }
    }

    // Summary cards
    const sumEl = document.getElementById('rn1-summary');
    if (sumEl && rn1.record_counts) {
      const rc = rn1.record_counts;
      sumEl.innerHTML = `
        <div class="learn-stat"><div class="learn-label">Total Records</div><div class="learn-value">${((rc.buys||0)+(rc.sells||0)+(rc.merges||0)+(rc.redeems||0)).toLocaleString()}</div></div>
        <div class="learn-stat"><div class="learn-label">Buys</div><div class="learn-value">${(rc.buys||0).toLocaleString()}</div></div>
        <div class="learn-stat"><div class="learn-label">Sells</div><div class="learn-value">${rc.sells||0}</div></div>
        <div class="learn-stat"><div class="learn-label">Merges</div><div class="learn-value">${(rc.merges||0).toLocaleString()}</div></div>
        <div class="learn-stat"><div class="learn-label">Redeems</div><div class="learn-value">${(rc.redeems||0).toLocaleString()}</div></div>
        <div class="learn-stat"><div class="learn-label">Avg Position Size</div><div class="learn-value">$${(rn1.position_sizing?.mean||0).toFixed(2)}</div></div>
        <div class="learn-stat"><div class="learn-label">Peak Hour (UTC)</div><div class="learn-value">${rn1.time_of_day?.peak_hour_utc ?? '--'}:00</div></div>
        <div class="learn-stat"><div class="learn-label">Peak Day</div><div class="learn-value">${rn1.time_of_day?.peak_day ?? '--'}</div></div>
        <div class="learn-stat"><div class="learn-label">Median Hold</div><div class="learn-value">${rn1.holding_periods?.median_hours ?? '--'}h</div></div>
        <div class="learn-stat"><div class="learn-label">Computed At</div><div class="learn-value" style="font-size:12px;">${rn1.computed_at || '--'}</div></div>
      `;
    }
  }
}

// === RN1 Live Activity ===
if (rn1live) {
  // Header badge
  const badge = document.getElementById('rn1-live-badge');
  if (badge) {
    const cnt = rn1live.active_market_count || 0;
    if (cnt > 0 && rn1live.tracker_alive) {
      badge.style.display = 'inline-block';
      badge.textContent = 'RN1: ' + cnt + ' mkt' + (cnt !== 1 ? 's' : '');
      badge.style.background = rn1live.hot_markets?.length > 0 ? 'var(--orange)' : 'var(--purple)';
    } else {
      badge.style.display = rn1live.tracker_alive ? 'inline-block' : 'none';
      badge.textContent = rn1live.tracker_alive ? 'RN1: idle' : '';
      badge.style.background = 'var(--text-muted)';
    }
  }

  // Status cards
  const statusEl = document.getElementById('rn1l-status');
  if (statusEl) {
    statusEl.textContent = rn1live.tracker_alive ? 'Online' : 'Offline';
    statusEl.style.color = rn1live.tracker_alive ? 'var(--green)' : 'var(--red)';
  }
  const setCard = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  setCard('rn1l-active', rn1live.active_market_count || 0);
  setCard('rn1l-hot', (rn1live.hot_markets || []).length);
  setCard('rn1l-new', (rn1live.new_markets || []).length);
  setCard('rn1l-trades', (rn1live.trades_last_5m || 0) + ' / ' + (rn1live.trades_last_15m || 0));
  setCard('rn1l-overlap', (rn1live.our_positions_in_rn1_markets || []).length);

  // Hot markets list
  const hotEl = document.getElementById('rn1l-hot-list');
  if (hotEl) {
    const hot = rn1live.hot_markets || [];
    if (hot.length > 0) {
      hotEl.innerHTML = hot.map(s => '<span class="badge" style="background:var(--orange);color:#000;margin:4px;">' + s + '</span>').join('');
    } else {
      hotEl.innerHTML = '<div class="empty">No hot markets right now</div>';
    }
  }

  // New markets list
  const newEl = document.getElementById('rn1l-new-list');
  if (newEl) {
    const nm = rn1live.new_markets || [];
    if (nm.length > 0) {
      newEl.innerHTML = nm.map(s => '<span class="badge" style="background:var(--green);color:#000;margin:4px;">' + s + '</span>').join('');
    } else {
      newEl.innerHTML = '<div class="empty">No new markets right now</div>';
    }
  }

  // Recent activity table
  const tbody = document.getElementById('rn1l-activity-tbody');
  if (tbody) {
    const acts = rn1live.recent_activity || [];
    if (acts.length > 0) {
      tbody.innerHTML = acts.map(a => {
        const typeColor = a.type === 'TRADE' ? 'var(--blue)' : a.type === 'REDEEM' ? 'var(--green)' : 'var(--yellow)';
        const dt = a.datetime ? a.datetime.replace('T',' ').substring(0,19) : '';
        return `<tr>
          <td class="mono" style="font-size:12px;">${dt}</td>
          <td><span style="color:${typeColor};font-weight:600;">${a.type}</span></td>
          <td><a href="https://polymarket.com/event/${a.slug}" target="_blank" title="${a.title || a.slug}">${shortSlug(a.slug)}</a></td>
          <td class="mono">$${(a.usdc_size || 0).toFixed(2)}</td>
        </tr>`;
      }).join('');
    } else {
      tbody.innerHTML = '<tr><td colspan="4" class="empty">No recent activity</td></tr>';
    }
  }

  // All active markets list
  const activeEl = document.getElementById('rn1l-active-list');
  if (activeEl) {
    const am = rn1live.active_markets || [];
    if (am.length > 0) {
      const overlap = new Set(rn1live.our_positions_in_rn1_markets || []);
      activeEl.innerHTML = am.map(s => {
        const style = overlap.has(s)
          ? 'background:var(--green);color:#000;margin:4px;'
          : 'background:var(--card-border);color:var(--text);margin:4px;';
        const label = overlap.has(s) ? s + ' [OUR POS]' : s;
        return '<span class="badge" style="' + style + '">' + label + '</span>';
      }).join('');
    } else {
      activeEl.innerHTML = '<div class="empty">No active markets in last 15 minutes</div>';
    }
  }
}

// Update "last updated" counter
function updateTimer() {
  const secs = Math.floor((Date.now() - lastUpdated) / 1000);
  document.getElementById('last-updated').textContent = 'Last updated: ' + secs + 's ago  |  Auto-refresh: 30s';
}

// Initial load
refreshAll();
setInterval(refreshAll, 30000);
setInterval(updateTimer, 1000);
</script>
</body>
</html>"""


@app.route("/")
def index():
    return Response(DASHBOARD_HTML, mimetype="text/html")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket Arb Bot Dashboard")
    parser.add_argument("--port", type=int, default=8050, help="Port (default 8050)")
    args = parser.parse_args()

    print(f"Starting dashboard on http://localhost:{args.port}")
    print(f"Data dir: {DATA_DIR}")
    app.run(host="0.0.0.0", port=args.port, debug=False)
