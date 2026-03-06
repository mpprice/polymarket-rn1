#!/usr/bin/env python3
"""Closing Line Value (CLV) Baseline Tracker.

CLV is the gold standard for measuring betting skill. If you consistently
beat the closing line (the final odds before game start), you have a
genuine edge -- regardless of short-term P&L variance.

This script:
1. Takes a snapshot of current sharp odds and Polymarket prices
2. Saves to data/clv_snapshots/ with timestamp
3. When run with --analyze, compares historical snapshots to measure CLV

Design:
- Run every 30-60 minutes via cron/Task Scheduler
- Minimal API usage (1-2 calls per run, only NBA + EPL)
- Each snapshot stores: event, odds, Polymarket price, timestamp, hours_to_start
- After events start, the last pre-game snapshot is the "closing line"

Cron example (every 30 minutes):
    */30 * * * * cd /path/to/polymarket-rn1 && python analysis/clv_baseline.py --snapshot

Usage:
    python analysis/clv_baseline.py --snapshot       # Take a snapshot
    python analysis/clv_baseline.py --analyze        # Analyze CLV from snapshots
    python analysis/clv_baseline.py --status         # Show snapshot inventory
"""
import json
import math
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = PROJECT_ROOT / "data" / "clv_snapshots"
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
CACHE_DIR = PROJECT_ROOT / "data" / "odds_cache"

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "b163f7a2df21a194b548a36fea51c4ef")
ODDS_API_BASE = "https://api.the-odds-api.com/v4"
GAMMA_URL = "https://gamma-api.polymarket.com"

# Only track 2 sports to minimize API usage (2 calls per snapshot)
CLV_SPORTS = {
    "basketball_nba": "NBA",
    "soccer_epl": "EPL",
}

SHARP_BOOKS = ["pinnacle", "betfair_ex_eu"]


