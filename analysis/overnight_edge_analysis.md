# Overnight Edge Quality Analysis - 2026-03-06

**Analysis time:** ~10:40 UTC, 2026-03-06
**Data sources:** VPS my_positions.csv, /api/positions endpoint, bot.log, scanner.log, edges cache, matcher.py source

---

## 1. Portfolio Summary

| Metric | Value |
|---|---|
| Open positions | 62 |
| Total cost (USDC) | ~$598 |
| Sports | nba (18), lal (14), bun (18), epl (7), betfair (1) |
| Market types | total (37), h2h (15), spread (10) |
| DRY RUN mode | Yes -- all positions are simulated |

All 62 positions were opened between 00:32 and 08:50 UTC on 2026-03-06.

---

## 2. Matching Error Analysis (Spreads/Totals with Wrong Point Values)

### 2.1 Critical Issue: Spread Point Mismatch Tolerance

The matcher at `src/matcher.py` line 867 allows spread lines to differ by up to **1.0 points**:
```python
if abs(abs(odds_point) - pm_spread) > 1.0:
    continue
```

For totals (line 892), tolerance is **0.5 points**:
```python
if abs(odds_point - pm_total) > 0.5:
    continue
```

This means a Polymarket spread of -1.5 could be matched against a Pinnacle spread of -2.5 (difference = 1.0, within tolerance). This creates **phantom edges** because the probability of winning by 2+ goals is very different from winning by 1+ goals.

### 2.2 Likely Matching Errors in Current Positions

The scanner log shows the top "edges" from the 21:41 UTC scan were:

| Slug | Poly Price | Fair Prob | Edge | Issue |
|---|---|---|---|---|
| lal-osa-mal-2026-03-06-spread-away-**2pt5** | 0.015 | 0.188 | +1193% | Poly has -2.5 spread, Pinnacle likely has -1.5 |
| bun-lei-aug-2026-03-07-spread-away-**2pt5** | 0.013 | 0.146 | +1069% | Same pattern |
| lal-sev-ray-2026-03-08-spread-home-**2pt5** | 0.035 | 0.378 | +966% | Same pattern |
| lal-get-bet-2026-03-07-spread-away-**2pt5** | 0.034 | 0.337 | +892% | Same pattern |
| bun-pau-ein-2026-03-08-spread-away-**2pt5** | 0.038 | 0.371 | +863% | Same pattern |

These were NOT purchased (too extreme, likely capped by risk limits or filtered), but the bot DID buy the **-1.5 spread** versions of several games:

**Positions that are likely spread mismatches (high risk):**

| Position | Entry | Fair Prob | Edge % | Concern |
|---|---|---|---|---|
| lal-osa-mal-2026-03-06-spread-home-**1pt5** [Osasuna] | 0.290 | 0.481 | +65.8% | Pinnacle -1.5 matched to PM -1.5, but fair_prob=0.48 suggests Pinnacle may have -0.5 or AH line |
| lal-bar-sev-2026-03-15-spread-home-**2pt5** [Barcelona] | 0.335 | 0.525 | +56.6% | Barca -2.5 in La Liga, plausible but very high edge |
| lal-vil-elc-2026-03-08-spread-home-**1pt5** [Villarreal] | 0.410 | 0.536 | +30.7% | Could be line mismatch |
| bun-lei-aug-2026-03-07-spread-home-**1pt5** [Leipzig] | 0.450 | 0.506 | +12.5% | More plausible, smaller edge |
| bun-ein-hei-2026-03-14-spread-home-**1pt5** [Frankfurt] | 0.480 | 0.510 | +6.3% | Plausible |
| bun-hof-wol-2026-03-14-spread-away-**1pt5** [Wolfsburg] | 0.440 | 0.468 | +6.3% | Plausible |

**Assessment:** The top 3 spread positions (65.8%, 56.6%, 30.7% edges) are **highly suspicious**. In La Liga, Pinnacle typically offers -1.5 Asian handicap lines for favorites, which is a different product than Polymarket's European -1.5 spread. The fair probs appear to be derived from Pinnacle's -1.5 handicap line but applied to a -1.5 European spread, which should have the same points but different resolution rules (Asian vs European push handling).

### 2.3 Totals Line Mismatches

Several soccer total positions show suspiciously high edges:

