#!/usr/bin/env python3
"""Assess paper trading readiness for live."""
import sys, json, re
from datetime import datetime, timezone

data = json.load(sys.stdin)
positions = data.get("positions", data) if isinstance(data, dict) else data
print(f"Total open: {len(positions)}")

sports = {}
for p in positions:
    s = p.get("sport", "?")
    sports[s] = sports.get(s, 0) + 1
print("By sport:")
for s, c in sorted(sports.items(), key=lambda x: -x[1]):
    print(f"  {s}: {c}")

edges = [float(p.get("edge_pct", 0)) for p in positions]
edges.sort(reverse=True)
print(f"Edge range: {min(edges):.1f}% to {max(edges):.1f}%")
print(f"Median edge: {edges[len(edges)//2]:.1f}%")

costs = [float(p.get("cost_usdc", 0)) for p in positions]
print(f"Position sizes: ${min(costs):.0f} - ${max(costs):.0f}, avg ${sum(costs)/len(costs):.1f}")
print(f"Total exposure: ${sum(costs):.0f}")

now = datetime.now(timezone.utc)
days_out = []
for p in positions:
    slug = p.get("slug", "")
    m = re.search(r"(\d{4}-\d{2}-\d{2})", slug)
    if m:
        try:
            dt = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            days_out.append((dt - now).days)
        except:
            pass
if days_out:
    print(f"Days to event: {min(days_out)} to {max(days_out)}, median {sorted(days_out)[len(days_out)//2]}")

mtm = data.get("total_mtm_pnl", "N/A") if isinstance(data, dict) else "N/A"
print(f"Unrealized PnL: {mtm}")

# High edge analysis
high = [p for p in positions if float(p.get("edge_pct", 0)) > 10]
print(f"\nHigh edge (>10%): {len(high)} positions")
for p in high:
    print(f"  {p.get('slug','')[:45]}  edge={float(p.get('edge_pct',0)):.1f}%  ${float(p.get('cost_usdc',0)):.0f}")
