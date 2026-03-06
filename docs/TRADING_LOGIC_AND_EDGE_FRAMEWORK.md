# Everest Agentic AI Trader: Trading Logic & Mathematical Edge Framework

**EverestQuant Research Paper | March 2026**
**Classification: Internal**

---

## 1. Executive Summary

The Everest Agentic AI Trader exploits structural inefficiencies between Polymarket's prediction market CLOB and sharp bookmaker lines (primarily Pinnacle). The system identifies mispriced outcomes, sizes positions using fractional Kelly criterion, and learns from resolved trades to continuously improve edge estimation.

This paper documents the mathematical foundation for why a persistent edge exists, how it is captured, and defines the key test metrics against which live agent performance should be measured.

**Core thesis:** Polymarket sports markets are systematically less efficient than Pinnacle because (1) they attract recreational flow, (2) they lack professional market-making infrastructure, and (3) structural features like the 3-second TAKER delay and neg-risk collateral requirements create friction that prevents instantaneous arbitrage.

---

## 2. Why the Edge Exists

### 2.1 Market Structure Asymmetry

Polymarket operates as a binary options CLOB on Polygon. Each outcome trades as a token priced 0-$1.00, with YES + NO = $1.00 by CTF contract. Key structural features creating exploitable inefficiency:

| Feature | Polymarket | Pinnacle |
|---------|-----------|----------|
| Participant base | Retail-heavy, crypto-native | Professional bettors, syndicates |
| Market-making | Fragmented, often manual | Algorithmic, sub-second |
| Vig/spread | 1-5% per side | 1.5-3% total overround |
| Settlement | On-chain, T+resolution | Instant, oracle-based |
| TAKER delay | 3 seconds (sports) | None |
| Liquidity | $10K-$500K per market | $50K-$2M per market |

The 3-second TAKER delay on sports markets means informed traders cannot immediately correct prices when Pinnacle lines move, creating a persistent 30-180 second window where Polymarket prices lag fair value.

### 2.2 The Favourite-Longshot Bias (FLB)

Prediction markets exhibit the well-documented FLB: longshots are overpriced relative to their true probability, and favourites are underpriced. On Polymarket, this manifests as tokens priced 5-40c trading above their fair value on the "No" side and below fair value on the "Yes" side.

RN1 (the reference trader, +$20.35M verified P&L) concentrated 80%+ of volume in the 5-40c price range, confirming this is where mispricing is largest.

### 2.3 Information Propagation Delay

When a material event occurs (injury, lineup change, weather), Pinnacle's lines adjust within seconds via algorithmic market makers. Polymarket's prices adjust over minutes because:
- Fewer active market makers
- Lower capital deployed per market
- TAKER delay prevents rapid correction
- Neg-risk collateral requirements slow position entry

This creates a **latency arbitrage window** that systematic traders can exploit.

---

## 3. Fair Probability Estimation

### 3.1 Overround Removal

Bookmaker odds contain embedded margin ("overround"). To extract fair probabilities, we must remove this margin. The bot uses sport-specific methods because margin distribution varies by market structure.

#### 3.1.1 Shin's Model (Soccer, 3-way markets)

Shin (1991, 1993) models a bookmaker protecting against a fraction *z* of insider traders. Margin is distributed non-equally: longshots carry more margin due to adverse selection.

For each outcome *i* with raw implied probability ip_i:

```
fair_prob_i = [sqrt(z^2 + 4(1-z) * ip_i^2 / S) - z] / [2(1-z)]
```

where S = sum of all implied probabilities and z is solved via bisection such that sum(fair_prob_i) = 1.

**Used for:** EPL, Bundesliga, La Liga, UCL, Serie A, Ligue 1 (all 3-way soccer).

#### 3.1.2 MWPO -- Margin Weights Proportional to Odds (US Sports, 2-way)

For tight 2-way markets (NBA, NFL), margin is subtracted proportional to the decimal odds:

