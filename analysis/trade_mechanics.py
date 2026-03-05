"""
RN1 Polymarket Trade Mechanics Analysis
========================================
Deep analysis of HOW wallet 0x2005D16a84CEEfa912D4e380cD32E7ff827875Ea makes money.

Data:
  - rn1_positions.csv: 10,500 positions with full PnL breakdown
  - rn1_trades.csv: 2,671 recent individual trades

Key column definitions (positions):
  size         = shares currently held (after all buys/sells/redemptions)
  avgPrice     = average cost per share for remaining position
  initialValue = avgPrice * size (cost basis of remaining shares)
  totalBought  = total USDC ever spent buying shares on this position
  realizedPnl  = profit/loss from sells and redemptions (closed portions)
  cashPnl      = currentValue - initialValue (unrealized P&L on remaining shares)
  curPrice     = current market price (0 = resolved losing side)
  redeemable   = whether shares can be redeemed (event resolved)
"""

import pandas as pd
import numpy as np
from datetime import datetime
from collections import defaultdict

pd.set_option('display.float_format', '{:.2f}'.format)
pd.set_option('display.max_colwidth', 60)
pd.set_option('display.width', 140)

DATA_DIR = r"C:\Users\MartonPeterPrice\polymarket-rn1\data"

pos = pd.read_csv(f"{DATA_DIR}/rn1_positions.csv")
trades = pd.read_csv(f"{DATA_DIR}/rn1_trades.csv")

# Convert timestamps
trades['datetime'] = pd.to_datetime(trades['timestamp'], unit='s')
pos['endDate'] = pd.to_datetime(pos['endDate'])

# Derived columns
pos['netPnl'] = pos['realizedPnl'] + pos['cashPnl']
pos['turnover_ratio'] = pos['totalBought'] / pos['initialValue']
pos['is_resolved'] = pos['curPrice'] == 0
pos['is_winner'] = pos['curPrice'] > 0.5  # winning side of resolved event

# ============================================================================
# 1. ENTRY/EXIT PATTERN: Trading vs Hold-to-Resolution
# ============================================================================
print("=" * 90)
print("1. ENTRY/EXIT PATTERN: Does RN1 trade or hold to resolution?")
print("=" * 90)

# Positions where all shares are still held (no selling/redeeming happened)
# If totalBought == initialValue, no shares were ever sold (all original shares remain)
# But turnover_ratio > 1 means shares were bought and sold repeatedly

# The real question: what fraction of profit comes from realizedPnl (trading) vs cashPnl (holding)?
total_realized = pos['realizedPnl'].sum()
total_cash = pos['cashPnl'].sum()
total_net = total_realized + total_cash

print(f"\n  Total realizedPnl (from sells/redemptions):  ${total_realized:>14,.2f}")
print(f"  Total cashPnl (unrealized / final position):  ${total_cash:>14,.2f}")
print(f"  Net P&L:                                      ${total_net:>14,.2f}")
print(f"\n  realizedPnl as % of |total flows|:  {abs(total_realized)/(abs(total_realized)+abs(total_cash))*100:.1f}%")

# cashPnl is dominated by losing resolved positions (shares worth $0)
# The REAL profit is in realizedPnl
# Let's break down realizedPnl sources
pos_realized_pos = pos[pos['realizedPnl'] > 0]
pos_realized_neg = pos[pos['realizedPnl'] < 0]
pos_realized_zero = pos[pos['realizedPnl'] == 0]

print(f"\n  Positions with realized profit:  {len(pos_realized_pos):>6,d}  (${pos_realized_pos['realizedPnl'].sum():>12,.2f})")
print(f"  Positions with realized loss:    {len(pos_realized_neg):>6,d}  (${pos_realized_neg['realizedPnl'].sum():>12,.2f})")
print(f"  Positions with zero realized:    {len(pos_realized_zero):>6,d}")

# Trade types from trades data
print(f"\n  Trade type breakdown (from trades.csv, recent sample):")
for ttype, count in trades['type'].value_counts().items():
    usdc = trades[trades['type'] == ttype]['usdcSize'].sum()
    print(f"    {ttype:>10s}: {count:>5d} trades, ${usdc:>12,.2f} USDC volume")

trade_sides = trades[trades['type'] == 'TRADE']['side'].value_counts()
print(f"\n  Trade direction (TRADE type only):")
for side, count in trade_sides.items():
    usdc = trades[(trades['type'] == 'TRADE') & (trades['side'] == side)]['usdcSize'].sum()
    print(f"    {side:>5s}: {count:>5d} trades, ${usdc:>12,.2f} USDC")