| Position | PM Line | Entry | Fair | Edge % | Concern |
|---|---|---|---|---|---|
| lal-get-bet-2026-03-07-total-**2pt5** Over | 2.5 | 0.335 | 0.460 | +37.4% | PM O2.5 matched to Pinnacle O2.5 -- plausible but 37% edge is extreme for soccer |
| lal-get-bet-2026-03-07-total-**1pt5** Under | 1.5 | 0.395 | 0.540 | +36.6% | PM U1.5 goals -- Pinnacle unlikely to offer 1.5 total in soccer (they use 2.5) |
| lal-vil-elc-2026-03-08-total-**3pt5** Over | 3.5 | 0.375 | 0.486 | +29.6% | PM O3.5 matched to Pinnacle O2.5 (within 0.5 tolerance)? |
| bun-mai-stu-2026-03-07-total-**3pt5** Over | 3.5 | 0.380 | 0.488 | +28.5% | Same concern |

**Key issue with lal-get-bet total-1pt5:** Pinnacle soccer does not typically offer O/U 1.5 goals. The matcher's 0.5 tolerance means the PM 1.5 total could have been matched to Pinnacle's 2.0 or 2.5 total. If it was matched to a 2.5 total, the fair prob for U2.5 (0.54) is being applied to U1.5, which is a fundamentally different bet. **This is almost certainly a matching error.**

---

## 3. Edge Distribution by Sport and Market Type

### 3.1 By Sport

| Sport | Positions | Total Cost | Avg Edge % | Median Edge % |
|---|---|---|---|---|
| lal (La Liga) | 14 | $214.54 | 26.8% | 16.5% |
| bun (Bundesliga) | 18 | $170.89 | 12.2% | 8.4% |
| nba (NBA) | 18 | $176.42 | 16.2% | 5.3% |
| epl (EPL) | 7 | $81.92 | 12.8% | 9.9% |

La Liga positions have the highest average edge (26.8%), which is **anomalous** -- this is a mature, well-arbitraged market. This strongly suggests systematic matching errors in La Liga spreads/totals.

### 3.2 By Market Type

| Type | Positions | Total Cost | Avg Edge % | Range |
|---|---|---|---|---|
| total | 37 | $328.59 | 12.8% | 3.1% - 70.6% |
| h2h | 15 | $168.06 | 17.2% | 3.3% - 81.8% |
| spread | 10 | $148.11 | 27.7% | 6.3% - 65.8% |

Spread positions show the highest average edge at 27.7%, driven by the top 3 suspicious La Liga positions. The h2h average is inflated by the Clippers-Spurs 81.8% edge outlier (betfair source).

### 3.3 Edge Distribution (All 62 Positions)

| Edge Bucket | Count | Total Cost |
|---|---|---|
| > 50% | 3 | $75.00 |
| 20-50% | 7 | $134.29 |
| 10-20% | 14 | $195.70 |
| 5-10% | 18 | $120.53 |
| 3-5% | 15 | $59.67 |
| < 3% | 5 | $12.57 |

**34 positions (55%) show edges > 10%, which is unrealistic for liquid sports markets.** Even 5% true edges are exceptional. This portfolio is dominated by phantom edges from matching imprecision.

---

## 4. Positions Resolving Soon (Games Today/Tomorrow)

### 4.1 Already Played / In Progress (March 5 NBA games -- should be resolved)

These 13 NBA positions reference March 5 games that have **already been played**:

