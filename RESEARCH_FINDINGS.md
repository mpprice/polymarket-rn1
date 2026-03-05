# Polymarket Sports Arbitrage Research Findings

## RN1 Deep Dive (2 Research Agents)

### Identity
- Wallet: `0x2005D16a84CEEfa912D4e380cD32E7ff827875Ea`
- Handle: @RN1 on Polymarket
- NOT the same as "Theo" (French election whale who made $85M on Trump)
- Started July 2025 with $1,000 deposit

### P&L Timeline (Verified via /activity API, 1.1M records)
| Period | Monthly Net P&L | Cumulative | Trades |
|--------|----------------|-----------|--------|
| Jul 2025 (start) | +$12K | $12K | 4K |
| Aug 2025 | +$126K | $138K | 16K |
| Sep 2025 | +$585K | $723K | 23K |
| Oct 2025 | +$1.86M | $2.59M | 61K |
| Nov 2025 | +$3.31M | $5.89M | 117K |
| Dec 2025 | +$4.27M | $10.16M | 168K |
| Jan 2026 (peak) | +$5.61M | $15.77M | 365K |
| Feb 2026 | +$4.12M | $19.89M | 274K |
| Mar 2026 (5d) | +$460K | $20.35M | 25K |
| **TOTAL** | **+$20.35M** | | **1.05M trades** |
| Total volume (buys) | $93.1M | | |
| Daily avg P&L | $85K/day | | |

### True P&L: +$20.35M (Verified On-Chain)
- PANews previously claimed -$920K loss -- THIS WAS COMPLETELY WRONG
- **Root cause of PANews error**: /positions API only shows positions with size > 0
  - Winning positions: redeemed for $1, size drops to 0, DISAPPEARS from API
  - MERGE events (synthetic sells): YES+NO combined for $1, DISAPPEARS from API
  - Result: /positions data shows ALL losses but NONE of the profits
- **Verified via /activity API (full history, 1.1M records, Jul 2025 - Mar 2026):**
  - BUY trades (outflow): $93.1M
  - REDEEM inflows: $72.3M (winning positions at $1/share)
  - MERGE inflows: $40.4M (YES+NO pairs combined at $1/pair)
  - SELL inflows: $692K (RN1 almost never sells directly)
  - **Total NET P&L: +$20.35M**
  - Daily avg: +$85K/day
- MERGE is the dominant profit mechanism ($40.4M, 35% of total inflows)
- Exponential scale-up: 4K trades in Jul 2025 to 365K in Jan 2026
- Jan 2026 was peak month: +$5.61M net
- Source: Polymarket /activity API, paginated backward via `end` timestamp parameter

### RN1's Actual Strategy (3 Components)

**A. Live Sports Latency Arbitrage (Core)**
- RN1 is ~45 seconds faster than typical Polymarket traders on live markets
- Bot monitors live match stats from fast data feeds, places orders 3-5s after events
- Polymarket prices take seconds-to-minutes to adjust after goals/touchdowns
- Buys correct outcome at stale (cheap) prices before crowd reacts

**B. Intra-Market Mispricing**
- Exploits when YES + NO contracts don't sum to $1.00
- Example: Man Utd "doesn't win" priced at $0.21 but fair = $0.28 (33% edge)
- Uses combined hedging across win/loss, spread, O/U markets

**C. Synthetic Sells + Trash Farming**
- NEVER sells directly (avoids taker fees)
- Buys opposite side = "synthetic sell" maintaining delta-neutral
- Buys $0.01-$0.03 contracts near expiry for volume-based rewards

### RN1 Scale and Competition
- $93M total buy volume over 8 months, 1.05M trades
- Peak: 365K trades in Jan 2026 (~12K/day)
- MERGE strategy (synthetic sells) = $40.4M (43% of total buy volume returned via merge)
- Arb edges have compressed as more bots entered (2.7s avg window, down from 12.3s)
- Despite compression, still generating +$85K/day average across full history
- Feb/Mar 2026 daily rate: ~$145K/day (accelerating)

## What DOES Work: Bot Arbitrage ($40M in Profits)

### Industry Data
- $40M in arb profits on Polymarket between Apr 2024 - Apr 2025
- Top 3 wallets: $4.2M profit from 10,200+ bets
- One crypto bot: $313 → $414K in one month (BTC/ETH/SOL 15-min markets, 98% win rate)
- Only 0.51% of arb traders earn > $1,000 (execution is everything)
- 73% of arb profits captured by sub-100ms bots

