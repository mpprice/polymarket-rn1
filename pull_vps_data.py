#!/usr/bin/env python3
"""Pull latest data from VPS dashboard API and save locally.

Usage:
    python pull_vps_data.py              # paper mode (default)
    python pull_vps_data.py --mode live  # live mode
    python pull_vps_data.py --summary    # print summary only, no file save
"""
import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

BASE_URL = "http://89.167.8.255:8050"
DATA_DIR = Path(__file__).parent / "data" / "snapshots"

ENDPOINTS = [
    "summary",
    "positions",
    "resolved",
    "stats",
    "pnl_series",
    "daily_pnl",
    "calibration",
    "edge_distribution",
    "sport_heatmap",
    "learning",
    "rn1_live",
    "sports",
    "market_types",
]


SLOW_ENDPOINTS = {"positions", "resolved", "rn1"}  # these do live pricing

def fetch(endpoint: str, mode: str) -> dict | list | None:
    url = f"{BASE_URL}/api/{endpoint}?mode={mode}"
    timeout = 60 if endpoint in SLOW_ENDPOINTS else 15
    try:
        resp = urllib.request.urlopen(url, timeout=timeout)
        return json.loads(resp.read())
    except Exception as e:
        print(f"  WARN: {endpoint} failed: {e}", file=sys.stderr)
        return None


def print_summary(data: dict):
    summary = data.get("summary", {})
    positions_raw = data.get("positions", {})
    stats = data.get("stats", {})

    # Positions data
    if isinstance(positions_raw, dict):
        mtm = positions_raw.get("mtm_summary", {})
        positions = positions_raw.get("positions", [])
    else:
        mtm = {}
        positions = positions_raw or []

    open_pos = [p for p in positions if p.get("status", "").lower() == "open"]
    won = [p for p in positions if p.get("status", "").lower() == "won"]
    lost = [p for p in positions if p.get("status", "").lower() == "lost"]

    print("=" * 60)
    print(f"  POLYMARKET PAPER TRADING — {datetime.now(tz=__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")
    print("=" * 60)

    # Portfolio overview
    print(f"\n  Bot status:      {summary.get('bot_status', '?')}")
    print(f"  Open positions:  {len(open_pos)}")
    print(f"  Resolved:        {len(won)} W / {len(lost)} L "
          f"({len(won)/(len(won)+len(lost))*100:.0f}% WR)" if (won or lost) else
          f"  Resolved:        {len(won)} W / {len(lost)} L")
    print(f"  Total exposure:  ${mtm.get('total_cost', summary.get('total_exposure', 0)):.2f}")
    print(f"  MTM value:       ${mtm.get('total_mtm_value', 0):.2f}")
    print(f"  Unrealized P&L:  ${mtm.get('total_mtm_pnl', 0):+.2f}")
    print(f"  Realized P&L:    ${summary.get('total_pnl', 0):+.2f}")
    print(f"  Starting capital:${summary.get('starting_capital', 500):.0f}")

    # Last scan
    scan = summary.get("last_scan", {})
    if scan:
        print(f"\n  Last scan:       {scan.get('last_scan_utc', '?')} ({scan.get('last_scan_ago', '?')}s ago)")
        print(f"  Matched markets: {scan.get('matched_markets', '?')}")
        print(f"  New trades:      {scan.get('new_trades', 0)}")

    # Sport breakdown
    if open_pos:
        by_sport = defaultdict(lambda: {"count": 0, "cost": 0.0, "mtm": 0.0, "upnl": 0.0})
        for p in open_pos:
            s = p.get("sport", "?")
            by_sport[s]["count"] += 1
            by_sport[s]["cost"] += float(p.get("cost_usdc", 0))
            by_sport[s]["mtm"] += float(p.get("mtm_value", 0) or 0)
            by_sport[s]["upnl"] += float(p.get("mtm_pnl", 0) or 0)

        print(f"\n  {'Sport':6s} {'Pos':>4s} {'Cost':>8s} {'MTM':>8s} {'uPnL':>8s}")
        print(f"  {'-'*6} {'-'*4} {'-'*8} {'-'*8} {'-'*8}")
        for s, v in sorted(by_sport.items(), key=lambda x: -x[1]["count"]):
            print(f"  {s:6s} {v['count']:4d} ${v['cost']:7.2f} ${v['mtm']:7.2f} ${v['upnl']:+7.2f}")

    # Top movers
    if open_pos:
        sorted_pos = sorted(open_pos, key=lambda p: float(p.get("mtm_pnl", 0) or 0), reverse=True)
        print(f"\n  Top 3 winners:")
        for p in sorted_pos[:3]:
            upnl = float(p.get("mtm_pnl", 0) or 0)
            print(f"    {p['slug'][:45]:45s} {p['outcome'][:18]:18s} ${upnl:+.2f}")
        print(f"  Top 3 losers:")
        for p in sorted_pos[-3:]:
            upnl = float(p.get("mtm_pnl", 0) or 0)
            print(f"    {p['slug'][:45]:45s} {p['outcome'][:18]:18s} ${upnl:+.2f}")

    # Edge stats
    edges = [float(p.get("edge_pct", 0)) for p in open_pos if p.get("edge_pct")]
    if edges:
        print(f"\n  Edge: min={min(edges):.1f}% avg={sum(edges)/len(edges):.1f}% max={max(edges):.1f}%")

    # Stats
    if stats.get("total_resolved", 0) > 0:
        print(f"\n  Sharpe:          {stats.get('sharpe', 0):.2f}")
        print(f"  ROI:             {stats.get('roi', 0):.1f}%")
        print(f"  Profit factor:   {stats.get('profit_factor', 0):.2f}")
        print(f"  Max drawdown:    ${stats.get('max_dd_dollar', 0):.2f} ({stats.get('max_dd_pct', 0):.1f}%)")

    print()


def main():
    parser = argparse.ArgumentParser(description="Pull VPS dashboard data")
    parser.add_argument("--mode", default="paper", choices=["paper", "live"])
    parser.add_argument("--summary", action="store_true", help="Print summary only")
    args = parser.parse_args()

    print(f"Pulling data from {BASE_URL} (mode={args.mode})...")
    all_data = {}
    for ep in ENDPOINTS:
        result = fetch(ep, args.mode)
        if result is not None:
            all_data[ep] = result
            print(f"  OK: {ep}")

    if not all_data:
        print("ERROR: Could not fetch any data from VPS", file=sys.stderr)
        sys.exit(1)

    print_summary(all_data)

    if not args.summary:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(tz=__import__('datetime').timezone.utc).strftime("%Y%m%d_%H%M%S")

        # Save timestamped snapshot
        snapshot_path = DATA_DIR / f"snapshot_{args.mode}_{ts}.json"
        with open(snapshot_path, "w") as f:
            json.dump(all_data, f, indent=2)
        print(f"Saved snapshot: {snapshot_path}")

        # Save latest (overwrite)
        latest_path = DATA_DIR / f"latest_{args.mode}.json"
        with open(latest_path, "w") as f:
            json.dump(all_data, f, indent=2)
        print(f"Saved latest:   {latest_path}")

        # Also save positions as CSV for easy analysis
        positions_raw = all_data.get("positions", {})
        positions = positions_raw.get("positions", []) if isinstance(positions_raw, dict) else positions_raw
        if positions:
            import csv
            csv_path = DATA_DIR / f"positions_{args.mode}_{ts}.csv"
            keys = positions[0].keys()
            with open(csv_path, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=keys)
                w.writeheader()
                w.writerows(positions)
            print(f"Saved CSV:      {csv_path}")


if __name__ == "__main__":
    main()
