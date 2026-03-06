#!/usr/bin/env python3
"""Run the Polymarket sports arbitrage bot.

Integrates:
- Directional arb: Pinnacle-vs-Polymarket edge detection
- Merge arb: YES+NO mispricing (RN1's primary profit mechanism)
- Learning agent: continuously improves from trade outcomes

Usage:
    # Paper trade (scan, log opportunities, don't place real orders)
    python run_bot.py

    # Continuous paper trading
    python run_bot.py --loop

    # Live trading with $500 test wallet (CAUTION: real money)
    python run_bot.py --loop --live

    # Quick scan with custom parameters
    python run_bot.py --min-edge 5 --max-price 0.30

    # Show portfolio and learning report
    python run_bot.py --report

    # Show learning agent metrics
    python run_bot.py --learning-report

    # Check wallet and API credentials
    python run_bot.py --check
"""
import argparse
import logging
import sys

from src.config import Config
from src.strategy import Strategy
from src.position_tracker import PositionTracker


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s %(name)-20s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler("bot.log", mode="a"),
        ],
    )


def cmd_check(config: Config):
    """Check credentials and connectivity."""
    import requests

    print("\n=== CREDENTIAL & CONNECTIVITY CHECK ===\n")

    # Private key
    pk = config.private_key
    print(f"Private Key:     {'SET' if pk else 'MISSING'}")
    if pk:
        try:
            from eth_account import Account
            account = Account.from_key(pk)
            print(f"Wallet Address:  {account.address}")
        except Exception as e:
            print(f"Private key error: {e}")

    # API key
    print(f"API Key:         {'SET' if config.api_key else 'MISSING (will derive on connect)'}")

    # Odds API
    print(f"Odds API Key:    {'SET' if config.odds_api_key else 'MISSING'}")
    if config.odds_api_key:
        try:
            resp = requests.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": config.odds_api_key},
                timeout=10,
            )
            remaining = resp.headers.get("x-requests-remaining", "?")
            used = resp.headers.get("x-requests-used", "?")
            print(f"Odds API:        {'OK' if resp.ok else 'FAILED'} "
                  f"(used: {used}, remaining: {remaining})")
        except Exception as e:
            print(f"Odds API:        FAILED - {e}")

    # Polymarket connectivity
    try:
        resp = requests.get(f"{config.gamma_url}/sports", timeout=10)
        sports = resp.json() if resp.ok else []
        print(f"Gamma API:       {'OK' if resp.ok else 'FAILED'} ({len(sports)} sports)")
    except Exception as e:
        print(f"Gamma API:       FAILED - {e}")

    # Risk config
    print(f"\n=== RISK CONFIGURATION ===\n")
    print(f"Bankroll:        ${config.bankroll_usdc:,.0f}")
    print(f"Max position:    ${config.max_position_usdc:,.0f} ({config.max_position_usdc/config.bankroll_usdc*100:.0f}% of bankroll)")
    print(f"Max exposure:    ${config.max_total_exposure_usdc:,.0f} ({config.max_total_exposure_usdc/config.bankroll_usdc*100:.0f}% of bankroll)")
    print(f"Min edge:        {config.min_edge_pct:.1f}%")
    print(f"Kelly fraction:  {config.kelly_fraction}")
    print(f"Price range:     {config.min_entry_price:.0%} - {config.max_entry_price:.0%}")
    print(f"Merge enabled:   {config.merge_enabled}")
    print(f"Learning:        {config.learning_enabled}")
    print(f"Sports:          {len(config.target_sports)} configured")


def cmd_report(config: Config):
    """Show portfolio and learning report."""
    tracker = PositionTracker(config)
    tracker.print_report()

    if config.learning_enabled:
        try:
            from src.learning_agent import LearningAgent
            agent = LearningAgent()
            if agent.history:
                print()
                agent.print_report()
            else:
                print("\nNo learning history yet.")
        except Exception:
            pass


def cmd_learning_report(config: Config):
    """Show detailed learning agent report."""
    try:
        from src.learning_agent import LearningAgent
        agent = LearningAgent()
        if agent.history:
            agent.print_report()
        else:
            print("No learning history yet. Run the bot to accumulate trade data.")
    except Exception as e:
        print(f"Learning agent error: {e}")