```
fair_prob_i = ip_i - w_i * M
where M = sum(ip) - 1        (total margin)
and   w_i = odds_i / sum(odds_j)
```

**Rationale:** Bookmakers shade margin toward the illiquid/uncertain side. MWPO captures this by assigning more margin to longer-odds outcomes.

**Used for:** NBA, NFL, CBB, NHL.

#### 3.1.3 Power Method (Tennis, 2-way with extreme odds)

Each implied probability is raised to exponent k, solved via bisection:

```
fair_prob_i = ip_i^k,  where sum(ip_i^k) = 1
```

**Used for:** ATP, WTA tennis (2-way markets with significant FLB at extremes).

#### 3.1.4 Proportional (Fallback)

Simple normalization: `fair_prob_i = ip_i / sum(ip)`. Adequate when overround < 3% (Pinnacle's typical range), where all methods converge to < 0.1pp difference.

### 3.2 Multi-Book Consensus

When multiple sharp books are available, fair probabilities are combined via efficiency-weighted average:

```
P_fair = sum(P_fair_book_j * w_j) / sum(w_j)
```

| Bookmaker | Efficiency Weight |
|-----------|------------------|
| Pinnacle | 1.00 |
| Betfair Exchange | 0.95 |
| Matchbook | 0.85 |
| BetCris | 0.75 |

Pinnacle is the primary reference because it accepts the largest limits and has the tightest margins, making its closing line the most efficient estimator of true probability.

---

## 4. Edge Calculation

### 4.1 Raw Edge

```
edge = P_fair - P_polymarket
edge_pct = 100 * edge / P_polymarket
```

The edge represents the percentage by which Polymarket underprices an outcome relative to sharp-book fair value.

**Filters applied:**
- Minimum edge: 3% (below this, transaction costs dominate)
- Maximum edge: 25% (above this, likely a matching error)
- Price range: 3c - 50c (highest mispricing zone, per RN1 analysis)
- Time to event: < 5 days (avoid capital lockup)
- Line agreement: exact match required for spreads/totals (tolerance = 0.01)

### 4.2 Edge Confidence Scoring

Not all edges are equally reliable. A 5% edge on an EPL match 2 hours before kickoff with 3 books agreeing is far more reliable than a 5% edge on a minor league match 4 days out with only Pinnacle data.

Five independent factors, each mapped to [0, 1]:

| Factor | Weight | Calculation |
|--------|--------|-------------|
| Book agreement | 0.30 | min(1.0, agreeing_books / total_books * 1.2) |
| Time to event | 0.25 | 1.0 if <2h, 0.85 if <6h, 0.65 if <24h, 0.45 if <48h, 0.30 otherwise |
| Liquidity | 0.15 | min(1.0, log(1 + liquidity) / log(101000)) |
| Market type | 0.15 | h2h: 0.90, spread: 0.75, total: 0.60 |
| Historical accuracy | 0.15 | Based on learning agent win rate for this segment |

```
confidence = sum(factor_i * weight_i) / sum(weight_i)
```

### 4.3 Edge Decay

Edges found far from game start decay as information arrives and prices converge:

```
decay = min_factor + (1 - min_factor) * exp(-hours_to_start / half_life)
effective_edge = raw_edge * decay
```

Parameters: half_life = 12 hours, min_factor = 0.40, no decay within 2 hours of start.

---

## 5. Position Sizing: Fractional Kelly Criterion

### 5.1 The Kelly Formula

For a binary outcome with net odds b = (1/price) - 1:

```
f* = (b*p - q) / b
```

where p = fair probability, q = 1 - p.

Kelly maximises the expected geometric growth rate of the bankroll:

```
G(f) = p * log(1 + f*b) + q * log(1 - f)
```

### 5.2 Quarter-Kelly Implementation

Full Kelly is optimal only with perfect probability estimates and infinite horizon. In practice, estimation error makes it dangerously aggressive. We use quarter-Kelly:

```
f_used = f* * 0.25
```

| Kelly Fraction | Growth Captured | Variance Captured | Ruin Probability |
|---------------|----------------|-------------------|-----------------|
| Full (1.00) | 100% | 100% | Material |
| Half (0.50) | 75% | 50% | Low |
| Quarter (0.25) | 56% | 25% | Negligible |

Quarter-Kelly sacrifices 44% of theoretical growth but reduces variance by 75%, providing a smooth equity curve and negligible risk of ruin.

### 5.3 Position Size Constraints

```
size_usdc = min(
    f_used * effective_bankroll,
    max_position_usdc     ($25)
)
```

Additional constraints:
- Maximum total exposure: $300 (60% of $500 bankroll)
- Minimum trade size: $2 (below this, gas costs dominate)
- No contradictory positions (both sides of same event)
- Bankroll adjusts with realized P&L: effective_bankroll = initial + realized_pnl

### 5.4 Estimation-Error Adjustment (Thorp 2006)

When edge uncertainty is quantifiable:

```
f_adjusted = f_kelly * [1 - 0.5 * (sigma_edge / edge)]
```

This reduces position size when the edge estimate has high variance, preventing overbetting on uncertain opportunities.

---

## 6. Merge Arbitrage (Risk-Free)

### 6.1 Mechanism

On Polymarket, YES + NO tokens for the same market can be merged into $1.00 via the CTF contract. When:

```
yes_ask + no_ask < $1.00
```

a risk-free profit exists:

```
profit_per_pair = $1.00 - (yes_ask + no_ask)
pairs = min(yes_depth_at_ask, no_depth_at_ask)
total_profit = profit_per_pair * pairs - gas_cost
```

### 6.2 Why Merge Opportunities Exist

- Neg-risk markets require full collateral, reducing liquidity
- Market makers on YES and NO sides operate independently
- During volatile events, one side reprices faster than the other
- Low-liquidity markets have wide bid-ask spreads on both sides

RN1's verified data shows MERGE was the **primary profit mechanism**: $40.4M in synthetic sells (35% of total volume), with merges happening continuously across 1,000+ markets simultaneously.

---

## 7. Learning Agent

### 7.1 Adaptive Edge Adjustment

The learning agent segments historical trades by sport, market type, and price bucket. When sufficient samples exist (n >= 20), it adjusts future edge estimates:

```
actual_win_rate = wins / total_trades      (for segment)
predicted_win_rate = mean(entry_prices)    (market's implied probability)

adjustment = (actual_wr - predicted_wr) / predicted_wr
adjusted_edge = raw_edge * (1 + adjustment * LEARNING_RATE)
```

LEARNING_RATE = 0.30 (conservative, avoids overfitting to small samples).

### 7.2 Sport Scoring

Capital allocation across sports is guided by a composite score:

```
score = win_rate * log2(sample_count + 1) * (1 + avg_edge / 100)
```

Sports with higher scores receive preferential capital allocation.

---

## 8. Statistical Edge Validation

Before trusting any observed edge, we apply three independent statistical tests:

### 8.1 Binomial Test (Win Rate Significance)

H0: actual_win_rate = expected_win_rate (market-implied)

```
z = (actual_wr - expected_wr) / sqrt(expected_wr * (1 - expected_wr) / n)
p_value = 1 - Phi(z)
```

Reject H0 at p < 0.05: the win rate is significantly better than market-implied.

### 8.2 t-Test on P&L

H0: mean(pnl) = 0

```
t = mean(pnl) / (stdev(pnl) / sqrt(n))
```

Reject H0 at p < 0.05: the strategy generates statistically significant positive P&L.

### 8.3 Runs Test (Randomness)

Verifies that wins and losses are not serially correlated (which would suggest regime-dependent performance):

```
expected_runs = 2 * n_w * n_l / n + 1
z_runs = (observed_runs - expected_runs) / sqrt(variance_runs)
```

p > 0.05 (desired): outcomes appear random, edge is not driven by streaks.

### 8.4 Combined Confidence Score

| Component | Points Available |
|-----------|-----------------|
| Binomial test p < 0.01 | 25 |
| t-Test p < 0.01 | 25 |
| Sample size >= required | 15 |
| Flat-bet ROI > 5% | 15 |
| Runs test p > 0.05 | 10 |
| Win rate > expected + 5pp | 10 |
| **Total** | **100** |

Verdict: >= 80 "Strong evidence", >= 60 "Moderate", >= 40 "Inconclusive", < 40 "No evidence".

---

## 9. Live Performance Test Metrics

The following metrics define the scorecard against which live agent trading performance should be evaluated. Thresholds are calibrated for a $500 test wallet with quarter-Kelly sizing.

### 9.1 Primary Metrics (Must-Pass)

| Metric | Formula | Target | Red Flag | Review Frequency |
|--------|---------|--------|----------|-----------------|
| **Closing Line Value (CLV)** | mean((entry_fair - closing_fair) / closing_fair) | > +2% | < 0% | Weekly |
| **Win Rate vs Expected** | actual_wr - mean(entry_prices) | > +3pp | < 0pp | Weekly (n >= 30) |
| **Flat-Bet ROI** | total_pnl / total_capital_deployed | > +3% | < -5% | Weekly |
| **Binomial Test p-value** | See 8.1 | < 0.10 | > 0.50 | After 50+ trades |
| **t-Test on PnL p-value** | See 8.2 | < 0.10 | > 0.50 | After 50+ trades |

**CLV is the single most important metric.** A positive CLV means the agent is consistently buying at prices better than where the market closes -- the gold standard of sharp betting. Even during losing streaks, positive CLV confirms the edge is real and variance will resolve favourably.

### 9.2 Risk Metrics (Guardrails)

| Metric | Formula | Target | Hard Limit | Action if Breached |
|--------|---------|--------|------------|-------------------|
| **Max Drawdown** | max(peak_equity - current_equity) | < 15% of bankroll | 25% ($125) | Halt trading, review |
| **Max Drawdown Duration** | Days from peak to recovery | < 14 days | 30 days | Reduce position sizes 50% |
| **Daily Loss Limit** | sum(pnl) for single day | > -$25 | -$50 | Halt for remainder of day |
| **Exposure / Bankroll** | total_open_exposure / bankroll | < 60% | 80% | No new positions |
| **Single Position / Bankroll** | max_position / bankroll | < 5% | 10% | Reject trade |
| **Contradictory Positions** | Count of opposing sides held | 0 | 0 | Immediate investigation |

### 9.3 Edge Quality Metrics (Diagnostic)

| Metric | Formula | Healthy Range | Concern |
|--------|---------|--------------|---------|
| **Brier Score** | mean((predicted_prob - actual)^2) | < 0.22 | > 0.25 (worse than naive) |
| **Log Loss** | mean(-(y*log(p) + (1-y)*log(1-p))) | < 0.65 | > 0.69 (worse than coin flip) |
| **Profit Factor** | gross_wins / abs(gross_losses) | > 1.3 | < 1.0 (net loser) |
| **Avg Edge Realized** | mean(pnl / cost) for resolved trades | > +3% | < 0% |
| **Edge Decay Ratio** | realized_edge / entry_edge | > 0.50 | < 0.30 (edges disappearing) |
| **Match Accuracy** | % of matched markets that are correct | > 90% | < 80% (false matches) |

### 9.4 Operational Metrics (Health Checks)

| Metric | Target | Red Flag |
|--------|--------|----------|
| API uptime | > 99% | < 95% |
| Scan cycle success rate | > 98% | < 90% |
| Odds API requests remaining | > 5,000 / month | < 1,000 |
| Position resolution latency | < 24h after event | > 48h |
| Stale positions (past events, still open) | 0 | > 5 |
| 404 sport key errors per cycle | 0 | > 2 |

### 9.5 Milestone Checkpoints

| Milestone | Trades Required | Key Decision |
|-----------|----------------|--------------|
| **M1: Signal Validation** | 30 resolved | Is CLV positive? Is win rate > expected? If no to both, review matching logic. |
| **M2: Statistical Significance** | 100 resolved | Binomial p < 0.10? t-test p < 0.10? If no, edge may not be real. |
| **M3: Sizing Validation** | 100 resolved | Is Kelly sizing profitable? Compare vs flat-bet. If flat-bet wins, sizing is miscalibrated. |
| **M4: Sport Segmentation** | 200 resolved | Which sports/market types are profitable? Prune losers, concentrate on winners. |
| **M5: Live Readiness** | 300 resolved, CLV > +2%, Brier < 0.22, PF > 1.3 | Approve transition from paper to live trading with real USDC. |

### 9.6 Benchmark Comparison

| Benchmark | Expected Performance | Source |
|-----------|---------------------|--------|
| Random betting (no edge) | -2% to -5% ROI (bookmaker vig) | Theoretical |
| Naive Pinnacle follower | +0% to +2% ROI (before costs) | Industry consensus |
| Competent sports bettor | +3% to +8% CLV | Pinnacle data |
| RN1 (reference trader) | +21.8% ROI on $93.1M deployed | Verified activity data |
| **Our target (paper phase)** | **+3% CLV, +5% flat-bet ROI** | Conservative for $500 wallet |

---

## 10. Summary of Mathematical Edge Sources

| Edge Source | Mechanism | Magnitude | Persistence |
|-------------|-----------|-----------|-------------|
| Sharp-book mispricing | Polymarket lags Pinnacle by 30-180s | 3-15% per trade | High (structural) |
| Favourite-longshot bias | Longshots overpriced in prediction markets | 5-25% at 5-20c range | High (behavioural) |
| Merge arbitrage | YES+NO < $1.00 due to fragmented liquidity | 1-5% per merge | High (structural) |
| TAKER delay exploitation | 3s delay prevents rapid price correction | 2-8% during live events | High (protocol-level) |
| Information asymmetry | Sharp books price news faster than Polymarket | Variable, event-driven | Medium (competition increases) |
| Edge decay capture | Entering early, edge decays toward 0 by close | Captured via CLV | Medium (time-dependent) |

The combination of multiple independent edge sources provides robustness: even if one source diminishes (e.g., more market makers enter Polymarket), others persist due to structural protocol constraints.

---

## References

1. Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*, 35(4), 917-926.
2. Shin, H.S. (1991). "Optimal Betting Odds Against Insider Traders." *Economic Journal*, 101(408), 1179-1185.
3. Shin, H.S. (1993). "Measuring the Incidence of Insider Trading in a Market for State-Contingent Claims." *Economic Journal*, 103(420), 1141-1153.
4. Thorp, E.O. (2006). "The Kelly Criterion in Blackjack, Sports Betting, and the Stock Market." *Handbook of Asset and Liability Management*, Vol. 1.
5. Clarke, S., Krase, S., Peel, D. (2017). "Removing the Favourite-Longshot Bias." *Journal of Gambling Studies*.
6. Cheung, K. (2015). "A Comparison of Methods for Removing the Margin from Bookmaker Odds." *Journal of Prediction Markets*.
7. Pinnacle Sports (2019). "Closing Line Value: The Most Important Metric for Sports Bettors."
8. MacLean, L.C., Thorp, E.O., Ziemba, W.T. (2011). *The Kelly Capital Growth Investment Criterion*. World Scientific.

---

*Document generated by Everest Agentic AI Trader system. Last updated: 2026-03-06.*
