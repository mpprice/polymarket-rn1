#!/usr/bin/env python3
"""Polymarket Arb Bot - Paper Trading Dashboard.

Single-file Flask dashboard for monitoring the Polymarket sports arbitrage
paper trading bot. Reads data from CSV/JSON files and bot.log.

Usage:
    python dashboard.py
    python dashboard.py --port 8080
"""
import argparse
import csv
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, Response

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
POSITIONS_FILE = DATA_DIR / "my_positions.csv"
TRADES_FILE = DATA_DIR / "my_trades.csv"
LEARNING_FILE = DATA_DIR / "learning_history.json"
LOG_FILE = BASE_DIR / "bot.log"


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


def _read_log_lines(n: int = 50) -> list[str]:
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

    return jsonify({
        "bot_status": _bot_status(),
        "total_pnl": round(total_pnl, 2),
        "win_rate": round(win_rate, 1),
        "wins": wins,
        "losses": losses,
        "open_count": len(open_pos),
        "total_exposure": round(total_exposure, 2),
        "total_trades": total_trades,
        "best_trade": round(best_trade_pnl, 2),
        "utc_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
    })


@app.route("/api/positions")
def api_positions():
    positions = _read_csv(POSITIONS_FILE)
    open_pos = [p for p in positions if p.get("status", "").lower() == "open"]
    open_pos.sort(key=lambda p: _safe_float(p.get("cost_usdc")), reverse=True)
    return jsonify(open_pos)


@app.route("/api/resolved")
def api_resolved():
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]
    resolved.sort(key=lambda p: p.get("closed_at", ""), reverse=True)
    return jsonify(resolved[:50])


@app.route("/api/log")
def api_log():
    return jsonify({"lines": _read_log_lines(50)})