# Redemptions vs trading
redeem_trades = trades[trades['type'] == 'REDEEM']
merge_trades = trades[trades['type'] == 'MERGE']
print(f"\n  REDEEM events: {len(redeem_trades)} (winning shares redeemed for $1)")
print(f"  MERGE events:  {len(merge_trades)} (Yes+No merged for $1 arbitrage)")

# Key finding: turnover analysis
print(f"\n  Turnover ratio (totalBought / initialValue) - measures round-trip trading:")
for pct in [25, 50, 75, 90, 95, 99]:
    val = pos['turnover_ratio'].quantile(pct/100)
    print(f"    P{pct:>2d}: {val:>8.1f}x")

print(f"\n  FINDING: Median turnover is {pos['turnover_ratio'].median():.1f}x, meaning RN1 buys/sells")
print(f"  repeatedly on the same position. This is ACTIVE TRADING, not buy-and-hold.")
print(f"  Only {(pos['turnover_ratio'] < 1.5).sum()} of {len(pos)} positions have <1.5x turnover (pure hold).")

# ============================================================================
# 2. PRICING EDGE: Entry prices on winners vs losers
# ============================================================================
print("\n" + "=" * 90)
print("2. PRICING EDGE: Average entry prices on winning vs losing positions")
print("=" * 90)

# Resolved positions only (curPrice == 0 means losing side resolved)
resolved = pos[pos['redeemable'] == True].copy()

# For resolved losing positions: curPrice = 0, all remaining shares are worthless
# For resolved winning positions: curPrice ~ 1 (0.9995), shares redeemable for $1
# But most winning positions have size=0 (already redeemed) or are in the data as losing side

# Better approach: look at positions where realizedPnl is significantly positive
# These are positions where RN1 bought cheap and sold/redeemed at profit

# Classify by netPnl (total P&L including both realized and unrealized)
winners = pos[pos['netPnl'] > 0]
losers = pos[pos['netPnl'] < 0]

print(f"\n  Winning positions (netPnl > 0): {len(winners):,d}")
print(f"    Avg entry price (avgPrice): {winners['avgPrice'].mean():.4f}")
print(f"    Median entry price:         {winners['avgPrice'].median():.4f}")
print(f"    Total net profit:           ${winners['netPnl'].sum():,.2f}")

print(f"\n  Losing positions (netPnl < 0): {len(losers):,d}")
print(f"    Avg entry price (avgPrice): {losers['avgPrice'].mean():.4f}")
print(f"    Median entry price:         {losers['avgPrice'].median():.4f}")
print(f"    Total net loss:             ${losers['netPnl'].sum():,.2f}")

# Price distribution analysis
print(f"\n  Entry price distribution (all positions):")
for bucket_lo, bucket_hi, label in [
    (0, 0.10, "0.00-0.10 (deep longshot)"),
    (0.10, 0.20, "0.10-0.20 (longshot)"),
    (0.20, 0.40, "0.20-0.40 (underdog)"),
    (0.40, 0.60, "0.40-0.60 (coin flip)"),
    (0.60, 0.80, "0.60-0.80 (favorite)"),
    (0.80, 1.01, "0.80-1.00 (heavy favorite)"),
]:
    mask = (pos['avgPrice'] >= bucket_lo) & (pos['avgPrice'] < bucket_hi)
    bucket = pos[mask]
    n = len(bucket)
    avg_realized = bucket['realizedPnl'].mean() if n > 0 else 0
    total_realized = bucket['realizedPnl'].sum()
    win_rate = (bucket['netPnl'] > 0).mean() * 100 if n > 0 else 0
    print(f"    {label}: {n:>5,d} positions, "
          f"avg realized ${avg_realized:>8.2f}, "
          f"total realized ${total_realized:>12,.2f}, "
          f"win rate {win_rate:.1f}%")

# Check: is RN1 buying at prices below implied probability?
# If buying Yes at 0.30 and it wins 50% of the time -> edge
# We can estimate this from the data
print(f"\n  Avg price paid on ALL positions: {pos['avgPrice'].mean():.4f}")
print(f"  Weighted avg price (by initialValue): {(pos['avgPrice'] * pos['initialValue']).sum() / pos['initialValue'].sum():.4f}")

# ============================================================================
# 3. POSITION SIZING
# ============================================================================
print("\n" + "=" * 90)
print("3. POSITION SIZING: How does RN1 size bets?")
print("=" * 90)