| Slug | Outcome | Entry | Cost | Status |
|---|---|---|---|---|
| nba-det-sas-2026-03-05-total-227pt5 | Under | 0.475 | $5.34 | Should be resolved |
| nba-det-sas-2026-03-05-total-228pt5 | Under | 0.485 | $3.64 | Should be resolved |
| nba-det-sas-2026-03-05 | Pistons | 0.325 | $18.19 | Should be resolved |
| nba-nop-sac-2026-03-05-total-234pt5 | Under | 0.495 | $5.07 | Should be resolved |
| nba-nop-sac-2026-03-05-total-235pt5 | Over | 0.500 | $3.87 | Should be resolved |
| nba-nop-sac-2026-03-05 | Kings | 0.305 | $5.19 | Should be resolved |
| nba-uta-was-2026-03-05-total-241pt5 | Over | 0.290 | $25.00 | Should be resolved |
| nba-uta-was-2026-03-05-total-241pt5 | Under | 0.460 | $9.26 | Should be resolved |
| nba-uta-was-2026-03-05-total-240pt5 | Over | 0.455 | $9.11 | Should be resolved |
| nba-uta-was-2026-03-05-total-240pt5 | Under | 0.460 | $9.26 | Should be resolved |
| nba-uta-was-2026-03-05 | Wizards | 0.425 | $7.10 | Should be resolved |
| nba-chi-phx-2026-03-05-total-224pt5 | Over | 0.470 | $6.47 | Should be resolved |
| nba-chi-phx-2026-03-05-total-225pt5 | Over | 0.490 | $4.66 | Should be resolved |
| nba-chi-phx-2026-03-05-total-223pt5 | Under | 0.485 | $4.58 | Should be resolved |
| nba-chi-phx-2026-03-05 | Bulls | 0.295 | $10.55 | Should be resolved |

**Total at risk from Mar 5 games: $126.29 (15 positions)**

**CRITICAL NOTE:** The bot holds BOTH Over AND Under on the same total line for several games:
- nba-uta-was total 241.5: Over ($25.00) + Under ($9.26) = $34.26 cost
- nba-uta-was total 240.5: Over ($9.11) + Under ($9.26) = $18.37 cost

This is **guaranteed loss** -- one side resolves at $1 per share, the other at $0. For the 241.5 total: cost $34.26, max payout ~$86.21 (if Over wins) or ~$20.13 (if Under wins). The Over was bought at 0.29 (86 shares) and Under at 0.46 (20 shares). These are at different times and prices, so the combined position is effectively long vol -- profitable only if the Over side wins.

### 4.2 Games Today (March 6)

| Slug | Outcome | Entry | Cost | Game Time (est) |
|---|---|---|---|---|
| lal-osa-mal-2026-03-06-spread-home-1pt5 | Osasuna | 0.290 | $25.00 | Today |
| lal-osa-mal-2026-03-06-total-2pt5 | Over | 0.430 | $13.94 | Today |
| nba-lac-sas-2026-03-06 | Clippers | 0.275 | $25.00 | Tonight |
| nba-lac-sas-2026-03-06-total-224pt5 | Under | 0.490 | $4.68 | Tonight |
| nba-lac-sas-2026-03-06-total-225pt5 | Over | 0.485 | $5.54 | Tonight |
| nba-nop-phx-2026-03-06-total-225pt5 | Over | 0.475 | $5.02 | Tonight |
| nba-nop-phx-2026-03-06-total-224pt5 | Under | 0.495 | $4.13 | Tonight |

**Total resolving today: $83.31 (7 positions)**

Again, the bot holds Over 225.5 AND Under 224.5 on LAC-SAS -- these overlap such that if the total is exactly 225, both could win. But in practice this is a narrow band bet.

### 4.3 Games Tomorrow (March 7)

| Slug | Outcome | Entry | Cost |
|---|---|---|---|
| lal-get-bet-2026-03-07-total-2pt5 | Over | 0.335 | $23.56 |
| lal-get-bet-2026-03-07-total-1pt5 | Under | 0.395 | $25.00 |
| bun-mai-stu-2026-03-07-total-3pt5 | Over | 0.380 | $21.86 |
| bun-mai-stu-2026-03-07-total-2pt5 | Under | 0.405 | $22.39 |
| bun-lei-aug-2026-03-07-total-3pt5 | Over | 0.440 | $14.25 |
| bun-lei-aug-2026-03-07-spread-home-1pt5 | Leipzig | 0.450 | $12.82 |
| bun-hei-hof-2026-03-07-total-3pt5 | Over | 0.425 | $12.67 |

**Total resolving tomorrow: $132.55 (7 positions)**

---

## 5. Positions Where Price Has Moved Against Us

Without live Polymarket orderbook snapshots, I can assess this from the entry edges and the fact that **this is DRY RUN mode** -- no real fills occurred. However, the API endpoint data shows all positions still at 0.0 P&L (expected for dry run with no actual market exposure).

### 5.1 Highest Risk Positions (Largest Edge = Largest Likely Mispricing)

