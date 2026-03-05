#!/usr/bin/env python3
"""Analyze RN1's true P&L from Polymarket /activity API data.

The /positions API only shows positions with size > 0. When positions resolve
(winning side redeemed) or are merged (YES+NO combined), size drops to 0 and
they DISAPPEAR from the API. This means /positions shows ALL losses but NONE
of the profits -- leading to the erroneous PANews claim of -$920K.

The /activity API shows ALL events: TRADE, REDEEM, MERGE, REWARD.
This script computes true P&L from activity data.

Usage:
    # Download fresh activity data and analyze
    python analysis/rn1_activity_pnl.py --download

    # Analyze existing data
    python analysis/rn1_activity_pnl.py
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

import requests


WALLET = "0x2005D16a84CEEfa912D4e380cD32E7ff827875Ea"
ACTIVITY_URL = "https://data-api.polymarket.com/activity"
DATA_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "rn1_full_activity.json")


def download_activity(end_timestamp=None, max_batches=500):
    """Download all activity records by paginating backwards via end timestamp."""
    all_data = []
    end_ts = end_timestamp or int(time.time())
    batch_size = 500

    for i in range(max_batches):
        resp = requests.get(ACTIVITY_URL, params={
            "user": WALLET,
            "limit": batch_size,
            "end": end_ts,
        }, timeout=30)

        if resp.status_code != 200:
            print(f"Error at batch {i}: HTTP {resp.status_code}")
            break

        data = resp.json()
        if not data:
            break

        all_data.extend(data)
        timestamps = [a["timestamp"] for a in data]
        min_ts = min(timestamps)
        date_str = time.strftime("%Y-%m-%d", time.gmtime(min_ts))
        print(f"Batch {i}: {len(data)} records, oldest={date_str}, total={len(all_data)}")

        if min_ts == end_ts or len(data) < batch_size:
            break

        end_ts = min_ts - 1
        time.sleep(0.3)

    # Deduplicate
    seen = set()
    unique = []
    for a in all_data:
        key = (a.get("transactionHash", ""), a.get("type", ""),
               a.get("conditionId", ""), a.get("outcomeIndex", ""))
        if key not in seen:
            seen.add(key)
            unique.append(a)

    print(f"\nTotal: {len(all_data)} raw, {len(unique)} unique")
    return unique


def analyze(data):
    """Compute P&L from activity records."""
    buy_usdc = sell_usdc = redeem_usdc = merge_usdc = 0
    buy_count = sell_count = 0

    for a in data:
        usdc = float(a.get("usdcSize", 0))
        t = a["type"]
        side = a.get("side", "")

        if t == "TRADE":
            if side == "BUY":
                buy_usdc += usdc
                buy_count += 1
            elif side == "SELL":
                sell_usdc += usdc
                sell_count += 1
        elif t == "REDEEM":
            redeem_usdc += usdc
        elif t == "MERGE":
            merge_usdc += usdc

    timestamps = [a["timestamp"] for a in data]
    min_ts, max_ts = min(timestamps), max(timestamps)
    days = max(1, (max_ts - min_ts) / 86400)

    types = defaultdict(int)
    for a in data:
        types[a["type"]] += 1

    total_inflows = sell_usdc + redeem_usdc + merge_usdc
    net_pnl = total_inflows - buy_usdc

    print(f"\n{'='*70}")
    print(f"RN1 ACTIVITY P&L ANALYSIS")
    print(f"{'='*70}")
    print(f"Period: {time.strftime('%Y-%m-%d', time.gmtime(min_ts))} to "
          f"{time.strftime('%Y-%m-%d', time.gmtime(max_ts))} ({days:.0f} days)")
    print(f"Records: {len(data):,} ({dict(types)})")
    print(f"")
    print(f"OUTFLOWS:")
    print(f"  BUY trades:   {buy_count:>8,} trades  ${buy_usdc:>14,.2f}")
    print(f"")
    print(f"INFLOWS:")
    print(f"  SELL trades:  {sell_count:>8,} trades  ${sell_usdc:>14,.2f}")
    print(f"  REDEEM:       {types['REDEEM']:>8,} events  ${redeem_usdc:>14,.2f}")
    print(f"  MERGE:        {types['MERGE']:>8,} events  ${merge_usdc:>14,.2f}")
    print(f"  Total:                          ${total_inflows:>14,.2f}")
    print(f"")
    print(f"NET P&L:                          ${net_pnl:>14,.2f}")
    print(f"Daily avg:                        ${net_pnl/days:>14,.2f}")
    print(f"Annualized:                       ${net_pnl/days*365:>14,.0f}")

    # Daily breakdown
    daily = defaultdict(lambda: {"buys": 0, "sells": 0, "redeems": 0, "merges": 0, "trades": 0})
    for a in data:
        day = time.strftime("%Y-%m-%d", time.gmtime(a["timestamp"]))
        usdc = float(a.get("usdcSize", 0))
        t = a["type"]
        side = a.get("side", "")
        if t == "TRADE":
            daily[day]["trades"] += 1
            if side == "BUY":
                daily[day]["buys"] += usdc
            else:
                daily[day]["sells"] += usdc
        elif t == "REDEEM":
            daily[day]["redeems"] += usdc
        elif t == "MERGE":
            daily[day]["merges"] += usdc

    print(f"\n{'='*70}")
    print(f"DAILY P&L")
    print(f"{'='*70}")
    print(f"{'Date':<12} {'Trades':>7} {'Buys':>12} {'Redeems':>12} {'Merges':>12} {'Net':>12} {'Cumul':>12}")
    cum = 0
    for day in sorted(daily.keys()):
        d = daily[day]
        net_day = d["sells"] + d["redeems"] + d["merges"] - d["buys"]
        cum += net_day
        print(f"{day:<12} {d['trades']:>7,} ${d['buys']:>10,.0f} ${d['redeems']:>10,.0f} "
              f"${d['merges']:>10,.0f} ${net_day:>10,.0f} ${cum:>10,.0f}")

    # Sport breakdown
    sports = defaultdict(lambda: {"buys": 0, "sells": 0, "redeems": 0, "merges": 0, "count": 0})
    for a in data:
        slug = a.get("slug", "")
        sport = slug.split("-")[0] if slug else "unknown"
        usdc = float(a.get("usdcSize", 0))
        t = a["type"]
        side = a.get("side", "")
        sports[sport]["count"] += 1
        if t == "TRADE" and side == "BUY":
            sports[sport]["buys"] += usdc
        elif t == "TRADE" and side == "SELL":
            sports[sport]["sells"] += usdc
        elif t == "REDEEM":
            sports[sport]["redeems"] += usdc
        elif t == "MERGE":
            sports[sport]["merges"] += usdc

    print(f"\n{'='*70}")
    print(f"P&L BY SPORT")
    print(f"{'='*70}")
    print(f"{'Sport':<10} {'Count':>7} {'Buys':>12} {'Redeems':>12} {'Merges':>12} {'Net':>12}")
    for sport in sorted(sports.keys(),
                        key=lambda s: -(sports[s]["sells"] + sports[s]["redeems"] + sports[s]["merges"] - sports[s]["buys"])):
        s = sports[sport]
        net_s = s["sells"] + s["redeems"] + s["merges"] - s["buys"]
        if abs(net_s) > 100:
            print(f"{sport:<10} {s['count']:>7,} ${s['buys']:>10,.0f} ${s['redeems']:>10,.0f} "
                  f"${s['merges']:>10,.0f} ${net_s:>10,.0f}")


def main():
    parser = argparse.ArgumentParser(description="RN1 activity P&L analysis")
    parser.add_argument("--download", action="store_true", help="Download fresh data")
    parser.add_argument("--max-batches", type=int, default=500, help="Max API batches")
    args = parser.parse_args()

    if args.download or not os.path.exists(DATA_FILE):
        print(f"Downloading activity data for {WALLET}...")
        data = download_activity(max_batches=args.max_batches)
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print(f"Saved {len(data)} records to {DATA_FILE}")
    else:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded {len(data)} records from {DATA_FILE}")

    analyze(data)


if __name__ == "__main__":
    main()