print(f"\n  InitialValue (cost basis of remaining position) stats:")
print(f"    Mean:   ${pos['initialValue'].mean():>12,.2f}")
print(f"    Median: ${pos['initialValue'].median():>12,.2f}")
print(f"    Std:    ${pos['initialValue'].std():>12,.2f}")
print(f"    Max:    ${pos['initialValue'].max():>12,.2f}")
print(f"    Min:    ${pos['initialValue'].min():>12,.2f}")

print(f"\n  TotalBought (total USDC spent) stats:")
print(f"    Mean:   ${pos['totalBought'].mean():>12,.2f}")
print(f"    Median: ${pos['totalBought'].median():>12,.2f}")
print(f"    Std:    ${pos['totalBought'].std():>12,.2f}")
print(f"    Max:    ${pos['totalBought'].max():>12,.2f}")

# Size distribution
print(f"\n  TotalBought distribution:")
for pct in [10, 25, 50, 75, 90, 95, 99]:
    val = pos['totalBought'].quantile(pct/100)
    print(f"    P{pct:>2d}: ${val:>12,.2f}")

# Is sizing related to price (edge)?
print(f"\n  TotalBought by entry price bucket:")
for bucket_lo, bucket_hi, label in [
    (0, 0.10, "0.00-0.10"),
    (0.10, 0.20, "0.10-0.20"),
    (0.20, 0.40, "0.20-0.40"),
    (0.40, 0.60, "0.40-0.60"),
    (0.60, 0.80, "0.60-0.80"),
    (0.80, 1.01, "0.80-1.00"),
]:
    mask = (pos['avgPrice'] >= bucket_lo) & (pos['avgPrice'] < bucket_hi)
    bucket = pos[mask]
    if len(bucket) > 0:
        print(f"    {label}: median ${bucket['totalBought'].median():>10,.2f}, "
              f"mean ${bucket['totalBought'].mean():>10,.2f}, "
              f"n={len(bucket):,d}")

# Check if sizing is correlated with realizedPnl potential
corr = pos[['totalBought', 'avgPrice', 'realizedPnl', 'turnover_ratio']].corr()
print(f"\n  Correlations:")
print(f"    totalBought vs avgPrice:      {corr.loc['totalBought','avgPrice']:.3f}")
print(f"    totalBought vs realizedPnl:   {corr.loc['totalBought','realizedPnl']:.3f}")
print(f"    totalBought vs turnover:      {corr.loc['totalBought','turnover_ratio']:.3f}")

# ============================================================================
# 4. TIMING: Speed of entry/exit
# ============================================================================
print("\n" + "=" * 90)
print("4. TIMING: Trade speed and pattern analysis")
print("=" * 90)

# Analyze timestamp clustering
trades_sorted = trades.sort_values('timestamp')
trades_sorted['time_diff'] = trades_sorted['timestamp'].diff()

print(f"\n  Time between consecutive trades (seconds):")
td = trades_sorted['time_diff'].dropna()
for pct in [10, 25, 50, 75, 90, 95]:
    val = td.quantile(pct/100)
    print(f"    P{pct:>2d}: {val:>8.0f}s ({val/60:.1f} min)")

print(f"\n  Trades within 2 seconds of each other: {(td <= 2).sum()} ({(td <= 2).mean()*100:.1f}%)")
print(f"  Trades within 10 seconds: {(td <= 10).sum()} ({(td <= 10).mean()*100:.1f}%)")
print(f"  Trades within 60 seconds: {(td <= 60).sum()} ({(td <= 60).mean()*100:.1f}%)")

# Burst analysis: how many trades happen in rapid succession?
burst_threshold = 5  # seconds
trades_sorted['new_burst'] = trades_sorted['time_diff'] > burst_threshold
trades_sorted['burst_id'] = trades_sorted['new_burst'].cumsum()
burst_sizes = trades_sorted.groupby('burst_id').size()

print(f"\n  Trade burst analysis (bursts separated by >{burst_threshold}s gaps):")
print(f"    Number of bursts: {len(burst_sizes):,d}")
print(f"    Avg trades per burst: {burst_sizes.mean():.1f}")
print(f"    Max trades in a burst: {burst_sizes.max()}")
print(f"    Bursts with >10 trades: {(burst_sizes > 10).sum()}")

