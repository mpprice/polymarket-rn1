#!/usr/bin/env python3
"""Run the RN1-style Polymarket sports arbitrage strategy.

Usage:
    # Paper trade: scan, find edges, log what would be traded
    python run_strategy.py

    # Continuous paper trading (scan every 5 min)
    python run_strategy.py --loop --interval 300

    # Live trading (CAUTION: real money on Polygon)
    python run_strategy.py --loop --live --interval 300

    # Custom edge threshold and price cap
    python run_strategy.py --min-edge 5 --max-price 0.30

    # Show portfolio report
    python run_strategy.py --report
"""
import argparse
import logging
import sys

from src.config import Config
from src.strategy import Strategy
from src.position_tracker import PositionTracker


def main():
    parser = argparse.ArgumentParser(description="RN1-style Polymarket sports arbitrage")
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Scan interval seconds")
    parser.add_argument("--min-edge", type=float, default=None, help="Min edge %% to trade")
    parser.add_argument("--max-price", type=float, default=None, help="Max entry price (0-1)")
    parser.add_argument("--max-edge", type=float, default=None, help="Max edge %% (cap)")
    parser.add_argument("--report", action="store_true", help="Show portfolio report")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)-20s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("strategy.log", mode="a"),
        ],
    )

    config = Config()

    # Portfolio report only
    if args.report:
        tracker = PositionTracker(config)
        tracker.print_report()
        return

    # Validate API keys
    if not config.odds_api_key:
        logging.error("ODDS_API_KEY not set in .env")
        sys.exit(1)

    dry_run = not args.live
    strategy = Strategy(config, dry_run=dry_run)

    # Override parameters
    if args.min_edge is not None:
        strategy.min_edge_pct = args.min_edge
    if args.max_price is not None:
        strategy.max_entry_price = args.max_price
    if args.max_edge is not None:
        strategy.max_edge_pct = args.max_edge

    if dry_run:
        logging.info("*** PAPER TRADE MODE ***")
    else:
        logging.warning("*** LIVE TRADING MODE - REAL MONEY ***")
        if not config.private_key:
            logging.error("POLYMARKET_PRIVATE_KEY not set in .env")
            sys.exit(1)

    if args.loop:
        strategy.run_loop(interval=args.interval)
    else:
        opps = strategy.scan()
        if not opps:
            print(f"\nNo opportunities above {strategy.min_edge_pct:.1f}% edge "
                  f"and below {strategy.max_entry_price*100:.0f}c entry price.")
        else:
            print(f"\n{'='*80}")
            print(f"OPPORTUNITIES: {len(opps)} (sorted by edge)")
            print(f"{'='*80}")
            for i, o in enumerate(opps):
                line_str = f" ({o.line})" if o.line else ""
                print(f"\n#{i+1}: {o.question}")
                print(f"   {o.market_type.upper()}{line_str} | {o.outcome} | {o.sport}")
                print(f"   Poly: {o.poly_price:.3f}  Fair: {o.fair_prob:.3f}  "
                      f"Edge: +{o.edge_pct:.1f}%  ({o.bookmaker})")
                print(f"   Size: ${o.size_usdc:.0f} ({o.size_usdc/o.poly_price:.0f} shares)")

            # Summary by market type
            h2h = [o for o in opps if o.market_type == "h2h"]
            spread = [o for o in opps if o.market_type == "spread"]
            total = [o for o in opps if o.market_type == "total"]
            print(f"\n{'='*80}")
            print(f"Summary: {len(h2h)} h2h | {len(spread)} spread | {len(total)} total")
            total_size = sum(o.size_usdc for o in opps)
            avg_edge = sum(o.edge_pct for o in opps) / len(opps) if opps else 0
            print(f"Total size: ${total_size:,.0f} | Avg edge: +{avg_edge:.1f}%")


if __name__ == "__main__":
    main()
