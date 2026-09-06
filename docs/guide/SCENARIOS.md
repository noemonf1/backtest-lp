# SCENARIOS

Phase 3 record. Every run below was executed on the real August 2026 files described in DATA.md, from Python, with `run_claude.run_backtest` through the same `backtest._to_claude_config` adapter the app uses (`docs/guide/raw/run_scenarios.py`), or `backtest.run_hourly` with inputs built the way `app.py` builds them (`docs/guide/raw/run_hourly_stress.py`). Per run: `docs/guide/raw/<scenario>/summary.json`, `timeseries.csv.gz` (swap_level: the full 60-second grid frame with every column of `run_claude.run_backtest`'s `df`) or `timeseries.csv` (hourly), and `diag.json`. All swap_level runs are indexed in `raw/scenarios_index.json`; hourly runs in `raw/scenarios_hourly_index.json`. Charts are in `docs/guide/charts/`.

Conventions. USD figures are totals over the 31-day window. APR figures are the engine's: total / capital base / (31/365) × 100, where capital base = LP capital + hedge margin (run_claude.py:719-726). `lvr_usd` is the engine's per-swap LVR measured against the CEX mark; its sign is discussed in section 1 because on the 1-minute-kline mark it comes out negative. `net_usd` is the path-exact number (run_claude.py:699-707) and never depends on `lvr_usd`.

## 1. Default run

Inputs: `data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet` (159,990 swaps), `data/ETHUSDT-klines-1m-2026-08.csv` resampled to 1 s (2,678,341 rows), `data/ETHUSDT-fundingRate-2026-08.csv` (93 prints). All 24 `Assumptions` fields at their defaults: capital 1,000,000; range ±15%; hedge band 3% of max delta; grid 60 s; taker 4.5 bps; half-spread 0.5 bps; impact 0.8 bps per 100k; leverage 4; margin buffer 1.5; reposition on, buffer 6 h, gas 40, re-ratio 5 bps; fee tier 0.05%; markout 0.

### The nine metrics, direct call and UI, reconciled

| Metric tile | Direct `run_claude.run_backtest` (`raw/default/summary.json`) | AppTest, `Run backtest` clicked (`raw/apptest/default_ui_metrics.json`) | Field |
|---|---|---|---|
| Net PnL (USD) | −11,592 | `$-11,592` | `net_usd` |
| Fees APR | 28.46 | `28.46%` | `fee_apr_pct` |
| LVR APR | 4.06 | `4.06%` | `lvr_apr_pct` = apr(−lvr_usd) |
| Funding APR | 1.47 | `1.47%` (delta text "collected (perp paid you)") | `funding_apr_pct` |
| Hedge cost APR | −3.48 | `-3.48%` | `hedge_cost_apr_pct` |
| Fees / LVR | NaN | `nan` | `fee_over_lvr` (NaN because `lvr_usd` ≤ 0, run_claude.py:750) |
| % time in range | 98.2 | `98.2%` | `pct_time_in_range` |
| Sharpe (daily) | −5.55 | `-5.55` | `sharpe` |
| Max drawdown | −13,161 | `$-13,161` | `max_drawdown_usd` |

The verdict banner in the UI reads `FEES DO NOT COVER THE COST — net -11.39% APR · engine: swap_level · 31 days` and the caption `Capital base = LP $1,000,000 + hedge margin $198,120 = $1,198,120` (`raw/apptest/default_ui.txt`). `bt.run_swap_level` (the adapter) and the direct call produce the same summary in every field; the only inequality is `NaN != NaN` on `fee_over_lvr` (`raw/default/adapter_vs_direct.json`).

### The full summary

| Field | Value | Field | Value |
|---|---|---|---|
| days | 31.0 | fees_usd | 28,956 |
| capital_lp_usd | 1,000,000 | lvr_usd | −4,134 |
| margin_usd | 198,120 | funding_usd | 1,495 |
| capital_base_usd | 1,198,120 | hedge_cost_usd | 3,540 |
| range_width | 0.15 | gas_reposition_usd | 540 |
| hedge_band | 0.03 | net_usd | −11,592 |
| n_repositions | 1 | fee_apr_pct | 28.46 |
| n_hedge_trades | 150 | lvr_apr_pct | 4.06 |
| pct_time_in_range | 98.2 | funding_apr_pct | 1.47 |
| mean_liquidity_share_pct | 2.655 | hedge_cost_apr_pct | −3.48 |
| sharpe | −5.55 | net_apr_pct | −11.39 |
| max_drawdown_usd | −13,161 | discrete_hedge_error_usd | −42,097 |
| closed_form_lvr_usd | 37,263 | | |

### What the −11,592 is made of (`raw/default/diag.json`)

| Term (run_claude.py:699-707) | USD |
|---|---|
| LP inventory value change, `lp_pnl_usd` (position marked at the perp price, includes the reposition write-down) | +63,475 |
| Perp hedge mark-to-market, `hedge_pnl_usd` | −101,437 |
| Fees, `fee_usd` | +28,956 |
| Funding, `funding_usd` | +1,495 |
| Hedge execution, `hedge_cost_usd` (3,178 spread and taker fee, 362 impact) | −3,540 |
| Gas and re-ratio on reposition, `gas_usd` | −540 |
| **net_usd** | **−11,592** |

The hedged inventory alone lost 37,962 USD (63,475 − 101,437). That is the gamma cost of the position over a month in which ETH went from 1,862.68 to 2,466.53 with 51.8% realised 1-minute vol. Fees of 28,956 did not cover it.

### The LVR sign

The engine's `lvr_usd` came out at −4,134, and the app shows `LVR APR 4.06%` as if adverse selection had been a gain. It was not. The cause is the CEX mark. `load_cex_prices` (run_claude.py:296-315) builds the 1-second series from the kline `close` stamped at `close_time`, then forward-fills. A swap at second 17 of a minute is marked against the previous minute's close, up to 59 seconds stale. Arbitrage swaps move the pool to the new price, so against a stale mark they look like taker losses. Three runs remove the staleness and agree with each other and with the closed form:

| Run | `lvr_usd` | `discrete_hedge_error_usd` (= net − decomposition) | Raw |
|---|---|---|---|
| default, 1m klines, markout 0 | −4,134 | −42,097 | `raw/default` |
| 1m klines, markout 60 s | +36,655 | −1,307 | `raw/markout_60` |
| 1m klines, markout 300 s | +35,024 | −2,939 | `raw/markout_300` |
| aggTrades resampled to 1 s, markout 0 | +34,360 | −3,602 | `raw/default_aggtrades` |
| closed form ½σ²P²abs(V'') integral, σ from the 60 s grid | 37,263 | | `closed_form_lvr_usd` |
| hedged inventory loss, path-exact | 37,962 | | `lp_pnl_usd + hedge_pnl_usd` |

`net_usd` is −11,601 in all three corrected runs against −11,592 in the default: the path-exact P&L does not use `lvr_usd`, so the headline is unaffected. What changes is the attribution and the two metric tiles that depend on it. Chart `charts/lvr_reconciliation.png`.

The app's default `CEX price file` pick is the aggTrades file when present (DATA.md section 7), which gives the corrected attribution. Loading it takes 61 s and 2.2 GB of CSV.

### Funding file default

The app's default `Funding file` is `(none — assume 0)`. With it: `net_usd` −13,086, `net_apr_pct` −12.86, funding 0 (`raw/default_nofunding`). The short collected 1,495 USD in August at a mean rate of 5.19e-5 per 8 h.

## 2. Hourly engine, `Local CSV files` mode

`app._load_hourly_inputs` (app.py:1118-1153) resamples the 1-second CEX series into 1-hour OHLC bars and sets `volume = quote_volume = 0.0` (app.py:1137-1138). `backtest.run_hourly` only creates `pool_volume_24h` when `quote_volume` exists (backtest.py:442-446), so it does, at zero. The kimi engine's fee line is `vol × pool_fee_rate × our_share_of_pool` (kimi/uniswap_delta_hedge_backtest.py:348). Fees are zero in this mode by construction.

| Run | fees | funding | hedge cost | net (as reported) | net APR | trades | in range | Raw |
|---|---|---|---|---|---|---|---|---|
| Local CSV path, app verbatim | 0 | 871 | 980 | +69,274 | 84.34% | 339 | 60.8% | `raw/hourly_localcsv` |
| real 1h klines with `quote_volume`, `pool_share` 0.001 (default) | 3,725 | 871 | 980 | +72,999 | 88.88% | 339 | 60.8% | `raw/hourly_1hklines_default` |
| same, `pool_share` = 0.02655 (the measured mean share of the $1M position, section 1) | 98,896 | 871 | 980 | +168,169 | 204.75% | 339 | 60.8% | `raw/hourly_1hklines_measured` |
| same, `pool_share` 0.02655 and `pool_volume_multiplier` = 0.01023 (measured pool/Binance volume ratio) | 12,650 | 871 | 980 | +81,923 | 99.74% | 339 | 60.8% | `raw/hourly_1hklines_measured_ratio` |

`pool_share` default 0.001 against the measured 0.02655: the default understates the $1M position's share by 27×. The default `pool_volume_multiplier` of 0.08 against the measured ratio of 0.01023 (pool volume 2,422,599,886 USD; Binance 1h `quote_volume` sum 236,750,172,916 USD): the default overstates pool volume by 7.8×. The two errors partly cancel (3,725 versus a fully measured 12,650). The swap-level engine's fee figure on the same tape is 28,956; the hourly engine has no notion of in-range liquidity varying by hour, no fee-net execution, and no LVR.

**The reported net is wrong in sign.** `raw/hourly_hedge_sign_check.txt`: the engine books realised hedge P&L as `-sign(hedge_eth) * close_size * (price - hedge_avg_price)` (kimi/uniswap_delta_hedge_backtest.py:393). For a short (`hedge_eth` < 0) that is `+close_size × (price − avg)`, a profit when the short is bought back above its average entry. The first reducing trade, 2026-08-02 01:00, buys back 19.33 ETH at 1,856.29 against an average of 1,861.05 and books −92.03 USD; the correct entry is +92.03. Over the month, with ETH up 32%, the engine reports hedge P&L +106,112 USD on a short; the correct figure from `Σ hedge_eth[i−1] × (p[i] − p[i−1])` is −45,646. With the correct hedge P&L the `Local CSV` net is −82,485 USD, not +69,274. The engine's unrealised leg (line 419) has the right sign, so the error appears only when trades reduce the position. The swap_level engine marks the hedge as `hedge[:-1] × dp` (run_claude.py:631) and has no such term.

A second defect shows only in the charts: the per-bar `binance_fees` and `slippage_cost` columns hold the cumulative total on every rebalance bar (line 445, 447), and `_normalise_hourly` sums them into `hedge_cost_usd` for the timeseries (backtest.py:366). The timeseries `hedge_cost_usd` column sums to 312,394 while the summary says 980. The `PnL attribution` charts for the hourly engine therefore show a hedge cost 319× too large; the metric tiles use the summary and are right.

Also: `days` is 30 (kimi counts `(last − first) / 24 h` on 744 bars), `hedge_band` reported is the kimi `rebalance_threshold` 0.05, `lvr_usd` is 0 by construction, `sharpe` is `None`, `capital_base_usd` is the LP capital with no margin (backtest.py:373, leverage and margin buffer are not passed to this engine).

## 3. Range width

Chart `charts/sweep_range.png`. Hedge band 3%, capital 1,000,000, everything else default.

| range | fees | lvr (klines mark) | funding | hedge cost | gas | net | net APR | trades | repositions | in range | share | margin |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| ±2% | 120,556 | −8,939 | 2,111 | 84,110 | 3,780 | −129,455 | −110.75% | 3,549 | 7 | 82.3% | 14.70% | 376,321 |
| ±5% | 71,555 | −6,431 | 2,303 | 23,542 | 1,620 | −45,228 | −38.76% | 1,075 | 3 | 93.2% | 7.11% | 373,800 |
| ±10% | 37,330 | −4,740 | 1,504 | 6,713 | 1,080 | −14,513 | −13.53% | 292 | 2 | 90.1% | 3.78% | 262,701 |
| ±15% | 28,956 | −4,134 | 1,495 | 3,540 | 540 | −11,592 | −11.39% | 150 | 1 | 98.2% | 2.65% | 198,120 |
| ±25% | 18,730 | −5,530 | 1,837 | 1,800 | 540 | −7,275 | −7.28% | 68 | 1 | 98.3% | 1.65% | 177,167 |
| ±50% | 10,262 | −3,198 | 1,287 | 342 | 0 | −2,436 | −2.49% | 13 | 0 | 100.0% | 0.89% | 149,757 |

Closed-form LVR by width: 240,399 / 105,348 / 59,048 / 37,263 / 22,578 / 11,779. Narrowing the range raises L for the same capital (±2% gives 5.5× the liquidity share of ±15%), so fees scale up 4.2×, but the gamma cost scales up 6.5× and the hedge turnover 24×. Every width loses. The ranking is a property of the strategy: for a fixed fee take on flow, concentrating liquidity raises the fee stream and the gamma cost together, and the gamma cost won at August's realised vol. The size of the loss is a property of the 31 days: a +32% trend with a 3.5% single-minute move is close to the worst tape for a short-gamma position with a ±15% strangle, and 7 repositions at ±2% each crystallised the loss at the band. What the data cannot say is whether a flat, low-vol month would flip the sign; the weekly sub-windows in section 10 are the only evidence, and the two calm weeks were flat, not profitable.

## 4. Hedge band

Chart `charts/sweep_band.png`, `charts/sweep_band_trades_cost.png`.

| band | trades | turnover (ETH) | hedge cost | funding | net | net APR | max drawdown | Sharpe |
|---|---|---|---|---|---|---|---|---|
| 0.5% | 3,218 | | 13,390 | 1,483 | −21,367 | −21.02% | −21,487 | −13.00 |
| 1% | 1,094 | | 8,439 | 1,484 | −16,633 | −16.34% | −16,768 | −10.57 |
| 2% | 312 | | 4,873 | 1,491 | −12,670 | −12.50% | −13,322 | −7.30 |
| 3% | 150 | 2,846 | 3,540 | 1,495 | −11,592 | −11.39% | −13,161 | −5.55 |
| 5% | 63 | | 2,509 | 1,518 | −11,299 | −11.14% | −13,278 | −4.60 |
| 10% | 15 | | 1,492 | 1,470 | −9,327 | −9.27% | −17,026 | −2.65 |

Fees, LVR and gas do not move (28,956 / −4,134 / 540 in every row), because the band only touches the hedge loop (run_claude.py:610-620). The band is a fraction of `max_delta` (561 ETH at t0), so 3% is a 16.8 ETH dead zone around the target hedge; at 0.5% the dead zone is 2.8 ETH and the engine trades 21× as often for 3.8× the cost. Net improves monotonically as the band widens, out to 10% where the drawdown gets worse (−17,026) because the position runs up to 56 ETH unhedged. This is the rehedge-frequency trade-off of any options book, and its shape is a property of the strategy. Where the optimum sits is a property of the tape: with 0.8 bps of impact per 100k the execution cost is nearly linear in turnover, and turnover fell by 3,218/15 = 215× across the grid while the unhedged gamma cost moved by 4,000 USD, so August rewarded the widest band tested.

## 5. Hedge grid seconds

Chart `charts/sweep_grid.png`.

| grid | trades | hedge cost | funding | net | net APR | closed-form LVR | Sharpe |
|---|---|---|---|---|---|---|---|
| 10 s | 150 | 3,540 | 1,463 | −11,623 | −11.42% | 0 | −5.57 |
| 30 s | 150 | 3,540 | 1,463 | −11,623 | −11.42% | 0 | −5.57 |
| 60 s | 150 | 3,540 | 1,495 | −11,592 | −11.39% | 37,263 | −5.55 |
| 120 s | 135 | 3,359 | 1,512 | −9,792 | −9.64% | 38,742 | −4.28 |
| 300 s | 129 | 3,221 | 1,509 | −7,910 | −7.76% | 36,852 | −5.76 |

The 10 s and 30 s rows equal each other and match 60 s to within 31 USD. The CEX tape is 1-minute klines: the price only changes once a minute, so a finer grid sees the same 44,640 distinct prices and makes the same 150 trades. The 31 USD difference is funding: the 8-hour stamps land on a different grid row and are multiplied by a hedge position that differs by one trade. `closed_form_lvr_usd` is 0 at 10 s and 30 s because `np.gradient` of delta against price returns NaN wherever consecutive grid prices repeat (run_claude.py:788-790), which is 5 of 6 steps at 10 s; `nansum` then sums nothing. Coarser grids (120 s, 300 s) hedge less often and lose less on this tape because the hedge decision is also the fee-aggregation and LP-mark interval (run_claude.py:679, 596), so the path-exact loss is sampled at coarser points. These rows are a property of the data resolution, not of the strategy: with the aggTrades tape a 10 s grid would see a different path.

## 6. Fee tier

Chart `charts/sweep_fee.png`.

| fee tier | fees | lvr (klines mark) | net | net APR | discrete_hedge_error | Sharpe |
|---|---|---|---|---|---|---|
| 0.01% | 5,791 | −27,299 | −34,756 | −34.16% | −65,261 | −12.95 |
| 0.05% (the pool's) | 28,956 | −4,134 | −11,592 | −11.39% | −42,097 | −5.55 |
| 0.30% | 173,735 | 140,645 | +133,187 | +130.89% | +102,682 | 21.40 |
| 1.00% | 579,116 | 546,026 | +538,568 | +529.27% | +508,064 | 21.74 |

This sweep is not economically coherent, and the numbers show why. The swap tape is the flow that chose to trade against a 0.05% pool. `attribute_swaps` charges `fee_tier` on the gross input of every one of those swaps (run_claude.py:511-513) and computes the fee-net execution price with the same tier (517-518). At 1.00% it books 579,116 USD of fees on 2.34 billion USD of in-range volume that would not have routed through a 1% pool, and the fee-net execution price it imputes is 95 bps away from the price the taker actually got, so `lvr_usd` jumps to 546,026 and the decomposition gap to 508,064. Fees minus LVR stays at 33,090 in every row because that difference is the taker's markout against the price actually paid (run_claude.py:525-531), which the tier cannot change. The one consistent reading is the pool's own tier. A trader who wants the 0.30% pool's economics needs the 0.30% pool's swap tape (`0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8`), where both the flow and the liquidity are different.

## 7. Capital

Chart `charts/sweep_capital.png`.

| capital | share (mean, in range) | fees | fee APR | hedge cost | of which impact | net | net APR | margin | capital base |
|---|---|---|---|---|---|---|---|---|---|
| 100k | 0.273% | 2,978 | 29.27% | 321 | | −1,115 | −10.96% | 19,812 | 119,812 |
| 300k | 0.814% | 8,879 | 29.09% | 986 | | −3,265 | −10.69% | 59,436 | 359,436 |
| 1M | 2.655% | 28,956 | 28.46% | 3,540 | 362 | −11,592 | −11.39% | 198,120 | 1,198,120 |
| 3M | 7.499% | 81,842 | 26.81% | 12,791 | | −41,816 | −13.70% | 594,360 | 3,594,360 |
| 10M | 20.817% | 227,868 | 22.39% | 67,982 | 36,204 | −209,481 | −20.59% | 1,981,201 | 11,981,201 |

Fee APR falls from 29.3% to 22.4% across two decades of capital because the position dilutes itself: share = L_you / (L_active + L_you) (run_claude.py:506), and at 10M the position is a fifth of the pool. Hedge cost grows faster than linearly because of the quadratic impact term `notional × (notional/1e5) × 0.8 bps` (run_claude.py:625): at 1M the mean clip is 42,369 USD and impact is 362 of 3,540; at 10M the mean clip is 423,696 USD, the largest 4,418,729 USD, and impact is 36,204 of 67,982. Trade count is 150 at every size because the band is a fraction of max delta. The dilution curve is a property of the strategy and of this pool's depth (mean in-range liquidity 8.8e18 in August); the impact curve is the assumption 0.8 bps per 100k, not a measurement. Neither size is profitable on this tape.

## 8. Leverage

Chart `charts/sweep_leverage.png`.

| leverage | margin | capital base | net (USD) | net APR | fee APR |
|---|---|---|---|---|---|
| 1x | 792,480 | 1,792,480 | −11,592 | −7.61% | 19.02% |
| 2x | 396,240 | 1,396,240 | −11,592 | −9.78% | 24.42% |
| 3x | 264,160 | 1,264,160 | −11,592 | −10.80% | 26.97% |
| 5x | 158,496 | 1,158,496 | −11,592 | −11.78% | 29.43% |
| 10x | 79,248 | 1,079,248 | −11,592 | −12.65% | 31.59% |

Leverage enters exactly once: `margin = max_notional / leverage × margin_buffer` (run_claude.py:720), with `max_notional` = 528,320 USD (the largest `|hedge| × price` on the grid). Every USD figure is identical across the rows. Only the denominator moves, so a losing strategy looks worse with more leverage and a winning one better. No liquidation logic, no funding on margin, no margin call is modelled.

## 9. Reposition

Chart `charts/reposition_on_off.png`.

| | fees | lvr (klines) | funding | hedge cost | gas | net | net APR | in range | trades | closed-form LVR |
|---|---|---|---|---|---|---|---|---|---|---|
| on (default) | 28,956 | −4,134 | 1,495 | 3,540 | 540 | −11,592 | −11.39% | 98.2% | 150 | 37,263 |
| off | 8,846 | 782 | 1,044 | 1,085 | 0 | −8,812 | −8.66% | 44.5% | 54 | 21,445 |

ETH crossed the upper bound 2,143.12 on Aug 19 and stayed above it; the engine repositioned at 2026-08-20 02:50 (6 hours out of range, run_claude.py:557-575) at 2,251.02 into a new band 1,912 to 2,589. With reposition off, the position sat fully in USDC above the band for 55.5% of the swaps, earned no fees, carried no delta and no hedge, and lost less. On this tape the re-entry bought a second month of short gamma at a higher strike. That is a property of the data: the reposition rule is a momentum re-entry, and August trended. The rule itself is a property of the strategy and is the only reason the position stayed 98% in range.

Reposition cost accounting. The schedule writes the capital down at the reposition: value at exit 1,033,724.8 USD, minus 5 bps (516.9) and 40 USD gas, giving 1,033,167.9 for the new position (run_claude.py:568-572, `raw/default/diag.json` `positions`). That write-down flows into `lp_pnl_usd` (the marked value of the new, smaller position). `run_backtest` then adds `gas_usd` = 40 + 1,000,000 × 5 bps = 540 on the same grid row (run_claude.py:690-695) and subtracts it again in `net_usd` (706). The reposition is charged 1,096.9 USD in `net_usd` for a 556.9 USD cost. On the default run the double count is 540 USD of the −11,592.

## 10. Sub-windows and the app's rolling and by-month panels

Weekly, chart `charts/weekly_usd.png`, `charts/weekly_apr.png`. Each week is clipped the way `Backtest window` clips (app.py:1028-1042) and run as a 7-day backtest; the app would show `INSUFFICIENT WINDOW (7d < 30d)` on each.

| week | swaps | fees | lvr (klines) | funding | hedge cost | gas | net | net APR | in range | trades | closed-form LVR | ETH move |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Aug 1-7 | 29,289 | 3,576 | 434 | 324 | 365 | 0 | +29 | +0.13% | 100% | 20 | 4,132 | flat |
| Aug 8-14 | 25,236 | 2,664 | 616 | 463 | 275 | 0 | +312 | +1.35% | 100% | 16 | 2,314 | flat |
| Aug 15-21 | 36,170 | 8,388 | −2,399 | 431 | 1,540 | 540 | −7,778 | −34.00% | 91.9% | 51 | 13,807 | +19% and out of range |
| Aug 22-28 | 49,769 | 10,751 | −2,377 | 841 | 1,205 | 0 | −3,667 | −15.44% | 100% | 60 | 13,726 | trend continues |

(Swap counts per week are from the clipped inputs, `raw/scenarios_index.json` notes.) Two calm weeks earned 3,000 to 3,600 of fees against 2,300 to 4,100 of gamma cost and ended within 312 USD of flat. The week of Aug 15-21 contains the 3.48% minute, the exit through the upper bound and the reposition, and lost 7,778. This is the clearest statement the month makes: the position is a short strangle that collects roughly 500 USD a day in fees, and one trending week costs two and a half months of that.

30-day rolling (AppTest, `raw/apptest/default_ui_rolling_bymonth.txt`): with 31 days of data the rolling window has 2 points. The app shows min −11.62%, p25 −11.60%, median −11.59%, p75 −11.58%, max −11.57%, std 0.03. It is the same number twice.

Backtest by month (same file): `Loaded data spans only 1 month — need at least 2 for a monthly breakdown. Widen the backtest window.` (app.py:1699-1705). With one month of data the panel produces no runs.

## 11. Stress shock (hourly engine only)

`stress_cfg` is applied only in the hourly branch of `_execute_run` (app.py:1944-1954). It rewrites the OHLC columns of the last N days of the bar frame (app.py:1876-1905) and then runs the hourly engine. The swap tape and the fee flow are untouched, so there is no swap_level stress. Chart `charts/stress_tape.png`, `charts/hourly_runs.png`.

| run | bars shocked | last close | fees | funding | hedge cost | net (as reported, with the sign error of section 2) | net APR | trades | in range |
|---|---|---|---|---|---|---|---|---|---|
| unshocked Local CSV | 0 | 2,466.53 | 0 | 871 | 980 | +69,274 | 84.34% | 339 | 60.8% |
| sidebar defaults: −20%, linear, last 7 days | 169 | 1,973.22 (min 1,835.75) | 0 | 920 | 1,260 | +65,320 | 79.53% | 313 | 69.8% |
| −30%, linear, last 1 day | 25 | 1,726.57 | 0 | 909 | 1,329 | +63,269 | 77.03% | 339 | 62.8% |
| −30%, step, last 1 day | 25 | 1,726.57 (min 1,684.89) | 0 | 972 | 1,358 | −15,947 | −19.42% | 317 | 64.1% |

Through the UI (`raw/shots_local_hourly_metrics.txt`, screenshots 40 and 43) the same two linear runs show net $65,194 (79.37% APR, hedge cost APR −1.69%) and $63,136 (76.87%, −1.78%). The difference from the Python rows is the taker fee: the sidebar's `Binance taker fee (bps)` widget writes `binance_taker_fee = 4.5 / 10,000` on every render (app.py:648), while `bt.Assumptions()` defaults it to 0.0004. Rerunning with `binance_taker_fee=0.00045` gives 65,194 and 63,136 exactly (`raw/hourly_stress_ui_taker_check.txt`). The swap_level engine is unaffected because it reads `taker_fee_bps`, which is 4.5 in both places.

A 30% drop over one day at the end of the tape pushes ETH back inside the ±15% band (1,584 to 2,144) and below it in the step case. The linear and step cases differ by 79,216 USD of reported net for the same end price because of the section 2 sign error: the linear ramp lets the engine reduce the short in 24 steps and book each reduction with the wrong sign, the step does it in one bar. The stress panel is a synthetic overlay on the weaker engine, and its output carries that engine's defect.

## 12. The app's own sweep

`Run range × hedge-band sweep` checked, defaults `hedge_band` = 0.005, 0.01, 0.02, 0.05, 0.1 across `range_width` = 0.02, 0.05, 0.10, 0.15, 0.25, 0.50 (backtest.py:182-195), 30 cells, run through AppTest in 15.8 s of engine time (`raw/apptest/default_ui_sweep.txt`). The rendered `Full sweep table`:

| hedge_band | range_width | fee_apr | lvr_apr | funding_apr | hedge_cost_apr | net_apr | in_range_pct | sharpe | n_hedges |
|---|---|---|---|---|---|---|---|---|---|
| 0.005 | 0.02 | 103.14 | 7.65 | 1.80 | −139.67 | −178.07 | 82.3 | −32.53 | 21,664 |
| 0.010 | 0.02 | 103.14 | 7.65 | 1.80 | −119.62 | −157.97 | 82.3 | −29.18 | 13,174 |
| 0.020 | 0.02 | 103.14 | 7.65 | 1.80 | −89.75 | −129.28 | 82.3 | −24.96 | 6,175 |
| 0.050 | 0.02 | 103.14 | 7.65 | 1.81 | −51.26 | −90.64 | 82.3 | −19.23 | 1,596 |
| 0.100 | 0.02 | 103.14 | 7.65 | 1.83 | −30.61 | −65.83 | 82.3 | −14.31 | 469 |
| 0.005 | 0.05 | 61.33 | 5.51 | 1.97 | −53.77 | −71.57 | 93.2 | −20.40 | 12,210 |
| 0.010 | 0.05 | 61.33 | 5.51 | 1.97 | −39.88 | −58.25 | 93.2 | −17.24 | 5,542 |
| 0.020 | 0.05 | 61.33 | 5.51 | 1.97 | −26.13 | −42.92 | 93.2 | −13.89 | 2,029 |
| 0.050 | 0.05 | 61.33 | 5.51 | 2.00 | −13.89 | −32.37 | 93.2 | −9.93 | 442 |
| 0.100 | 0.05 | 61.37 | 5.52 | 2.02 | −8.39 | −26.82 | 93.2 | −7.79 | 122 |
| 0.005 | 0.10 | 34.87 | 4.43 | 1.40 | −21.37 | −29.51 | 90.1 | −16.53 | 5,278 |
| 0.010 | 0.10 | 34.85 | 4.43 | 1.40 | −13.85 | −21.36 | 90.1 | −13.36 | 1,911 |
| 0.020 | 0.10 | 34.84 | 4.42 | 1.40 | −8.12 | −14.74 | 90.1 | −9.27 | 568 |
| 0.050 | 0.10 | 35.12 | 4.46 | 1.45 | −4.45 | −12.25 | 90.1 | −6.10 | 117 |
| 0.100 | 0.10 | 35.65 | 4.53 | 1.52 | −2.61 | −7.45 | 90.1 | −2.86 | 29 |
| 0.005 | 0.15 | 28.49 | 4.07 | 1.46 | −13.17 | −21.02 | 98.2 | −13.00 | 3,218 |
| 0.010 | 0.15 | 28.44 | 4.06 | 1.46 | −8.29 | −16.34 | 98.2 | −10.57 | 1,094 |
| 0.020 | 0.15 | 28.57 | 4.08 | 1.47 | −4.81 | −12.50 | 98.2 | −7.30 | 312 |
| 0.050 | 0.15 | 28.56 | 4.08 | 1.50 | −2.47 | −11.14 | 98.2 | −4.60 | 63 |
| 0.100 | 0.15 | 28.77 | 4.11 | 1.46 | −1.48 | −9.27 | 98.2 | −2.65 | 15 |
| 0.005 | 0.25 | 18.70 | 5.52 | 1.83 | −5.93 | −10.78 | 98.3 | −8.24 | 1,422 |
| 0.010 | 0.25 | 18.71 | 5.52 | 1.83 | −3.56 | −7.89 | 98.3 | −6.70 | 427 |
| 0.020 | 0.25 | 18.73 | 5.53 | 1.84 | −2.38 | −10.70 | 98.3 | −5.97 | 137 |
| 0.050 | 0.25 | 18.70 | 5.52 | 1.84 | −1.23 | −8.63 | 98.3 | −3.34 | 25 |
| 0.100 | 0.25 | 18.63 | 5.50 | 1.86 | −0.81 | −8.37 | 98.3 | −2.24 | 7 |
| 0.005 | 0.50 | 10.51 | 3.28 | 1.30 | −1.48 | −3.09 | 100.0 | −5.19 | 312 |
| 0.010 | 0.50 | 10.51 | 3.27 | 1.31 | −0.80 | −2.08 | 100.0 | −2.72 | 85 |
| 0.020 | 0.50 | 10.52 | 3.28 | 1.30 | −0.43 | −1.35 | 100.0 | −1.23 | 23 |
| 0.050 | 0.50 | 10.48 | 3.27 | 1.38 | −0.17 | +1.50 | 100.0 | 0.77 | 4 |
| 0.100 | 0.50 | 10.43 | 3.25 | 1.41 | −0.20 | −8.73 | 100.0 | −2.52 | 2 |

The app's callout reads `Best cell: Range width (±) 50% × Hedge band 5.0% → net APR 1.50%, Sharpe 0.77`. That cell made 4 hedge trades in 31 days. Its neighbour at band 10% made 2 trades and shows −8.73%: the difference between the two is one trade's timing against a 32% trend, which is the discrete hedge error, not a strategy property. The `fee_over_lvr` column is NaN in every row because `lvr_usd` is negative under the 1-minute mark (section 1). The 0.15 row at band 0.005 to 0.10 matches section 4 to the rounding of the capital base.

## Reproduction

```
python3 docs/guide/raw/run_scenarios.py          # ~3 min; sections 1, 3-10 and the aggTrades / no-funding variants
python3 docs/guide/raw/run_hourly_stress.py      # ~5 s; sections 2 and 11
python3 docs/guide/raw/hourly_hedge_sign_check.py   # the hourly engine hedge-sign check, output in hourly_hedge_sign_check.txt
python3 docs/guide/raw/apptest_runs.py           # ~30 s; the UI run, rolling, by-month and the app's sweep
python3 docs/guide/raw/make_charts.py            # charts/
```