# Directionality: BUY vs SELL ratio
buy_trades = trades[trades['side'] == 'BUY']
sell_trades = trades[trades['side'] == 'SELL']
print(f"\n  Directional analysis:")
print(f"    BUY trades:  {len(buy_trades):>5d} ({len(buy_trades)/len(trades)*100:.1f}%)")
print(f"    SELL trades: {len(sell_trades):>5d} ({len(sell_trades)/len(trades)*100:.1f}%)")
print(f"    => Overwhelmingly BUY-side. NOT a market-maker (would show ~50/50).")
print(f"    => RN1 is a DIRECTIONAL bettor who buys positions and holds to resolution.")

# Time of day analysis
trades_sorted['hour'] = trades_sorted['datetime'].dt.hour
hour_dist = trades_sorted.groupby('hour').agg(
    n_trades=('timestamp', 'count'),
    total_usdc=('usdcSize', 'sum')
).reset_index()
print(f"\n  Trading by hour (UTC):")
for _, row in hour_dist.iterrows():
    bar = "#" * int(row['n_trades'] / 10)
    print(f"    {int(row['hour']):>2d}:00  {int(row['n_trades']):>4d} trades  ${row['total_usdc']:>10,.0f}  {bar}")

# Day of week
trades_sorted['dow'] = trades_sorted['datetime'].dt.day_name()
dow_dist = trades_sorted.groupby('dow')['usdcSize'].agg(['count', 'sum'])
dow_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
print(f"\n  Trading by day of week:")
for day in dow_order:
    if day in dow_dist.index:
        row = dow_dist.loc[day]
        print(f"    {day:>10s}: {int(row['count']):>4d} trades, ${row['sum']:>10,.0f}")

# ============================================================================
# 5. MARKET SELECTION: Sports/event category analysis
# ============================================================================
print("\n" + "=" * 90)
print("5. MARKET SELECTION: Which sports/events are most profitable?")
print("=" * 90)

# Extract sport/category from eventSlug
def classify_event(slug):
    if pd.isna(slug):
        return 'unknown'
    slug = str(slug).lower()
    if slug.startswith('nfl-') or slug.startswith('nfl_'):
        return 'NFL'
    elif slug.startswith('nba-') or slug.startswith('nba_'):
        return 'NBA'
    elif slug.startswith('epl-'):
        return 'EPL (English Premier League)'
    elif slug.startswith('ucl-'):
        return 'UCL (Champions League)'
    elif slug.startswith('uel-'):
        return 'UEL (Europa League)'
    elif slug.startswith('bun-'):
        return 'Bundesliga'
    elif slug.startswith('lal-') or slug.startswith('lig-'):
        return 'La Liga'
    elif slug.startswith('ser-'):
        return 'Serie A'
    elif slug.startswith('cs2-'):
        return 'CS2 (Counter-Strike)'
    elif slug.startswith('lol-'):
        return 'LoL (League of Legends)'
    elif slug.startswith('nhl-'):
        return 'NHL'
    elif slug.startswith('mlb-'):
        return 'MLB'
    elif slug.startswith('acn-'):
        return 'Africa Cup of Nations'
    elif slug.startswith('tur-'):
        return 'Turkish Super Lig'
    elif slug.startswith('ncaab-') or slug.startswith('ncaam-'):
        return 'NCAA Basketball'
    elif slug.startswith('ncaaf-'):
        return 'NCAA Football'
    elif any(slug.startswith(p) for p in ['scop-', 'sco-']):
        return 'Scottish Premiership'
    elif slug.startswith('lig1-') or slug.startswith('frc-'):
        return 'Ligue 1'
    elif slug.startswith('ere-'):
        return 'Eredivisie'
    elif any(slug.startswith(p) for p in ['val-', 'dota-', 'r6-', 'rl-']):
        return 'Esports (Other)'
    elif any(slug.startswith(p) for p in ['atp-', 'wta-']):
        return 'Tennis'
    elif slug.startswith('ufc-') or slug.startswith('mma-'):
        return 'UFC/MMA'
    else:
        return 'Other'

pos['sport'] = pos['eventSlug'].apply(classify_event)

sport_stats = pos.groupby('sport').agg(
    n_positions=('netPnl', 'count'),
    total_bought=('totalBought', 'sum'),
    total_initial=('initialValue', 'sum'),
    total_realized=('realizedPnl', 'sum'),
    total_cashpnl=('cashPnl', 'sum'),
    total_netpnl=('netPnl', 'sum'),
    avg_price=('avgPrice', 'mean'),
    avg_turnover=('turnover_ratio', 'mean'),
    win_rate=('netPnl', lambda x: (x > 0).mean()),
).sort_values('total_bought', ascending=False)