The **Clippers h2h** position deserves special attention:
- **Entry:** 0.275 with fair_prob = 0.500 (from betfair_ex_eu)
- **Edge claimed:** 81.8%
- **Source:** Betfair exchange, not Pinnacle
- **Risk:** Betfair exchange odds for NBA can be very stale/thin, especially at 2 AM UTC. A 0.50 fair prob for Clippers vs Spurs implies an even-money game. If Clippers are actually ~27.5% implied (the PM price), Betfair may have had a stale line or the match was incorrectly mapped.

### 5.2 Contradictory Positions (Both Sides of Same Market)

The bot has taken both sides of several markets:

| Game | Position 1 | Position 2 | Combined Cost |
|---|---|---|---|
| UTA-WAS O/U 241.5 | Over @ 0.29 ($25) | Under @ 0.46 ($9.26) | $34.26 |
| UTA-WAS O/U 240.5 | Over @ 0.455 ($9.11) | Under @ 0.46 ($9.26) | $18.37 |
| LAC-SAS O/U ~225 | Over 225.5 @ 0.485 ($5.54) | Under 224.5 @ 0.49 ($4.68) | $10.22 |
| NOP-PHX O/U ~225 | Over 225.5 @ 0.475 ($5.02) | Under 224.5 @ 0.495 ($4.13) | $9.15 |

For different lines (e.g., Over 225.5 + Under 224.5), this is a "middle" bet that wins if the total falls between 225 and 225 -- effectively zero chance. This is nearly always a losing strategy when paying the spread on both sides.

---

## 6. Key Findings and Recommendations

### 6.1 Critical Issues

1. **Spread matching tolerance too loose (1.0 points):** This allows -1.5 spreads to match against -2.5 spreads, creating phantom edges of 30-65%. **Recommendation: Reduce tolerance to 0.0 (exact match only) for spreads.**

2. **Totals matching tolerance too loose (0.5 points):** The 0.5 tolerance allows soccer O/U 1.5 to match against O/U 2.0 or 2.5. **Recommendation: Require exact line match (tolerance = 0.0).**

3. **No check for contradictory positions:** The bot opens both Over and Under on the same game at adjacent lines, guaranteeing losses. **Recommendation: Add position-level conflict detection.**

4. **Stale betfair exchange odds:** The Clippers 81.8% edge came from betfair_ex_eu at 2 AM UTC. Exchange odds for US sports are notoriously thin during off-hours. **Recommendation: Require Pinnacle as primary source, or apply a staleness discount for exchange odds.**

5. **No resolution tracking for completed games:** 15 positions reference March 5 NBA games that should already be resolved but still show status=open. The position tracker does not poll for resolution events.

### 6.2 Edge Quality Assessment

| Category | Count | Est. True Positive |
|---|---|---|
| Likely matching errors (>30% edge, spreads/totals) | 6 | 0% |
| Suspicious (15-30% edge, soccer) | 8 | ~20% |
| Plausible edges (5-15%, correct lines) | 28 | ~50% |
| Marginal edges (3-5%) | 15 | ~60% |
| Very marginal (<3%) | 5 | ~40% |

**Estimated portfolio-wide true positive rate: ~35%** -- roughly 22 of 62 positions may have genuine (small) edges. The remaining 40 are likely matching artifacts or noise.

### 6.3 Expected P&L Impact

If this were live trading with $598 deployed:
- **Matching error positions (~$200 cost):** Expected to lose 30-50% of capital = -$60 to -$100
- **Genuine small-edge positions (~$200 cost):** Expected +3-5% return = +$6 to +$10
- **Noise positions (~$200 cost):** Expected ~breakeven minus vig = -$5 to -$15
- **Net expected: approximately -$55 to -$105** (i.e., the matching errors dominate)

### 6.4 Immediate Actions

1. **Fix matcher.py:** Set spread tolerance to 0.0 and total tolerance to 0.0 (exact match only)
2. **Add position conflict detection** in strategy.py to prevent both-sides bets
3. **Add edge cap:** Reject any position with >25% edge as likely matching error
4. **Add betfair staleness filter:** Ignore betfair odds more than 30 minutes old for US sports
5. **Add resolution polling** to close out completed games

---

*Analysis performed by reading VPS data and local source code. No code was modified.*
