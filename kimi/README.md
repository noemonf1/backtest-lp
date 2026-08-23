# Uniswap V3 LP Delta-Hedge Backtest — Complete Setup Guide

## Overview

This backtest answers: **Do Uniswap V3 LP fees cover the cost of delta-hedging with Binance perpetuals?**

### Files Included

| File | Purpose |
|------|---------|
| `uniswap_delta_hedge_backtest.py` | Core backtest engine + Binance fetcher |
| `uniswap_real_data_backtest.py` | Complete real-data pipeline (The Graph / Goldsky / Dune) |
| `uniswap_real_data_fetcher.py` | Standalone data fetchers for all sources |
| `dune_queries.sql` | Ready-to-run Dune SQL queries |
| `backtest_chart.png` | Example visualization |

---

## Quick Start (No API Keys)

```bash
pip install pandas numpy matplotlib requests
python uniswap_real_data_backtest.py
```

This runs with synthetic data to verify the engine works.

---

## Running with REAL Uniswap Data

You have 3 options for sourcing real pool data. Pick one:

### Option 1: The Graph (Recommended)

**Cost:** FREE (100,000 queries/month)
**Quality:** Best — official Uniswap subgraph, exact fees, tick-level data

1. Go to https://thegraph.com/studio/
2. Sign in with GitHub/Wallet
3. Create an API key (free, no credit card)
4. Paste it in `uniswap_real_data_backtest.py`:

```python
THEGRAPH_API_KEY = "your-api-key-here"
```

5. Run:
```python
from uniswap_real_data_backtest import run_full_backtest
results, summary = run_full_backtest("thegraph", days=90)
```

**What you get:** Hourly pool volume, exact fees accrued, TVL, price, tick

---

### Option 2: Goldsky (No API Key)

**Cost:** FREE starter tier
**Quality:** Good — community subgraphs, may have different schema

1. Find the public endpoint at https://app.goldsky.com/dashboard
2. Update `GOLDSKY_ENDPOINT` in the script
3. Run:

```python
results, summary = run_full_backtest("goldsky", days=90)
```

---

### Option 3: Dune Analytics

**Cost:** FREE (4,000 credits/month)
**Quality:** Excellent for custom analysis, position-level data

1. Get API key at https://dune.com/settings/api
2. Run the SQL queries in `dune_queries.sql`
3. Export CSV and load into the backtest:

```python
import pandas as pd
pool_data = pd.read_csv('dune_export.csv', parse_dates=['hour'], index_col='hour')
# Then pass pool_data directly to the backtest engine
```

---

## Understanding the Results

The backtest produces a DataFrame with these key columns:

| Column | Meaning |
|--------|---------|
| `price` | ETH price in USDC |
| `in_range` | Whether price is inside your LP range |
| `lp_value` | Mark-to-market of LP position |
| `delta_eth` | ETH exposure of LP (what you need to hedge) |
| `hedge_eth` | Your short position on Binance |
| `cum_fees` | Cumulative LP fees earned |
| `cum_funding` | Cumulative funding P&L (positive = you earned) |
| `cum_binance_fees` | Trading fees paid to Binance |
| `total_pnl` | Net P&L = LP + Hedge + Fees - Costs |

### Key Metric: `fees_cover_hedge`

```
Fees Earned:     $X
Hedge Costs:     $Y  (funding + binance fees + slippage)
Net:             $X - $Y

If $X > $Y  →  Fees cover hedge costs ✓
If $X < $Y  →  Hedge costs exceed fees ✗
```

---

## Critical Assumptions

1. **Pool Share**: The script estimates your share of fees based on `initial_capital / pool_TVL`. This is approximate. For exact fee attribution, you need your specific position's liquidity relative to the active tick's total liquidity.

2. **Rebalancing**: The hedge is rebalanced discretely (threshold or periodic). Between rebalances, you're exposed to delta drift (hedge error).

3. **Funding Rates**: Binance pays funding every 8 hours. Positive funding = longs pay shorts (you earn as a short). Negative funding = you pay.

4. **Slippage**: Set `slippage_bps` based on your trade size. $100K notional ≈ 1-2 bps on ETH perp.

---

## TypeScript Alternative

If you must use TypeScript:

```typescript
// Use viem for Uniswap math
import { TickMath, SqrtPriceMath } from '@uniswap/v3-sdk'
import { BigNumber } from 'ethers'

// Fetch from The Graph
const response = await fetch(THEGRAPH_ENDPOINT, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ query: POOL_HOUR_DATA_QUERY })
});
```

But **Python is strongly recommended** for quant backtesting. The pandas/numpy ecosystem is purpose-built for this.

---

## Troubleshooting

**"No data returned from The Graph"**
→ Check your API key. The hosted service is deprecated; you MUST use the decentralized network with a key.

**"Binance API timeout"**
→ Add retries with exponential backoff. Binance rate-limits to 1,200 request weight/minute.

**"Fees seem too low"**
→ Your `our_share_of_pool` estimate might be too low. Check actual pool TVL on [Uniswap Info](https://info.uniswap.org/#/pools/0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640).

**"Price gaps between Uniswap and Binance"**
→ Normal. Use Binance close for hedge mark-to-market, Uniswap sqrtPriceX96 for LP valuation. The spread is usually <0.1%.

---

## Next Steps

1. Get a The Graph API key (free)
2. Run `run_full_backtest("thegraph", days=90)`
3. Adjust `lower_price`, `upper_price`, `initial_capital_usd`
4. Try different rebalance thresholds (0.02, 0.05, 0.10)
5. Add margin/liquidation modeling for production use