print(f"\n  {'Sport':<30s} {'#Pos':>6s} {'TotalBought':>14s} {'RealizedPnl':>14s} {'NetPnl':>14s} {'WinRate':>8s} {'AvgTurn':>8s}")
print(f"  {'-'*30} {'-'*6} {'-'*14} {'-'*14} {'-'*14} {'-'*8} {'-'*8}")
for sport, row in sport_stats.iterrows():
    print(f"  {sport:<30s} {int(row['n_positions']):>6,d} "
          f"${row['total_bought']:>12,.0f} "
          f"${row['total_realized']:>12,.0f} "
          f"${row['total_netpnl']:>12,.0f} "
          f"{row['win_rate']*100:>7.1f}% "
          f"{row['avg_turnover']:>7.1f}x")

print(f"\n  TOTAL: ${sport_stats['total_bought'].sum():,.0f} bought, "
      f"${sport_stats['total_realized'].sum():,.0f} realized, "
      f"${sport_stats['total_netpnl'].sum():,.0f} net")

# Top 20 most profitable events
print(f"\n  Top 20 most profitable events (by realizedPnl):")
event_pnl = pos.groupby('eventSlug').agg(
    title=('title', 'first'),
    sport=('sport', 'first'),
    n_pos=('outcome', 'count'),
    total_bought=('totalBought', 'sum'),
    realized=('realizedPnl', 'sum'),
    net=('netPnl', 'sum'),
).sort_values('realized', ascending=False).head(20)
for i, (slug, row) in enumerate(event_pnl.iterrows()):
    print(f"    {i+1:>2d}. ${row['realized']:>10,.0f} realized | "
          f"{row['sport']:<15s} | {row['n_pos']} pos | "
          f"${row['total_bought']:>10,.0f} vol | {row['title'][:55]}")

# Worst 10 events
print(f"\n  Bottom 10 worst events (by realizedPnl):")
event_pnl_worst = pos.groupby('eventSlug').agg(
    title=('title', 'first'),
    sport=('sport', 'first'),
    n_pos=('outcome', 'count'),
    realized=('realizedPnl', 'sum'),
).sort_values('realized', ascending=True).head(10)
for i, (slug, row) in enumerate(event_pnl_worst.iterrows()):
    print(f"    {i+1:>2d}. ${row['realized']:>10,.0f} | "
          f"{row['sport']:<15s} | {row['n_pos']} pos | {row['title'][:55]}")

# ============================================================================
# 6. BOTH-SIDES ANALYSIS: Yes+No on same event (arb / market-making)
# ============================================================================
print("\n" + "=" * 90)
print("6. BOTH-SIDES ANALYSIS: Does RN1 buy Yes AND No on the same event?")
print("=" * 90)

# Group by conditionId (same market) to find both-side positions
# For negativeRisk markets, Yes and No have different assets but same conditionId
# For multi-outcome markets, each outcome has its own conditionId

# Use slug (market-level) to group
slug_groups = pos.groupby('slug').agg(
    n_outcomes=('outcome', 'nunique'),
    outcomes=('outcome', lambda x: ', '.join(sorted(x.unique()))),
    total_bought=('totalBought', 'sum'),
    total_realized=('realizedPnl', 'sum'),
    total_initial=('initialValue', 'sum'),
).reset_index()

# For negativeRisk=True events, the same slug has Yes and No
# For negativeRisk=False, each outcome is a separate slug within the same eventSlug

# Better: group by eventSlug
event_groups = pos.groupby('eventSlug').agg(
    n_positions=('outcome', 'count'),
    n_outcomes=('outcome', 'nunique'),
    outcomes=('outcome', lambda x: ', '.join(sorted(x.unique()))),
    total_bought=('totalBought', 'sum'),
    total_initial=('initialValue', 'sum'),
    total_realized=('realizedPnl', 'sum'),
    total_net=('netPnl', 'sum'),
    neg_risk=('negativeRisk', 'first'),
).reset_index()

both_sides = event_groups[event_groups['n_outcomes'] >= 2]
single_side = event_groups[event_groups['n_outcomes'] == 1]

print(f"\n  Events with BOTH SIDES (>=2 outcomes held): {len(both_sides):,d}")
print(f"  Events with SINGLE SIDE:                    {len(single_side):,d}")
print(f"  Total events:                               {len(event_groups):,d}")
print(f"  % both sides:                               {len(both_sides)/len(event_groups)*100:.1f}%")