@app.route("/api/sports")
def api_sports():
    positions = _read_csv(POSITIONS_FILE)
    resolved = [p for p in positions if p.get("status", "").lower() in ("won", "lost")]

    sports: dict[str, dict] = {}
    for p in resolved:
        sport = p.get("sport", "unknown")
        if sport not in sports:
            sports[sport] = {"sport": sport, "count": 0, "wins": 0, "losses": 0, "pnl": 0.0}
        sports[sport]["count"] += 1
        if p.get("status", "").lower() == "won":
            sports[sport]["wins"] += 1
        else:
            sports[sport]["losses"] += 1
        sports[sport]["pnl"] += _safe_float(p.get("pnl"))

    result = sorted(sports.values(), key=lambda s: s["pnl"], reverse=True)
    for r in result:
        r["pnl"] = round(r["pnl"], 2)
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

    # Best sport
    sport_stats: dict[str, dict] = {}
    for t in trades:
        s = t.get("sport", "unknown")
        if s not in sport_stats:
            sport_stats[s] = {"wins": 0, "total": 0}
        sport_stats[s]["total"] += 1
        if t.get("won"):
            sport_stats[s]["wins"] += 1

    best_sport = ""
    best_sport_wr = 0.0
    for s, v in sport_stats.items():
        wr = v["wins"] / v["total"] * 100 if v["total"] > 0 else 0
        if wr > best_sport_wr and v["total"] >= 3:
            best_sport_wr = wr
            best_sport = s

    # Best market type
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

    return jsonify({
        "available": True,
        "trade_count": trade_count,
        "win_rate": round(win_rate, 1),
        "best_sport": best_sport,
        "best_sport_wr": round(best_sport_wr, 1),
        "best_market_type": best_mt,
        "best_mt_wr": round(best_mt_wr, 1),
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
    --header-bg: #0f3460;
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  .mono { font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace; }

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
  }
  .header h1 {
    font-size: 20px;
    font-weight: 700;
    color: #fff;
    letter-spacing: 0.5px;
  }
  .header h1 span { color: var(--green); }
  .header-right {
    display: flex;
    align-items: center;
    gap: 16px;
    flex-wrap: wrap;
  }
  .badge {
    padding: 4px 12px;
    border-radius: 12px;
    font-size: 12px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }
  .badge-paper { background: var(--yellow); color: #1a1a2e; }
  .badge-running { background: var(--green); color: #1a1a2e; }
  .badge-stopped { background: var(--red); color: #fff; }
  .utc-time { color: var(--text-muted); font-size: 13px; }
  .last-updated { color: var(--text-muted); font-size: 11px; text-align: right; }

  /* Container */
  .container { max-width: 1400px; margin: 0 auto; padding: 20px; }

  /* Summary cards */
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
    gap: 16px;
    margin-bottom: 24px;
  }
  .card {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 18px;
    text-align: center;
  }
  .card-label {
    font-size: 12px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 1px;
    margin-bottom: 8px;
  }
  .card-value {
    font-size: 28px;
    font-weight: 700;
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
  }
  .card-sub { font-size: 11px; color: var(--text-muted); margin-top: 4px; }
  .pnl-pos { color: var(--green); }
  .pnl-neg { color: var(--red); }

  /* Sections */
  .section {
    background: var(--card-bg);
    border: 1px solid var(--card-border);
    border-radius: 10px;
    padding: 20px;
    margin-bottom: 20px;
  }
  .section h2 {
    font-size: 16px;
    font-weight: 600;
    margin-bottom: 14px;
    color: var(--blue);
    border-bottom: 1px solid var(--card-border);
    padding-bottom: 8px;
  }

  /* Tables */
  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }
  th {
    text-align: left;
    padding: 8px 10px;
    color: var(--text-muted);
    font-weight: 600;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    border-bottom: 1px solid var(--card-border);
  }
  td {
    padding: 7px 10px;
    border-bottom: 1px solid rgba(15,52,96,0.5);
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 12px;
  }
  td.slug-col {
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  tr:hover { background: rgba(79,195,247,0.05); }
  tr.row-won { background: rgba(0,212,170,0.08); }
  tr.row-lost { background: rgba(255,107,107,0.08); }
  .edge-high { color: var(--green); font-weight: 600; }
  .edge-mid { color: var(--yellow); }
  .edge-low { color: var(--text); }

  /* Charts grid */
  .charts-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 20px;
    margin-bottom: 20px;
  }
  @media (max-width: 900px) {
    .charts-grid { grid-template-columns: 1fr; }
  }
  .chart-container { position: relative; height: 260px; }

  /* Log */
  .log-box {
    background: #0d1117;
    border: 1px solid #30363d;
    border-radius: 6px;
    padding: 12px;
    max-height: 400px;
    overflow-y: auto;
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 11px;
    line-height: 1.5;
  }
  .log-line { white-space: pre-wrap; word-break: break-all; }
  .log-order { color: var(--blue); }
  .log-resolved { color: var(--green); }
  .log-error { color: var(--red); font-weight: 600; }

  /* Learning */
  .learning-grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
    gap: 14px;
  }
  .learn-stat {
    background: rgba(15,52,96,0.4);
    border-radius: 8px;
    padding: 14px;
    text-align: center;
  }
  .learn-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; text-transform: uppercase; }
  .learn-value { font-size: 22px; font-weight: 700; font-family: 'Cascadia Code', monospace; }

  /* Empty state */
  .empty { text-align: center; color: var(--text-muted); padding: 30px; font-size: 14px; }

  /* Responsive table wrapper */
  .table-wrap { overflow-x: auto; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; height: 6px; }
  ::-webkit-scrollbar-track { background: var(--bg); }
  ::-webkit-scrollbar-thumb { background: var(--card-border); border-radius: 3px; }
</style>
</head>
<body>

<!-- Header -->
<div class="header">
  <div>
    <h1><span>&#9670;</span> Polymarket Arb Bot <span>|</span> Paper Trading Dashboard</h1>
  </div>
  <div class="header-right">
    <span class="badge badge-paper">Paper Trade</span>
    <span id="bot-status" class="badge badge-stopped">Stopped</span>
    <span id="utc-time" class="utc-time"></span>
  </div>
</div>

