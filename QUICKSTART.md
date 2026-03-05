# Polymarket Sports Arbitrage Bot - Quick Start

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Configure credentials (copy and edit)
cp .env.example .env
# Edit .env with your keys
```

## Required Credentials

| Key | Source | Notes |
|-----|--------|-------|
| `POLYMARKET_PRIVATE_KEY` | Generate via `python setup_wallet.py --generate` | Fund with 500 USDC on Polygon |
| `POLYMARKET_API_KEY/SECRET/PASSPHRASE` | Auto-derived on first connect, or `python setup_wallet.py --key 0x...` | |
| `ODDS_API_KEY` | [the-odds-api.com](https://the-odds-api.com) | Free tier: 500 requests/month |

## Commands

```bash
# Check credentials and connectivity
python run_bot.py --check

# Single scan (paper trade) - finds opportunities without placing orders
python run_bot.py

# Continuous paper trading (scan every 5 min)
python run_bot.py --loop

# Live trading with real money (CAUTION!)
python run_bot.py --loop --live

# Custom parameters
python run_bot.py --min-edge 5 --max-price 0.30

# Portfolio report
python run_bot.py --report

# Learning agent metrics
python run_bot.py --learning-report

# Verbose logging
python run_bot.py -v
```

## Architecture

```
run_bot.py              Main entry point
src/
  config.py             Configuration from .env
  strategy.py           Core strategy (directional arb + merge + learning)
  polymarket_client.py  Polymarket CLOB API wrapper
  odds_client.py        The Odds API client (Pinnacle/Betfair odds)
  matcher.py            Match Polymarket markets to bookmaker odds
  risk_manager.py       Kelly sizing + exposure limits
  position_tracker.py   CSV-based position persistence
  learning_agent.py     Adaptive learning from trade outcomes
  merge_strategy.py     YES+NO merge arbitrage (RN1's primary profit mechanism)
  scanner.py            Legacy scanner (use run_bot.py instead)
```

## How It Works

### Directional Arbitrage
1. Fetch Polymarket sports markets via Gamma API
2. Fetch Pinnacle/Betfair odds via The Odds API
3. Match markets by team names (130+ team database)
4. Remove bookmaker overround to get fair probabilities
5. When Polymarket price < fair prob by >3% EV, buy the underpriced side
6. Use MAKER limit orders (no 3-second delay on sports)
7. Hold to resolution

### Merge Arbitrage (RN1's $40M mechanism)
1. For each binary market, check YES_ask + NO_ask
2. If total < $1.00 (minus gas), it's a risk-free merge opportunity
3. Buy both YES and NO tokens
4. Merge into $1.00 USDC

### Learning Agent
- Tracks every trade outcome (win/loss, P&L)
- Computes calibration stats by sport, market type, price bucket, edge bucket
- Adjusts future edge estimates based on actual vs predicted win rates
- Identifies most profitable sports/market types for allocation

## Risk Parameters ($500 Bankroll)

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Max position | $25 | 5% of bankroll |
| Max exposure | $300 | 60% of bankroll |
| Kelly fraction | 0.25 | Quarter Kelly (conservative) |
| Min edge | 3.0% | Gambot-calibrated threshold |
| Price range | 3-50c | RN1's profitable range |
| Min merge profit | $0.02/pair | Covers Polygon gas |

## Data Files

```
data/
  my_positions.csv      Open/resolved positions
  my_trades.csv         Trade audit log
  learning_history.json Learning agent state (persists across restarts)
  rn1_full_activity.json  1.1M RN1 activity records for research
```

## Based on RN1 Research

RN1 made +$20.35M over 8 months trading Polymarket sports markets:
- $93M total volume, 1.05M trades
- MERGE was the primary profit mechanism ($40.4M)
- Profitable across nearly every sport
- See `RESEARCH_FINDINGS.md` for full analysis