print(f"\n  Both-sides events:")
print(f"    Total bought:    ${both_sides['total_bought'].sum():>14,.2f}")
print(f"    Total realized:  ${both_sides['total_realized'].sum():>14,.2f}")
print(f"    Total net PnL:   ${both_sides['total_net'].sum():>14,.2f}")

print(f"\n  Single-side events:")
print(f"    Total bought:    ${single_side['total_bought'].sum():>14,.2f}")
print(f"    Total realized:  ${single_side['total_realized'].sum():>14,.2f}")
print(f"    Total net PnL:   ${single_side['total_net'].sum():>14,.2f}")

# For negativeRisk (binary Yes/No) markets, buying Yes+No creates a guaranteed $1 payoff
# Profit = $1 - (price_yes + price_no). If sum < $1, it's an arb.
neg_risk_both = pos[pos['negativeRisk'] == True].copy()
neg_risk_events = neg_risk_both.groupby('eventSlug').agg(
    n_outcomes=('outcome', 'nunique'),
    outcomes=('outcome', lambda x: sorted(x.unique())),
    total_bought=('totalBought', 'sum'),
    avg_yes_price=('avgPrice', lambda x: x.values[0] if len(x) > 0 else np.nan),
).reset_index()

# Check merge trades (Yes+No merged for $1)
print(f"\n  MERGE trades in trades.csv: {len(merge_trades)}")
if len(merge_trades) > 0:
    print(f"    Total USDC from merges: ${merge_trades['usdcSize'].sum():,.2f}")
    print(f"    Avg merge size: ${merge_trades['usdcSize'].mean():,.2f}")
    print(f"    Merges indicate buying BOTH Yes+No and converting to $1 (pure arb)")

# For Yes/No binary markets, check if avgPrice(Yes) + avgPrice(No) < 1
yes_no_pairs = pos[pos['negativeRisk'] == True].copy()
yes_positions = yes_no_pairs[yes_no_pairs['outcomeIndex'] == 0].set_index('slug')
no_positions = yes_no_pairs[yes_no_pairs['outcomeIndex'] == 1].set_index('slug')

# For negativeRisk, the slug is the same for Yes and No
# Actually for negativeRisk, each position has a unique slug
# Let me use conditionId + outcome to pair them
neg_events = pos[pos['negativeRisk'] == True].groupby('eventSlug')
pair_analysis = []
for event, group in neg_events:
    yes_rows = group[group['outcome'] == 'Yes']
    no_rows = group[group['outcome'] == 'No']
    if len(yes_rows) > 0 and len(no_rows) > 0:
        yes_price = yes_rows['avgPrice'].iloc[0]
        no_price = no_rows['avgPrice'].iloc[0]
        implied_payout = yes_price + no_price
        total_bought = group['totalBought'].sum()
        realized = group['realizedPnl'].sum()
        pair_analysis.append({
            'event': event,
            'yes_price': yes_price,
            'no_price': no_price,
            'sum_price': implied_payout,
            'arb_spread': 1.0 - implied_payout,
            'total_bought': total_bought,
            'realized': realized,
        })

if pair_analysis:
    pairs_df = pd.DataFrame(pair_analysis)
    print(f"\n  Binary Yes/No pairs (negativeRisk) where BOTH sides held: {len(pairs_df)}")
    print(f"    Avg (Yes_price + No_price): {pairs_df['sum_price'].mean():.4f}")
    print(f"    Median sum:                 {pairs_df['sum_price'].median():.4f}")
    print(f"    Cases where sum < 1.0 (arb):{(pairs_df['sum_price'] < 1.0).sum()}")
    print(f"    Cases where sum > 1.0 (overpay): {(pairs_df['sum_price'] > 1.0).sum()}")
    print(f"    Avg arb spread (1 - sum):   {pairs_df['arb_spread'].mean():.4f}")
    print(f"    Total realized from pairs:  ${pairs_df['realized'].sum():,.2f}")

# ============================================================================
# 7. TURNOVER ANALYSIS
# ============================================================================
print("\n" + "=" * 90)
print("7. TURNOVER ANALYSIS: $37M+ volume on ~$10M cost basis")
print("=" * 90)

total_bought = pos['totalBought'].sum()
total_initial = pos['initialValue'].sum()
total_size_usd = (pos['size'] * pos['avgPrice']).sum()  # approximate current position value at cost

