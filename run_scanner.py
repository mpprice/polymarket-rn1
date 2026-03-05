#!/usr/bin/env python3
"""Run the Polymarket sports odds scanner.

Usage:
    # Dry run (no real orders, just scan and log opportunities)
    python run_scanner.py

    # Paper trade mode (scan continuously, log what would be traded)
    python run_scanner.py --loop --interval 300

    # Live trading (CAUTION: real money)
    python run_scanner.py --loop --live --interval 300

    # Single scan, show all matches
    python run_scanner.py --min-edge 0
"""
import argparse
import logging
import sys

from src.config import Config
from src.scanner import Scanner


def main():
    parser = argparse.ArgumentParser(description="Polymarket sports odds scanner")
    parser.add_argument("--live", action="store_true", help="Enable live trading (default: dry run)")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Scan interval in seconds (default: 300)")
    parser.add_argument("--min-edge", type=float, default=None, help="Override minimum edge %%")
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-5s %(name)-20s %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("scanner.log", mode="a"),
        ],
    )

    config = Config()
    if args.min_edge is not None:
        config.min_edge_pct = args.min_edge

    # Validate required keys
    if not config.odds_api_key:
        logging.error("ODDS_API_KEY not set in .env - get one at https://the-odds-api.com")
        sys.exit(1)

    dry_run = not args.live
    scanner = Scanner(config, dry_run=dry_run)

    if dry_run:
        logging.info("*** DRY RUN MODE - no real orders will be placed ***")
    else:
        logging.warning("*** LIVE TRADING MODE - real money at risk ***")
        if not config.private_key:
            logging.error("POLYMARKET_PRIVATE_KEY not set in .env")
            sys.exit(1)

    if args.loop:
        scanner.run_loop(interval_seconds=args.interval)
    else:
        opportunities = scanner.run_once()
        if not opportunities:
            print("\nNo opportunities found above %.1f%% edge threshold." % config.min_edge_pct)
        else:
            print(f"\n{'='*80}")
            print(f"OPPORTUNITIES FOUND: {len(opportunities)}")
            print(f"{'='*80}")
            for i, opp in enumerate(opportunities):
                mtype = opp.get('market_type', 'h2h')
                line = opp.get('line')
                line_str = f" ({line})" if line else ""
                print(f"\n#{i+1}: {opp['question']}")
                print(f"   Type: {mtype}{line_str}  |  Outcome: {opp['outcome']}")
                print(f"   Polymarket price: {opp['poly_price']:.3f}")
                print(f"   Fair probability:  {opp['fair_prob']:.3f} ({opp['bookmaker']})")
                print(f"   Edge: +{opp['edge_pct']:.1f}%")
                print(f"   Suggested size: ${opp['suggested_size_usdc']:.0f}")


if __name__ == "__main__":
    main()