def take_snapshot():
    """Take a snapshot of current odds and save to file."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y%m%d_%H%M%S")
    print(f"Taking CLV snapshot at {now.strftime('%Y-%m-%d %H:%M:%S UTC')}")

    snapshot = {
        "timestamp": now.isoformat(),
        "events": [],
    }

    for sport_key, sport_name in CLV_SPORTS.items():
        print(f"  Fetching {sport_name}...")
        try:
            url = f"{ODDS_API_BASE}/sports/{sport_key}/odds"
            params = {
                "apiKey": ODDS_API_KEY,
                "regions": "eu",
                "markets": "h2h",
                "oddsFormat": "decimal",
                "bookmakers": ",".join(SHARP_BOOKS),
            }
            resp = requests.get(url, params=params, timeout=15)
            remaining = resp.headers.get("x-requests-remaining", "?")
            print(f"    Status: {resp.status_code} | Quota remaining: {remaining}")

            if resp.status_code != 200:
                print(f"    Error: {resp.text[:100]}")
                continue

            events = resp.json()
            for event in events:
                parsed = _parse_event(event, sport_key, sport_name, now)
                if parsed:
                    snapshot["events"].append(parsed)

            print(f"    Got {len([e for e in snapshot['events'] if e['sport_key'] == sport_key])} events")
            time.sleep(0.3)

        except Exception as e:
            print(f"    Error: {e}")

    # Save snapshot
    filename = f"snapshot_{timestamp}.json"
    filepath = SNAPSHOT_DIR / filename
    with open(filepath, "w") as f:
        json.dump(snapshot, f, indent=2)

    print(f"\n  Saved {len(snapshot['events'])} events to {filename}")
    print(f"  Total snapshots in directory: {len(list(SNAPSHOT_DIR.glob('snapshot_*.json')))}")
    return filepath


def _parse_event(event, sport_key, sport_name, now):
    """Parse a single event from The Odds API into snapshot format."""
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    commence = event.get("commence_time", "")

    # Calculate hours to start
    hours_to_start = None
    if commence:
        try:
            start_dt = datetime.fromisoformat(commence.replace("Z", "+00:00"))
            delta = start_dt - now
            hours_to_start = delta.total_seconds() / 3600
        except (ValueError, TypeError):
            pass

    # Skip events that have already started
    if hours_to_start is not None and hours_to_start < -0.5:
        return None

    # Extract h2h odds from sharpest bookmaker
    bookmakers = event.get("bookmakers", [])
    sharp = None
    for pref in SHARP_BOOKS:
        for bm in bookmakers:
            if bm["key"] == pref:
                sharp = bm
                break
        if sharp:
            break
    if not sharp and bookmakers:
        sharp = bookmakers[0]
    if not sharp:
        return None

    h2h = None
    for market in sharp.get("markets", []):
        if market["key"] == "h2h":
            h2h = market
            break
    if not h2h:
        return None

    outcomes = {}
    for o in h2h.get("outcomes", []):
        dec = o["price"]
        outcomes[o["name"]] = {
            "decimal_odds": dec,
            "implied_prob": 1.0 / dec if dec > 0 else 0,
        }

    total_implied = sum(v["implied_prob"] for v in outcomes.values())
    for name in outcomes:
        outcomes[name]["fair_prob"] = outcomes[name]["implied_prob"] / total_implied if total_implied > 0 else 0

    return {
        "event_id": event.get("id", ""),
        "sport_key": sport_key,
        "sport_name": sport_name,
        "home_team": home,
        "away_team": away,
        "commence_time": commence,
        "hours_to_start": hours_to_start,
        "bookmaker": sharp["key"],
        "overround": total_implied - 1.0,
        "outcomes": outcomes,
        "snapshot_time": now.isoformat(),
    }


def load_all_snapshots():
    """Load all snapshots from disk."""
    snapshots = []
    for f in sorted(SNAPSHOT_DIR.glob("snapshot_*.json")):
        with open(f) as fh:
            data = json.load(fh)
            data["_filename"] = f.name
            snapshots.append(data)
    return snapshots


def show_status():
    """Show snapshot inventory."""
    snapshots = load_all_snapshots()
    print(f"\n{'=' * 60}")
    print(f"CLV SNAPSHOT INVENTORY")
    print(f"{'=' * 60}")
    print(f"Directory: {SNAPSHOT_DIR}")
    print(f"Total snapshots: {len(snapshots)}")

    if not snapshots:
        print("\nNo snapshots yet. Run with --snapshot to create one.")
        return

    # Time range
    first = snapshots[0].get("timestamp", "")
    last = snapshots[-1].get("timestamp", "")
    print(f"First: {first}")
    print(f"Last:  {last}")

    # Events per snapshot
    for snap in snapshots[-5:]:
        n = len(snap.get("events", []))
        ts = snap.get("timestamp", "?")[:19]
        sports = {}
        for e in snap.get("events", []):
            sports[e.get("sport_name", "?")] = sports.get(e.get("sport_name", "?"), 0) + 1
        sport_str = ", ".join(f"{k}:{v}" for k, v in sports.items())
        print(f"  {ts} | {n} events ({sport_str})")

    # Unique events tracked
    event_ids = set()
    for snap in snapshots:
        for e in snap.get("events", []):
            event_ids.add(e.get("event_id", ""))
    print(f"\nUnique events tracked: {len(event_ids)}")

    # Events with multiple snapshots (needed for CLV analysis)
    event_counts = {}
    for snap in snapshots:
        for e in snap.get("events", []):
            eid = e.get("event_id", "")
            event_counts[eid] = event_counts.get(eid, 0) + 1
    multi = sum(1 for c in event_counts.values() if c >= 2)
    print(f"Events with 2+ snapshots: {multi} (needed for CLV)")


def analyze_clv():
    """Analyze CLV from collected snapshots.

    CLV = (entry_fair_prob / closing_fair_prob - 1) * 100

    Positive CLV means we're consistently beating the closing line.
    In sharp markets, ~55% of random bets beat the close.
    Skilled bettors maintain 1-3% CLV.
    """
    snapshots = load_all_snapshots()

    print(f"\n{'=' * 70}")
    print(f"CLOSING LINE VALUE (CLV) ANALYSIS")
    print(f"{'=' * 70}")

    if len(snapshots) < 2:
        print(f"\nNeed at least 2 snapshots for CLV analysis.")
        print(f"Currently have: {len(snapshots)}")
        print(f"\nTo build CLV data:")
        print(f"  1. Run: python analysis/clv_baseline.py --snapshot")
        print(f"  2. Wait 30-60 minutes")
        print(f"  3. Run: python analysis/clv_baseline.py --snapshot")
        print(f"  4. Repeat until games start")
        print(f"  5. Run: python analysis/clv_baseline.py --analyze")
        return

    # Build timeline per event
    event_timeline = {}  # event_id -> list of (timestamp, fair_probs, hours_to_start)
    for snap in snapshots:
        snap_time = snap.get("timestamp", "")
        for ev in snap.get("events", []):
            eid = ev.get("event_id", "")
            if not eid:
                continue
            entry = {
                "timestamp": snap_time,
                "hours_to_start": ev.get("hours_to_start"),
                "outcomes": ev.get("outcomes", {}),
                "home": ev.get("home_team", ""),
                "away": ev.get("away_team", ""),
                "sport": ev.get("sport_name", ""),
                "commence": ev.get("commence_time", ""),
            }
            event_timeline.setdefault(eid, []).append(entry)

    # Sort each timeline by timestamp
    for eid in event_timeline:
        event_timeline[eid].sort(key=lambda x: x["timestamp"])

    # Identify events where we have early + late snapshots
    clv_events = []
    for eid, timeline in event_timeline.items():
        if len(timeline) < 2:
            continue

        # "Opening" = earliest snapshot, "Closing" = latest snapshot
        opening = timeline[0]
        closing = timeline[-1]

        # Skip if both snapshots are far from game time
        if closing.get("hours_to_start") is not None and closing["hours_to_start"] > 24:
            continue

        clv_events.append({
            "event_id": eid,
            "home": opening["home"],
            "away": opening["away"],
            "sport": opening["sport"],
            "commence": opening["commence"],
            "n_snapshots": len(timeline),
            "opening": opening,
            "closing": closing,
            "timeline": timeline,
        })

    print(f"\nEvents with CLV data: {len(clv_events)}")
    if not clv_events:
        print("No events with multiple snapshots close to game time.")
        print("Keep taking snapshots and rerun analysis.")
        return

    # Calculate CLV for each outcome
    print(f"\n--- LINE MOVEMENT ANALYSIS ---")
    all_movements = []

    for ev in clv_events:
        print(f"\n  {ev['home']} vs {ev['away']} ({ev['sport']}, {ev['n_snapshots']} snapshots)")
        open_outcomes = ev["opening"]["outcomes"]
        close_outcomes = ev["closing"]["outcomes"]

        for name in open_outcomes:
            if name not in close_outcomes:
                continue
            open_fair = open_outcomes[name].get("fair_prob", 0)
            close_fair = close_outcomes[name].get("fair_prob", 0)
            if open_fair <= 0 or close_fair <= 0:
                continue

            movement = (close_fair - open_fair) * 100  # in percentage points
            clv_pct = ((open_fair / close_fair) - 1) * 100 if close_fair > 0 else 0

            move = {
                "event_id": ev["event_id"],
                "sport": ev["sport"],
                "match": f"{ev['home']} vs {ev['away']}",
                "outcome": name,
                "open_fair": open_fair,
                "close_fair": close_fair,
                "movement_pp": movement,
                "clv_pct": clv_pct,
                "hours_open": ev["opening"].get("hours_to_start"),
                "hours_close": ev["closing"].get("hours_to_start"),
            }
            all_movements.append(move)

            direction = "+" if movement > 0 else ""
            print(f"    {name}: {open_fair:.4f} -> {close_fair:.4f} "
                  f"({direction}{movement:.2f}pp)")

    if not all_movements:
        print("\nNo line movements to analyze.")
        return

    # Summary statistics
    print(f"\n{'=' * 70}")
    print(f"CLV SUMMARY STATISTICS")
    print(f"{'=' * 70}")

    movements = [m["movement_pp"] for m in all_movements]
    abs_movements = [abs(m) for m in movements]

    avg_move = sum(abs_movements) / len(abs_movements)
    max_move = max(abs_movements)
    n_up = sum(1 for m in movements if m > 0.5)
    n_down = sum(1 for m in movements if m < -0.5)
    n_flat = len(movements) - n_up - n_down

    print(f"  Total outcome lines tracked: {len(movements)}")
    print(f"  Avg absolute movement: {avg_move:.2f}pp")
    print(f"  Max movement: {max_move:.2f}pp")
    print(f"  Moved up (>0.5pp): {n_up} ({n_up/len(movements)*100:.0f}%)")
    print(f"  Moved down (<-0.5pp): {n_down} ({n_down/len(movements)*100:.0f}%)")
    print(f"  Flat: {n_flat} ({n_flat/len(movements)*100:.0f}%)")

    # CLV interpretation
    print(f"\n--- CLV INTERPRETATION ---")
    print(f"  If our bot had bet at the opening prices:")
    clvs = [m["clv_pct"] for m in all_movements if abs(m["clv_pct"]) < 50]
    if clvs:
        avg_clv = sum(clvs) / len(clvs)
        positive_clv = sum(1 for c in clvs if c > 0)
        print(f"    Average CLV: {avg_clv:+.2f}%")
        print(f"    Positive CLV rate: {positive_clv}/{len(clvs)} "
              f"({positive_clv/len(clvs)*100:.0f}%)")
        print(f"    (Random would be ~50%, skilled = 55%+)")

    # By sport
    print(f"\n  By sport:")
    by_sport = {}
    for m in all_movements:
        by_sport.setdefault(m["sport"], []).append(m)
    for sport, items in sorted(by_sport.items()):
        avg_abs = sum(abs(m["movement_pp"]) for m in items) / len(items)
        print(f"    {sport}: {len(items)} lines, avg |movement|={avg_abs:.2f}pp")

    # Save analysis
    out_file = SNAPSHOT_DIR / f"clv_analysis_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.json"
    with open(out_file, "w") as f:
        json.dump(all_movements, f, indent=2)
    print(f"\n  Analysis saved to: {out_file.name}")

    # Guidance for next steps
    print(f"\n--- NEXT STEPS ---")
    print(f"  1. Continue taking snapshots every 30-60 minutes")
    print(f"  2. After 50+ events with 3+ snapshots each, CLV data becomes reliable")
    print(f"  3. Target: consistent positive CLV > 1% = genuine edge")
    print(f"  4. If CLV is negative, our fair value model needs recalibration")
    print(f"  5. Integrate CLV tracking into the live bot (src/edge_config.py has CLVConfig)")


def main():
    if "--snapshot" in sys.argv:
        take_snapshot()
    elif "--analyze" in sys.argv:
        analyze_clv()
    elif "--status" in sys.argv:
        show_status()
    else:
        print("CLV Baseline Tracker")
        print()
        print("Usage:")
        print("  python analysis/clv_baseline.py --snapshot   Take odds snapshot")
        print("  python analysis/clv_baseline.py --analyze    Analyze CLV from snapshots")
        print("  python analysis/clv_baseline.py --status     Show snapshot inventory")
        print()
        print("Quick start:")
        print("  1. Run --snapshot now")
        print("  2. Wait 30-60 minutes")
        print("  3. Run --snapshot again")
        print("  4. Run --analyze to see line movements")
        print()
        print("For automated collection (Linux/Mac cron):")
        print("  */30 * * * * cd /path/to/polymarket-rn1 && python analysis/clv_baseline.py --snapshot")
        print()
        print("For automated collection (Windows Task Scheduler):")
        print("  Create task running every 30 min: python analysis/clv_baseline.py --snapshot")


if __name__ == "__main__":
    main()