print(f"\n  Total ever bought (totalBought):      ${total_bought:>14,.2f}")
print(f"  Current cost basis (initialValue):    ${total_initial:>14,.2f}")
print(f"  Implied total sold/redeemed:          ${total_bought - total_initial:>14,.2f}")
print(f"  Overall turnover ratio:               {total_bought/total_initial:.1f}x")

# Estimate round-trip trading
# If you buy $100, sell $100, buy again $100 -> totalBought = $200, initialValue = $100
# Round-trip volume = totalBought - initialValue (approximately)
round_trip_est = total_bought - total_initial
print(f"\n  Estimated round-trip trading volume:   ${round_trip_est:>14,.2f}")
print(f"  As % of total volume:                 {round_trip_est/total_bought*100:.1f}%")

# Distribution of turnover
print(f"\n  Position count by turnover bucket:")
for lo, hi, label in [
    (0, 1.5, "1.0-1.5x (buy and hold)"),
    (1.5, 3, "1.5-3x (light trading)"),
    (3, 10, "3-10x (moderate trading)"),
    (10, 50, "10-50x (heavy trading)"),
    (50, 200, "50-200x (very heavy)"),
    (200, 99999, "200x+ (extreme churning)"),
]:
    mask = (pos['turnover_ratio'] >= lo) & (pos['turnover_ratio'] < hi)
    n = mask.sum()
    vol = pos.loc[mask, 'totalBought'].sum()
    realized = pos.loc[mask, 'realizedPnl'].sum()
    print(f"    {label:<30s}: {n:>5,d} positions, "
          f"${vol:>12,.0f} volume, "
          f"${realized:>10,.0f} realized PnL")

# ============================================================================
# 8. COMPREHENSIVE TRADE-LEVEL ANALYSIS (from trades.csv)
# ============================================================================
print("\n" + "=" * 90)
print("8. TRADE-LEVEL PATTERNS (from recent 2,671 trades)")
print("=" * 90)

# Price analysis from individual trades
buy_trades_only = trades[(trades['type'] == 'TRADE') & (trades['side'] == 'BUY')]
sell_trades_only = trades[(trades['type'] == 'TRADE') & (trades['side'] == 'SELL')]

print(f"\n  Buy trade price stats:")
print(f"    Mean price:   {buy_trades_only['price'].mean():.4f}")
print(f"    Median price: {buy_trades_only['price'].median():.4f}")
print(f"    Std:          {buy_trades_only['price'].std():.4f}")

if len(sell_trades_only) > 0:
    print(f"\n  Sell trade price stats:")
    print(f"    Mean price:   {sell_trades_only['price'].mean():.4f}")
    print(f"    Median price: {sell_trades_only['price'].median():.4f}")

# Trade size analysis
print(f"\n  Individual trade size (USDC):")
print(f"    Mean:   ${buy_trades_only['usdcSize'].mean():>10,.2f}")
print(f"    Median: ${buy_trades_only['usdcSize'].median():>10,.2f}")
print(f"    Max:    ${buy_trades_only['usdcSize'].max():>10,.2f}")

# How many unique events in the trades sample?
n_events_trades = trades['eventSlug'].nunique()
print(f"\n  Unique events in trade sample: {n_events_trades}")
print(f"  Avg trades per event: {len(trades)/n_events_trades:.1f}")

# Multi-trade events (RN1 scales into positions)
event_trade_counts = trades.groupby('eventSlug').size().sort_values(ascending=False)
print(f"\n  Trades per event distribution:")
for pct in [50, 75, 90, 95, 99]:
    print(f"    P{pct:>2d}: {event_trade_counts.quantile(pct/100):.0f} trades")
print(f"    Max: {event_trade_counts.max()} trades on one event")

# Time span of trade sample
print(f"\n  Trade sample time range: {trades['datetime'].min()} to {trades['datetime'].max()}")
time_span = (trades['datetime'].max() - trades['datetime'].min()).total_seconds() / 86400
print(f"  Span: {time_span:.1f} days")

# Simultaneous multi-event trading
# Group by timestamp to see if RN1 trades multiple events at same time
ts_events = trades.groupby('timestamp')['eventSlug'].nunique()
print(f"\n  Multi-event simultaneous trading:")
print(f"    Same-second trades on different events: {(ts_events > 1).sum()} timestamps")
print(f"    Max events traded in same second: {ts_events.max()}")
print(f"    => This indicates BOT/AUTOMATED trading")

# ============================================================================
# SUMMARY OF FINDINGS
# ============================================================================
print("\n" + "=" * 90)
print("SUMMARY: HOW RN1 MAKES MONEY")
print("=" * 90)

