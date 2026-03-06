#!/usr/bin/env python3
"""Standalone RN1 Pattern Analysis Script.

Loads data/rn1_full_activity.json, computes comprehensive patterns,
prints a report, and saves to data/rn1_patterns_summary.json.

Usage:
    python analysis/rn1_patterns.py
    python analysis/rn1_patterns.py --force   # Force recompute (ignore cache)
"""
import argparse
import json
import logging
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.rn1_analyzer import RN1Analyzer, CACHE_FILE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def print_separator(title: str):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


def print_table(headers: list[str], rows: list[list], col_widths: list[int] = None):
    """Print a simple ASCII table."""
    if not col_widths:
        col_widths = [max(len(str(h)), max((len(str(r[i])) for r in rows), default=4)) + 2
                      for i, h in enumerate(headers)]

    header_line = "".join(str(h).ljust(w) for h, w in zip(headers, col_widths))
    print(header_line)
    print("-" * sum(col_widths))
    for row in rows:
        print("".join(str(c).ljust(w) for c, w in zip(row, col_widths)))


def run_analysis(force: bool = False):
    """Run full RN1 pattern analysis and print report."""
    print_separator("RN1 Trading Pattern Analysis")
    print(f"  Cache file: {CACHE_FILE}")
    print(f"  Force recompute: {force}")

    analyzer = RN1Analyzer(force_reload=force)
    patterns = analyzer.patterns

    if "error" in patterns:
        print(f"\nERROR: {patterns['error']}")
        return

    # --- Record Counts ---
    print_separator("Record Counts")
    rc = patterns.get("record_counts", {})
    total = sum(rc.values())
    print(f"  Total records:  {total:,}")
    print(f"  Buys (TRADE):   {rc.get('buys', 0):,}")
    print(f"  Sells (TRADE):  {rc.get('sells', 0):,}")
    print(f"  Merges:         {rc.get('merges', 0):,}")
    print(f"  Redeems:        {rc.get('redeems', 0):,}")

    # --- Entry Price Distribution ---
    print_separator("Entry Price Distribution (Buy Trades)")
    epd = analyzer.entry_price_distribution()
    if epd:
        rows = []
        for bucket, v in epd.items():
            rows.append([bucket, f"{v['count']:,}", f"${v['total_usdc']:,.0f}"])
        print_table(["Price Bucket", "Count", "Total USDC"], rows, [18, 12, 15])

    # --- Position Sizing ---
    print_separator("Position Sizing")
    ps = analyzer.position_sizing_patterns()
    overall = ps.get("overall", {})
    print(f"  Mean trade size:   ${overall.get('mean', 0):.2f}")
    print(f"  Median trade size: ${overall.get('median', 0):.2f}")
    print(f"  P25:               ${overall.get('p25', 0):.2f}")
    print(f"  P75:               ${overall.get('p75', 0):.2f}")
    print(f"  Total USDC:        ${overall.get('total', 0):,.0f}")

    print("\n  By Market Type:")
    by_mt = ps.get("by_market_type", {})
    if by_mt:
        rows = [[mt, f"${v.get('mean', 0):.2f}", f"${v.get('median', 0):.2f}",
                 f"{v.get('count', 0):,}", f"${v.get('total', 0):,.0f}"]
                for mt, v in by_mt.items()]
        print_table(["Type", "Mean", "Median", "Count", "Total"], rows,
                    [15, 10, 10, 10, 15])

    # --- Holding Periods ---
    print_separator("Holding Periods")
    hp = analyzer.holding_period_analysis()
    print(f"  Slugs with exits:  {hp.get('count', 0):,}")
    print(f"  Mean hold:         {hp.get('mean_hours', 0):.1f} hours")
    print(f"  Median hold:       {hp.get('median_hours', 0):.1f} hours")
    print(f"  P10:               {hp.get('p10_hours', 0):.1f} hours")
    print(f"  P90:               {hp.get('p90_hours', 0):.1f} hours")
    buckets = hp.get("buckets", {})
    if buckets:
        print("\n  Distribution:")
        for b, c in buckets.items():
            bar = "#" * min(50, c // max(1, max(buckets.values()) // 50))
            print(f"    {b:>8s}: {c:>6,}  {bar}")

    # --- Sport Preferences ---
    print_separator("Top Sports by Estimated Profit")
    top_sports = analyzer.top_sports_by_profit()
    if top_sports:
        rows = [
            [s["sport"], f"{s.get('buy_count', 0):,}", f"${s.get('buy_usdc', 0):,.0f}",
             f"{s.get('merge_count', 0):,}", f"${s.get('estimated_profit', 0):,.0f}"]
            for s in top_sports[:20]
        ]
        print_table(["Sport", "Buys", "Buy USDC", "Merges", "Est Profit"], rows,
                    [35, 10, 15, 10, 15])

    # --- Merge Patterns ---
    print_separator("Merge Patterns")
    mp = analyzer.merge_patterns()
    print(f"  Total merges:       {mp.get('count', 0):,}")
    print(f"  Total USDC merged:  ${mp.get('total_usdc', 0):,.0f}")
    print(f"  Avg merge size:     ${mp.get('avg_size', 0):.2f}")
    print(f"  Median merge size:  ${mp.get('median_size', 0):.2f}")
    print(f"  Unique slugs:       {mp.get('unique_slugs', 0):,}")

    profitable = mp.get("profitable_merges", [])
    if profitable:
        print(f"\n  Top Profitable Merges (by profit):")
        rows = [
            [p["slug"][:45], f"${p['merge_usdc']:,.0f}", f"${p['buy_cost']:,.0f}",
             f"${p['profit']:,.0f}", f"{p['profit_pct']:.1f}%"]
            for p in profitable[:15]
        ]
        print_table(["Slug", "Merge USDC", "Buy Cost", "Profit", "ROI%"], rows,
                    [48, 14, 14, 12, 10])

    # --- Time of Day ---
    print_separator("Trading Time of Day (UTC)")
    tod = analyzer.time_of_day_patterns()
    print(f"  Peak hour: {tod.get('peak_hour_utc', '?')}:00 UTC")
    print(f"  Peak day:  {tod.get('peak_day', '?')}")

    by_hour = tod.get("by_hour_utc", {})
    if by_hour:
        max_count = max(v["count"] for v in by_hour.values())
        print("\n  Hourly activity:")
        for h in sorted(by_hour.keys(), key=int):
            v = by_hour[h]
            bar_len = int(v["count"] / max(1, max_count) * 40)
            bar = "#" * bar_len
            print(f"    {h:>2s}:00  {v['count']:>7,}  ${v['usdc']:>12,.0f}  {bar}")

    # --- Market Types ---
    print_separator("Market Type Preferences")
    mt = analyzer.market_type_preferences()
    if mt:
        rows = [[k, f"{v.get('count', 0):,}", f"${v.get('usdc', 0):,.0f}",
                 f"{v.get('pct_of_trades', 0):.1f}%", f"{v.get('avg_price', 0):.4f}"]
                for k, v in mt.items()]
        print_table(["Type", "Count", "USDC", "% Trades", "Avg Price"], rows,
                    [12, 12, 15, 12, 12])

    # --- Consecutive Trades ---
    print_separator("Scaling / Consecutive Trades")
    ct = analyzer.consecutive_trade_patterns()
    print(f"  Unique slugs:         {ct.get('unique_slugs', 0):,}")
    print(f"  Avg buys per slug:    {ct.get('avg_buys_per_slug', 0):.1f}")
    print(f"  Median buys per slug: {ct.get('median_buys_per_slug', 0)}")
    print(f"  Max buys per slug:    {ct.get('max_buys_per_slug', 0)}")
    dist = ct.get("distribution", {})
    if dist:
        print("\n  Distribution:")
        for b, c in dist.items():
            bar = "#" * min(50, c // max(1, max(dist.values()) // 50))
            print(f"    {b:>8s}: {c:>6,}  {bar}")

    # --- Top Markets by Volume ---
    print_separator("Top Markets by USDC Volume")
    top_mkts = analyzer.top_markets_by_volume()
    if top_mkts:
        rows = [
            [m["slug"][:50], f"${m.get('total_usdc', 0):,.0f}",
             f"{m.get('buy_count', 0):,}", f"${m.get('pnl', 0):,.0f}",
             f"{m.get('roi_pct', 0):.1f}%"]
            for m in top_mkts[:20]
        ]
        print_table(["Slug", "Total USDC", "Buys", "PnL", "ROI%"], rows,
                    [52, 14, 8, 12, 10])

    # --- Save Summary ---
    print_separator("Cache")
    print(f"  Patterns saved to: {CACHE_FILE}")
    print(f"  Computed at: {patterns.get('computed_at', 'unknown')}")
    print(f"  Cache size: {CACHE_FILE.stat().st_size / 1024 / 1024:.1f} MB" if CACHE_FILE.exists() else "  (not cached)")

    print(f"\n{'='*70}")
    print("  Analysis complete.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RN1 Pattern Analysis")
    parser.add_argument("--force", action="store_true",
                        help="Force recompute from raw data (ignore cache)")
    args = parser.parse_args()

    run_analysis(force=args.force)