<div class="container">
  <div class="last-updated" id="last-updated">Loading...</div>

  <!-- Summary Cards -->
  <div class="cards">
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
      <div class="card-label">Best Trade</div>
      <div class="card-value pnl-pos" id="card-best">$0</div>
    </div>
  </div>

  <!-- Open Positions -->
  <div class="section">
    <h2>Open Positions</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Slug</th><th>Outcome</th><th>Sport</th><th>Type</th>
            <th>Entry</th><th>Fair Prob</th><th>Edge%</th>
            <th>Shares</th><th>Cost</th><th>Opened</th>
          </tr>
        </thead>
        <tbody id="open-tbody"></tbody>
      </table>
      <div class="empty" id="open-empty" style="display:none;">No open positions</div>
    </div>
  </div>

  <!-- Resolved Positions -->
  <div class="section">
    <h2>Resolved Positions (Last 50)</h2>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Slug</th><th>Outcome</th><th>Sport</th><th>Type</th>
            <th>Entry</th><th>Resolution</th><th>Shares</th><th>Cost</th>
            <th>Payout</th><th>PnL</th><th>Status</th><th>Closed</th>
          </tr>
        </thead>
        <tbody id="resolved-tbody"></tbody>
      </table>
      <div class="empty" id="resolved-empty" style="display:none;">No resolved positions yet</div>
    </div>
  </div>

  <!-- Charts: Sports & Market Types -->
  <div class="charts-grid">
    <div class="section">
      <h2>P&amp;L by Sport</h2>
      <div class="chart-container"><canvas id="sportChart"></canvas></div>
      <div class="table-wrap" style="margin-top:14px;">
        <table>
          <thead><tr><th>Sport</th><th>Trades</th><th>W</th><th>L</th><th>PnL</th></tr></thead>
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

  <!-- Learning Agent -->
  <div class="section" id="learning-section" style="display:none;">
    <h2>Learning Agent Stats</h2>
    <div class="learning-grid" id="learning-grid"></div>
  </div>

  <!-- Log -->
  <div class="section">
    <h2>Recent Bot Log</h2>
    <div class="log-box" id="log-box"></div>
  </div>
</div>

<script>
let lastUpdated = Date.now();
let sportChart = null;
let mtChart = null;

function fmt$(v) {
  const n = parseFloat(v) || 0;
  const sign = n >= 0 ? '+' : '';
  return sign + '$' + n.toFixed(2);
}

function pnlClass(v) {
  const n = parseFloat(v) || 0;
  return n >= 0 ? 'pnl-pos' : 'pnl-neg';
}

function edgeClass(v) {
  const n = parseFloat(v) || 0;
  if (n >= 10) return 'edge-high';
  if (n >= 5) return 'edge-mid';
  return 'edge-low';
}

function shortSlug(s) {
  if (!s) return '';
  return s.length > 40 ? s.substring(0, 37) + '...' : s;
}

function shortTime(t) {
  if (!t) return '';
  return t.replace('T', ' ').substring(0, 19);
}

async function fetchJSON(url) {
  try {
    const r = await fetch(url);
    return await r.json();
  } catch(e) {
    console.error('Fetch error:', url, e);
    return null;
  }
}