def cmd_scan(config: Config, args):
    """Run a single scan."""
    if not config.odds_api_key:
        logging.error("ODDS_API_KEY not set in .env")
        sys.exit(1)

    strategy = Strategy(config, dry_run=True)

    if args.min_edge is not None:
        strategy.min_edge_pct = args.min_edge
    if args.max_price is not None:
        strategy.max_entry_price = args.max_price
    if args.max_edge is not None:
        strategy.max_edge_pct = args.max_edge

    # Directional opportunities
    opps = strategy.scan()

    if not opps:
        print(f"\nNo directional opportunities above {strategy.min_edge_pct:.1f}% edge "
              f"and below {strategy.max_entry_price*100:.0f}c entry price.")
    else:
        print(f"\n{'='*80}")
        print(f"DIRECTIONAL OPPORTUNITIES: {len(opps)} (sorted by edge)")
        print(f"{'='*80}")
        for i, o in enumerate(opps):
            line_str = f" ({o.line})" if o.line else ""
            adj_str = f" [adj:{o.adjusted_edge:.1f}%]" if o.adjusted_edge else ""
            print(f"\n#{i+1}: {o.question}")
            print(f"   {o.market_type.upper()}{line_str} | {o.outcome} | {o.sport}")
            print(f"   Poly: {o.poly_price:.3f}  Fair: {o.fair_prob:.3f}  "
                  f"Edge: +{o.edge_pct:.1f}%{adj_str}  ({o.bookmaker})")
            print(f"   Size: ${o.size_usdc:.0f} ({o.size_usdc/o.poly_price:.0f} shares)")

        # Summary
        h2h = [o for o in opps if o.market_type == "h2h"]
        spread = [o for o in opps if o.market_type == "spread"]
        total = [o for o in opps if o.market_type == "total"]
        total_size = sum(o.size_usdc for o in opps)
        avg_edge = sum(o.edge_pct for o in opps) / len(opps)
        print(f"\n{'='*80}")
        print(f"Summary: {len(h2h)} h2h | {len(spread)} spread | {len(total)} total")
        print(f"Total size: ${total_size:,.0f} | Avg edge: +{avg_edge:.1f}%")

    # Merge opportunities
    merge_opps = strategy.scan_merges()
    if merge_opps:
        print(f"\n{'='*80}")
        print(f"MERGE OPPORTUNITIES: {len(merge_opps)}")
        print(f"{'='*80}")
        for i, m in enumerate(merge_opps[:10]):
            print(f"  #{i+1}: {m.slug} | YES={m.yes_price:.3f} NO={m.no_price:.3f} "
                  f"| cost={m.total_cost:.4f} | profit=${m.profit_per_pair:.4f}/pair "
                  f"| edge={m.edge_pct:.2f}%")


def cmd_loop(config: Config, args):
    """Run continuous trading loop."""
    if not config.odds_api_key:
        logging.error("ODDS_API_KEY not set in .env")
        sys.exit(1)

    dry_run = not args.live
    strategy = Strategy(config, dry_run=dry_run)

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

    interval = args.interval or config.scan_interval_seconds
    strategy.run_loop(interval=interval)


def main():
    parser = argparse.ArgumentParser(
        description="Polymarket sports arbitrage bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run_bot.py                    # Single scan (paper trade)
  python run_bot.py --loop             # Continuous paper trading
  python run_bot.py --loop --live      # LIVE trading (real money!)
  python run_bot.py --check            # Verify credentials
  python run_bot.py --report           # Portfolio report
  python run_bot.py --learning-report  # Learning agent metrics
""",
    )
    parser.add_argument("--live", action="store_true", help="Enable live trading")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=None, help="Scan interval seconds")
    parser.add_argument("--min-edge", type=float, default=None, help="Min edge %% to trade")
    parser.add_argument("--max-price", type=float, default=None, help="Max entry price (0-1)")
    parser.add_argument("--max-edge", type=float, default=None, help="Max edge %% (cap)")
    parser.add_argument("--report", action="store_true", help="Show portfolio report")
    parser.add_argument("--learning-report", action="store_true", help="Show learning report")
    parser.add_argument("--check", action="store_true", help="Check credentials")
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose logging")
    args = parser.parse_args()

    setup_logging(verbose=args.verbose)
    config = Config()

    if args.check:
        cmd_check(config)
    elif args.report:
        cmd_report(config)
    elif args.learning_report:
        cmd_learning_report(config)
    elif args.loop:
        cmd_loop(config, args)
    else:
        cmd_scan(config, args)


if __name__ == "__main__":
    main()