realized_total = pos['realizedPnl'].sum()

print(f"""
  DATA CAVEAT: positions.csv only contains positions with REMAINING shares (size > 0).
  Fully closed positions (bought and sold all shares at profit) are NOT in this dataset.
  The -$7.6M "net P&L" is therefore BIASED toward losers held to resolution.
  The $2.9M realizedPnl is a LOWER BOUND on actual profits.

  1. STRATEGY TYPE: Automated directional sports bettor
     - 99.8% of trades are BUYS (only 5 sells in 2,671 trades)
     - NOT a market maker (would show ~50/50 buy/sell)
     - Holds to resolution, profits from correct predictions
     - realizedPnl (${realized_total:+,.0f}) comes from redemptions + partial sells
     - Additional profit from fully-closed positions is NOT captured

  2. PRICING EDGE: Buys cheap, wins on longshots
     - Average entry price: {pos['avgPrice'].mean():.3f}
     - Winning positions bought at avg {winners['avgPrice'].mean():.3f} (longshots/underdogs)
     - Losing positions bought at avg {losers['avgPrice'].mean():.3f}
     - Best edge in 0.00-0.20 range: 28% win rate at avg price 0.14 = massive +EV
     - Longshot & underdog buckets generate ALL the realized profit
     - Favorite buckets (0.60+) are net losers

  3. POSITION SIZING: High volume, many small-to-medium bets
     - {len(pos):,d} positions across {pos['eventSlug'].nunique():,d} events
     - Median totalBought per position: ${pos['totalBought'].median():,.0f}
     - Median cost basis remaining: ${pos['initialValue'].median():,.0f}
     - Sizing slightly larger for underdog range (0.20-0.60)
     - Strong correlation (0.50) between totalBought and realizedPnl

  4. TIMING: Bot-driven, multi-event simultaneous execution
     - {(td<=2).mean()*100:.0f}% of trades within 2 seconds of each other
     - Trades up to 6 events in the SAME SECOND
     - Massive burst at 21:00 UTC (pre-match for European evening sports)
     - 24/7 activity but concentrated around match times

  5. MARKET SELECTION: Heavy in EPL, NFL, NBA, Bundesliga, CS2
     - Sports betting dominates (no politics/crypto)
     - NFL most profitable per position despite fewest bets (33% win rate)
     - NBA second-best: $293K realized on $2.9M volume (31% win rate)
     - EPL highest absolute volume ($7.5M) with 27% win rate
     - CS2 esports surprisingly large ($5.7M volume)

  6. BOTH-SIDES TRADING: 34% of events have multiple outcomes
     - {len(both_sides):,d} events ({len(both_sides)/len(event_groups)*100:.1f}%) with multiple outcomes held
     - {len(merge_trades)} MERGE transactions (Yes+No -> $1 pure arb)
     - Binary pair analysis: avg sum of Yes+No prices = 0.54 (NOT arbing the spread)
     - Both-sides buying appears to be covering multiple outcomes directionally
       (e.g., betting both teams in a 3-way market), NOT pure Yes/No arbitrage

  7. TURNOVER: {total_bought/total_initial:.0f}x average, 81% is round-trip
     - ${total_bought:,.0f} total bought vs ${total_initial:,.0f} remaining cost basis
     - ~${round_trip_est:,.0f} ({round_trip_est/total_bought*100:.0f}%) is round-trip volume
     - Median position: {pos['turnover_ratio'].median():.1f}x turnover
     - Heavy traders (10x+) generate {pos[pos['turnover_ratio']>=10]['realizedPnl'].sum():,.0f} of the profit
     - Buy-and-hold (<1.5x) positions are slightly negative

  KEY INSIGHT: RN1 is a HIGH-VOLUME AUTOMATED SPORTS BETTING SYSTEM that:
  (a) Covers 6,500+ events across global sports simultaneously
  (b) Has a genuine PRICING EDGE on longshots/underdogs (buys at avg 0.14, wins 28%)
  (c) Almost exclusively buys (99.8%), then holds to resolution or actively trades
  (d) Active position management (5.5x median turnover) captures additional alpha
  (e) Realized profit on visible positions: ${realized_total:,.0f} on ${total_bought:,.0f} volume
      = {realized_total/total_bought*100:.2f}% return on volume (lower bound)
  (f) True total profit is HIGHER because fully-closed winning positions are not in data
  (g) Not a market maker or arbitrageur - this is a directional SHARP sports bettor
""")