### Gambot Reference Implementation
- [gambot.dev](https://www.gambot.dev/) - Solana-powered AI arb platform
- Pulls Pinnacle odds via RapidAPI, removes vig to calculate true probabilities
- Trades when Polymarket deviates by 3-8% EV
- Hosted on co-located servers near Polygon nodes

### Polyburg Mean-Reversion Bot (Alternative)
- Pre-splits USDC into both tokens before game starts
- Sells whichever side spikes after scoring events
- Claimed 73% win rate, 11% ROI, 0.55 Sharpe on NBA

## Polymarket Technical Details

### CLOB Mechanics
- Hybrid-decentralized: off-chain matching, on-chain settlement (Polygon)
- Binary: YES + NO = $1.00 (unified order book)
- BUY YES at X = SELL NO at (1-X), creating deeper liquidity
- negativeRisk markets: multi-outcome (spreads, props)
- **3-second delay on taker orders in sports markets** (anti-courtsiding)
- **Maker orders have NO delay** — RN1 uses maker orders

### Rate Limits
- Public API: 100 req/min
- Order placement: 60 orders/min
- Batch endpoint: 3,000 orders / 10-min rolling window
- WebSocket: no explicit limit, ~100ms updates
- REST: ~200-500ms round trip

### Fee Structure
- No explicit trading fees (currently)
- Polygon gas: ~$0.01 per trade
- Taker vs Maker: taker orders have the 3-second delay on sports

## Strategy Recommendations for Our Bot

### Primary: Pinnacle-vs-Polymarket Pre-Game Arb
1. Fetch Pinnacle odds (The Odds API) + Polymarket CLOB prices
2. Remove Pinnacle overround to get fair probabilities
3. When Polymarket misprices by >3% EV, place **maker limit orders**
4. Target 5-40c price range (highest mispricing)
5. Hold to resolution (not scalping)

### Secondary: Live Sports Latency Arb (Higher Edge, Higher Complexity)
1. Requires real-time sports data feed (not video streams)
2. Monitor scoring events, compare instant fair-value shift to Polymarket stale price
3. Place maker orders within seconds of events
4. Mean-reversion after scoring spikes

### Parameters (Calibrated from Research)
- Edge threshold: 3-8% EV (Gambot range)
- Entry price: 5-40c (longshot/underdog sweet spot)
- Sizing: 5% Kelly fraction max (avoid RN1's scaling mistake)
- Max position: 5% of capital
- Max exposure: 60% of capital
- **Use maker orders only** (avoid 3-second delay)
- Sports: EPL, Bundesliga, La Liga, NBA, CS2

## Sources

### RN1 Specific
- [RN1 Polymarket Profile](https://polymarket.com/@RN1)
- [RN1 Analytics](https://polymarketanalytics.com/traders/0x2005d16a84ceefa912d4e380cd32e7ff827875ea)
- [Phemex: RN1 $1K to $2M](https://phemex.com/news/article/smart-money-rn1-nets-2m-on-polymarket-from-1k-investment-49297)
- [PANews: 27K whale trade analysis](https://www.panewslab.com/en/articles/516262de-6012-4302-bb20-b8805f03f35f)
- [Leshka: RN1 $1K to $3.7M](https://x.com/leshka_eth/status/2014030274649326025)
- [Justin Wu: RN1 microstructure arb](https://x.com/hackapreneur/status/2004552276674003141)
- [InvestX: RN1 strategy breakdown](https://investx.fr/en/crypto-news/polymarket-trader-turns-1000-into-2-million-unveiling-winning-strategy/)

### Arbitrage Ecosystem
- [Gambot Dashboard](https://www.gambot.dev/)
- [Yahoo Finance: Arb Bots Dominate](https://finance.yahoo.com/news/arbitrage-bots-dominate-polymarket-millions-100000888.html)
- [DL News: Bot-like bettors](https://www.dlnews.com/articles/markets/polymarket-users-lost-millions-of-dollars-to-bot-like-bettors-over-the-past-year/)
- [QuantVPS: Cross-Market Arbitrage](https://www.quantvps.com/blog/cross-market-arbitrage-polymarket)
- [QuantVPS: Sports Betting Bots](https://www.quantvps.com/blog/automated-sports-betting-bots-on-polymarket)
- [Polyburg Mean-Reversion Bot](https://x.com/polyburg/status/2026877934179594524)

### Technical
- [Polymarket CLOB Docs](https://docs.polymarket.com/developers/CLOB/introduction)
- [py-clob-client GitHub](https://github.com/Polymarket/py-clob-client)
- [IMDEA Arbitrage Study](https://arxiv.org/abs/2508.03474)
- [OddsPapi: Polymarket/Kalshi API](https://oddspapi.io/blog/polymarket-api-kalshi-api-vs-sportsbooks-the-developers-guide/)