async function refreshAll() {
  const [summary, positions, resolved, log, sports, mtypes, learning] = await Promise.all([
    fetchJSON('/api/summary'),
    fetchJSON('/api/positions'),
    fetchJSON('/api/resolved'),
    fetchJSON('/api/log'),
    fetchJSON('/api/sports'),
    fetchJSON('/api/market_types'),
    fetchJSON('/api/learning'),
  ]);

  lastUpdated = Date.now();

  // Summary
  if (summary) {
    const pnlEl = document.getElementById('card-pnl');
    pnlEl.textContent = fmt$(summary.total_pnl);
    pnlEl.className = 'card-value ' + pnlClass(summary.total_pnl);

    document.getElementById('card-wl').textContent = summary.wins + 'W / ' + summary.losses + 'L';
    document.getElementById('card-wr').textContent = summary.win_rate + '%';
    document.getElementById('card-open').textContent = summary.open_count;
    document.getElementById('card-exposure').textContent = '$' + summary.total_exposure.toFixed(2);
    document.getElementById('card-trades').textContent = summary.total_trades;
    document.getElementById('card-best').textContent = fmt$(summary.best_trade);
    document.getElementById('utc-time').textContent = summary.utc_now;

    const statusEl = document.getElementById('bot-status');
    if (summary.bot_status === 'Running') {
      statusEl.textContent = 'Running';
      statusEl.className = 'badge badge-running';
    } else {
      statusEl.textContent = 'Stopped';
      statusEl.className = 'badge badge-stopped';
    }
  }

  // Open positions
  if (positions) {
    const tbody = document.getElementById('open-tbody');
    const empty = document.getElementById('open-empty');
    if (positions.length === 0) {
      tbody.innerHTML = '';
      empty.style.display = 'block';
    } else {
      empty.style.display = 'none';
      tbody.innerHTML = positions.map(p => `<tr>
        <td class="slug-col" title="${p.slug || ''}">${shortSlug(p.slug)}</td>
        <td>${p.outcome || ''}</td>
        <td>${p.sport || ''}</td>
        <td>${p.market_type || ''}</td>
        <td>${parseFloat(p.entry_price||0).toFixed(3)}</td>
        <td>${parseFloat(p.fair_prob||0).toFixed(3)}</td>
        <td class="${edgeClass(p.edge_pct)}">${parseFloat(p.edge_pct||0).toFixed(1)}%</td>
        <td>${parseFloat(p.shares||0).toFixed(1)}</td>
        <td>$${parseFloat(p.cost_usdc||0).toFixed(2)}</td>
        <td>${shortTime(p.opened_at)}</td>
      </tr>`).join('');
    }
  }

  // Resolved positions
  if (resolved) {
    const tbody = document.getElementById('resolved-tbody');
    const empty = document.getElementById('resolved-empty');
    if (resolved.length === 0) {
      tbody.innerHTML = '';
      empty.style.display = 'block';
    } else {
      empty.style.display = 'none';
      tbody.innerHTML = resolved.map(p => {
        const cls = p.status && p.status.toLowerCase() === 'won' ? 'row-won' : 'row-lost';
        const pnl = parseFloat(p.pnl||0);
        return `<tr class="${cls}">
          <td class="slug-col" title="${p.slug || ''}">${shortSlug(p.slug)}</td>
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
        </tr>`;
      }).join('');
    }
  }

  // Sports breakdown
  if (sports) {
    const tbody = document.getElementById('sport-tbody');
    tbody.innerHTML = sports.map(s => `<tr>
      <td class="slug-col">${s.sport}</td>
      <td>${s.count}</td>
      <td class="pnl-pos">${s.wins}</td>
      <td class="pnl-neg">${s.losses}</td>
      <td class="${pnlClass(s.pnl)}">${fmt$(s.pnl)}</td>
    </tr>`).join('');

    // Chart
    const labels = sports.map(s => s.sport);
    const data = sports.map(s => s.pnl);
    const colors = data.map(v => v >= 0 ? '#00d4aa' : '#ff6b6b');

    if (sportChart) sportChart.destroy();
    const ctx = document.getElementById('sportChart').getContext('2d');
    sportChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'P&L ($)',
          data: data,
          backgroundColor: colors,
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8892a4', font: { size: 11 } }, grid: { display: false } },
          y: { ticks: { color: '#8892a4', callback: v => '$'+v }, grid: { color: 'rgba(15,52,96,0.5)' } }
        }
      }
    });
  }

  // Market types breakdown
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

    if (mtChart) mtChart.destroy();
    const ctx = document.getElementById('mtChart').getContext('2d');
    mtChart = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{
          label: 'P&L ($)',
          data: data,
          backgroundColor: colors,
          borderRadius: 4,
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: '#8892a4', font: { size: 11 } }, grid: { display: false } },
          y: { ticks: { color: '#8892a4', callback: v => '$'+v }, grid: { color: 'rgba(15,52,96,0.5)' } }
        }
      }
    });
  }

  // Learning agent
  if (learning && learning.available) {
    document.getElementById('learning-section').style.display = 'block';
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
  }

  // Log
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
}

// Update "last updated" counter every second
function updateTimer() {
  const secs = Math.floor((Date.now() - lastUpdated) / 1000);
  document.getElementById('last-updated').textContent = 'Last updated: ' + secs + 's ago';
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
