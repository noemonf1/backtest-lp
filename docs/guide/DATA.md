# DATA

Phase 1 record. Every number below was printed by a script in `docs/guide/raw/` on 2026-09-06 and can be regenerated with the commands in the last section. Raw outputs: `binance_fetch.txt`, `fetch.log`, `fetch_result.json`, `repair_result.json`, `data_checks.txt`, `data_checks.json`, `apptest_file_selectboxes.txt`, `DATA_MANIFEST.json`.

## Window and pool

| Item | Value |
|---|---|
| Window | 2026-08-01 00:00:00 UTC to 2026-08-31 23:59:59 UTC, 31 days |
| Pool | `backtest.ETH_USDC_POOL` = `0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640`, Uniswap v3 ETH/USDC 0.05% fee tier, Ethereum mainnet. token0 = USDC (6 decimals), token1 = WETH (18 decimals). |
| Hedge instrument | Binance USD-M perpetual ETHUSDT |
| August 2026 archives | present on `data.binance.vision` (HEAD 200), so the window was not moved to July |

## 1. Legacy subgraph endpoint

`_query_uniswap_subgraph` (backtest.py:587-623) tries the legacy hosted endpoint first. In Phase 0 that attempt failed in 732 ms (a 301 to `error.thegraph.com`, which the egress proxy refuses). The threshold for patching was 3 seconds. **No patch was applied.** The deployed code runs unchanged.

## 2. Binance files

`backtest.ensure_all_data(start="2026-08", end="2026-08", cex_kind="klines", klines_interval="1m", include_pool_hourly=True)` and a second call with `klines_interval="1h"` (raw: `binance_fetch.txt`, 5.4 s total). aggTrades: `df -h .` showed 30 GB available, so `ensure_binance_csvs(['2026-08'], kinds=('aggTrades',))` was run (69 s, raw: `aggtrades_fetch.out`).

| File (`data/`) | Rows | First timestamp (UTC) | Last timestamp (UTC) | Bytes | sha256 |
|---|---|---|---|---|---|
| `ETHUSDT-klines-1m-2026-08.csv` | 44,640 | 2026-08-01 00:00:00 | 2026-08-31 23:59:00 | 4,947,296 | `f24c82daaf4eeed656444e05c5abec93d5286024036427739ce90a6baf3ad263` |
| `ETHUSDT-klines-1h-2026-08.csv` | 744 | 2026-08-01 00:00:00 | 2026-08-31 23:00:00 | 89,791 | `aa699e2d4e816d75363bf9a24f7aff38ea2e57934586bf07e379569ff6befe7d` |
| `ETHUSDT-fundingRate-2026-08.csv` | 93 | 2026-08-01 00:00:00 | 2026-08-31 16:00:00 | 2,563 | `e81333c5828b7611cc27ce119b928525cd1697d6beb1037c6d7007f510f8e37b` |
| `ETHUSDT-aggTrades-2026-08.csv` | 33,010,405 | 2026-08-01 00:00:00.013 | 2026-08-31 23:59:59.994 | 2,187,210,885 | `5db024bf09c9091d87b3e8000b7ce2e935838c4ea9f831d557a7968eb44c8f86` |
| `pool_hourly_0x88e6a0_2026-08-01_2026-08-31.parquet` | 721 | 2026-08-01 00:00 | 2026-08-31 00:00 | 48,939 | `8d944fe1e9f8a11df476698689fb3a6070537817e9e6ac1e39ffa1bd4cd26ca6` |
| `pool_hourly_0x88e6a0_2026-08-01_2026-09-01.parquet` | 745 | 2026-08-01 00:00 | 2026-09-01 00:00 | 50,491 | `7e3fdf480bef3e27ff240e6d52f941f9fbe0e8caf97029663f81f9eb5a4c4186` |
| `swaps_0x88e6a0_2026-08-01_2026-08-31.parquet` (repaired, see 4) | 159,990 | 2026-08-01 00:00:47 | 2026-08-31 23:59:59 | 10,468,110 | `3e28bbf3164c24bb801c7101cab0ca810623942e8a2d0876ee0f509fe90f8c83` |

The first `pool_hourly` file is what `ensure_all_data` produces: its query is `periodStartUnix_lte: end` with `end` = 2026-08-31 00:00 (backtest.py:642-644, 686), so it stops at the first hour of Aug 31. The second file was fetched with `ensure_pool_hourly("2026-08-01", "2026-09-01")` to cover the whole window; it is the one used for the liquidity fill.

## 3. Swap events: deviations from the stock code

The stock `backtest.fetch_pool_swaps` (backtest.py:708-825) cannot run against the live subgraph. Two defects, both observed, not inferred:

1. **`Swap.liquidity` does not exist.** The query at backtest.py:730-751 selects `liquidity` on each `Swap`. The gateway answers `Type Swap has no field liquidity` (`raw/schema_probe.json`). Calling the function for a one-minute window raised that error (ENVIRONMENT.md).
2. **int64 overflow on write.** The stock code stores `amount1` and `sqrt_price_x96` as Python ints (backtest.py:782-784). Raw WETH amounts above 9.22 ETH and every `sqrtPriceX96` of this pool (about 1.4e33) exceed int64, and `to_parquet` raises `OverflowError: Python int too large to convert to C long`. The first fetch attempt in this session died exactly there (`raw/fetch_swaps.out` of the first run). `run_claude.normalise_swaps` casts all three columns to float64 on load anyway (run_claude.py:269-270).

Fetch used for the guide: `docs/guide/raw/fetch_swaps.py`. It keeps the stock pagination rule byte for byte (1000 rows per page, `timestamp_gt` cursor, the same-second bump, backtest.py:790-799) and changes only: `liquidity` removed from the selection set; `logIndex` kept as `log_index`; amounts and `sqrt_price_x96` stored as float64 with the exact `sqrtPriceX96` string kept in `sqrt_price_x96_str`; the month split into 8 slices fetched in parallel threads with `timestamp_gte`/`timestamp_lt` edges, which cannot drop rows. Window `[2026-08-01, 2026-09-01)`.

Result (`raw/fetch_result.json`): 159,798 rows, first 2026-08-01 00:00:47, last 2026-08-31 23:59:59, 39.5 s wall, 0 retries, 155 full pages.

**Liquidity fill.** Each swap's `liquidity` column is the `poolHourDatas.liquidity` value of the hour containing the swap (`raw/repair_swaps.py`, from the 745-row hourly file; 0 swap hours were missing from the hourly file). Consequence: the engine's fee share `L_you / (L_active + L_you)` (run_claude.py:505-506) is computed against the pool's in-range liquidity as of the hour, not as of the swap. Within an hour the pool's liquidity changes when positions are minted, burned, or when the price crosses a tick boundary; those intra-hour changes are not seen. The August hourly liquidity ranged from 1.85e18 to 4.51e19 (section 6), so the share of a fixed position varies by a factor of 24 across the month, and the fill captures that variation at hourly resolution only.

**Window semantics.** The task text calls the fetch as `ensure_pool_swaps("2026-08-01", "2026-08-31")`. In the code `end` is exclusive: `timestamp_lt: end_ts` with `end_ts` = midnight of the end date (backtest.py:738, 754), so that call returns 30 days and omits Aug 31 entirely, while the sidebar help text for `Swaps to` says "Last day to fetch (inclusive)" (app.py:279-282). The fetch for this guide used `[2026-08-01, 2026-09-01)` to get the 31 days the task specifies. The file is named `swaps_0x88e6a0_2026-08-01_2026-08-31.parquet`, the name the sidebar would produce for those two dates, so the app's `Swaps file` selectbox lists it (section 7).

## 4. Completeness repair

Procedure (`raw/repair_swaps.py`): every `boundary=1` line in `fetch.log` (a full 1000-row page) gives the cursor second. For each such second, and for the second before it (the stock rule bumps the cursor by one when a whole page shares a timestamp), query `swaps(first: 1000, where: {pool, timestamp: <second>})` and add every row whose `(transaction.blockNumber, logIndex)` key is absent.

| Item | Value |
|---|---|
| Page boundaries | 155 |
| Seconds probed | 310 |
| Rows added | 192 |
| Fraction added | 0.1202% of 159,798 |
| Final rows | 159,990 |
| Added rows listed in | `raw/repair_added_rows.csv` |
| Per-second detail | `raw/repair_result.json` (`per_second`) |

So the stock pagination drops about one row in 830 on this pool. Every dropped row sits in a boundary second, which is a block that had more swaps than fitted in the page.

**Stock de-duplication key.** After paging, the stock code drops duplicates on `(block_number, block_time, sqrt_price_x96)` (backtest.py:813-815). On the repaired file that key would discard 21 real, distinct swaps (different `log_index`): 17 are 1-raw-USDC dust swaps at an unchanged price; 4 collide only because two distinct `sqrtPriceX96` strings round to the same float64 (`data_checks.json`, key `K_dedup_key`). USD volume affected: 0.00. The final parquet keeps all 159,990 rows and is de-duplicated on `(block_number, log_index)` instead.

## 5. Sanity checks (`raw/data_checks.txt`)

**Swaps per day.** 31 of 31 days present. Total 159,990. Minimum 1,902 on Aug 15, maximum 10,395 on Aug 21, mean 5,161. Busiest UTC hour of day 17:00 (8,506 swaps), quietest 04:00 (4,637). The app's `Fetch data` caption says "~2-3M swaps/month for ETH/USDC 0.05%" (app.py:266-271); August 2026 had 0.16M.

| Day | Swaps | USD volume from `amount0` | Day | Swaps | USD volume from `amount0` |
|---|---|---|---|---|---|
| Aug 01 | 3,315 | 29,560,428 | Aug 17 | 3,668 | 80,272,286 |
| Aug 02 | 3,752 | 40,358,329 | Aug 18 | 3,370 | 71,776,317 |
| Aug 03 | 4,698 | 53,586,788 | Aug 19 | 7,785 | 302,904,833 |
| Aug 04 | 4,659 | 57,762,831 | Aug 20 | 7,084 | 120,836,153 |
| Aug 05 | 4,900 | 78,342,357 | Aug 21 | 10,395 | 191,564,896 |
| Aug 06 | 3,952 | 91,540,433 | Aug 22 | 6,893 | 113,670,345 |
| Aug 07 | 4,013 | 102,651,690 | Aug 23 | 7,049 | 97,181,474 |
| Aug 08 | 3,684 | 23,767,607 | Aug 24 | 7,035 | 113,007,658 |
| Aug 09 | 2,790 | 31,948,641 | Aug 25 | 6,866 | 89,288,730 |
| Aug 10 | 4,062 | 83,078,873 | Aug 26 | 7,030 | 66,811,619 |
| Aug 11 | 4,157 | 44,597,769 | Aug 27 | 7,615 | 83,227,142 |
| Aug 12 | 3,837 | 78,979,058 | Aug 28 | 7,281 | 78,137,008 |
| Aug 13 | 3,391 | 56,743,862 | Aug 29 | 4,134 | 25,955,000 |
| Aug 14 | 3,315 | 44,373,315 | Aug 30 | 8,088 | 73,098,240 |
| Aug 15 | 1,902 | 15,744,604 | Aug 31 | 7,304 | 65,189,449 |
| Aug 16 | 1,966 | 16,642,151 | Total | 159,990 | 2,422,599,886 |

The full 31-row table with both volume columns is in `data_checks.json`, key `C_usd_volume_per_day`.

**USD volume, two ways.** Sum of `|amount0|/1e6` over the swap file: 2,422,599,886 USD. Sum of `poolHourDatas.volumeUSD` over the 744 August hours: 2,422,385,371 USD. Ratio 1.00009. Daily ratios range 0.9997 to 1.0009.

**Swap-derived hourly volume against `volumeUSD`.** 744 hours, 0 hours with `volumeUSD == 0`. Ratio: median 1.00006, mean 1.00009, p1 0.99869, p99 1.00129, min 0.99845, max 1.00457. Hours outside [0.9, 1.1]: 0. Chart `raw/charts/D_hourly_volume_ratio_hist.png`. The swap file is complete at the hour level to within 0.5%.

**Pool price against Binance.** Pool price after each swap = `run_claude.eth_price_from_sqrtx96(sqrt_price_x96)` = 1e12 / (sqrtPriceX96 / 2^96)^2 (run_claude.py:126-128). Binance mark = the 1-minute kline close of the minute containing the swap. Basis = (pool / Binance − 1) × 10,000.

| Sample | n | mean bps | median | std | p1 | p5 | p95 | p99 | min | max |
|---|---|---|---|---|---|---|---|---|---|---|
| every swap | 159,990 | −0.65 | −0.54 | 11.13 | −24.93 | | | +19.57 | −360.5 | +285.6 |
| last swap of each minute | 38,092 | −0.93 | | 5.22 | −10.25 | | | +10.33 | | +213 (Aug 22 05:10) |

97.3% of minutes are within 10 bps. The basis widens after Aug 19 (charts `raw/charts/E_basis_bps_timeseries.png`, `E_pool_vs_binance_price.png`). The pool quotes USDC, the perp quotes USDT, and the mean gap of under 1 bp says the two dollars traded at par in August 2026.

**Funding.** 93 prints, all with `funding_interval_hours = 8`, at 00:00, 08:00 and 16:00 UTC (31 each). Stored sign: positive = longs pay shorts (Binance convention; the code credits the short at run_claude.py:656). 88 positive, 5 negative, 0 zero. Mean +0.0000519 per 8 h. Annualised mean 0.0000519 × 3 × 365 = 5.68%. Sum over the month +0.4828%: a 1 USD short collected 0.48 cents in August.

**Inter-block spacing.** 92,532 distinct blocks carry swaps, block numbers 25,656,296 to 25,878,704. Spacing per block between consecutive swap-bearing blocks: median 12.0 s, mean 12.044 s, p1 12.0, p99 12.0, max 24. For the 47,661 pairs of adjacent block numbers, 99.65% are exactly 12 s apart and 167 are 24 s apart (a missed slot). The nominal 12-second slot holds.

## 6. Tape statistics (`data_checks.json` keys `H_tape_stats`, `I_liquidity`)

From the Binance 1-minute klines, 44,639 log returns of the close, no missing minutes:

| Statistic | Value |
|---|---|
| Max absolute 1-minute log return | +3.4753% at 2026-08-19 15:27 UTC (open 2046.42, high 2132.00, low 2040.30, close 2118.79) |
| Excess kurtosis (Fisher) | 264.59 |
| Skew | +2.43 |
| Lag-1 autocorrelation of absolute returns | 0.431 |
| Lag-1 autocorrelation of signed returns | −0.010 |
| Minutes with abs return > 0.5% / > 1% | 56 / 12 |
| Realised annualised vol from 1-minute returns (std × sqrt(525,600)) | 51.77% |
| Realised annualised vol from daily UTC close-to-close returns (std × sqrt(365), 30 returns) | 67.82% |
| Month open / close / high / low | 1861.68 / 2466.53 / 2566.40 (Aug 27 09:27) / 1820.61 (Aug 1 18:50) |
| Close-to-close return over the month | +32.49% |

Charts `raw/charts/H_close_with_max_move.png`, `raw/charts/H_1m_return_hist.png`.

Per-swap liquidity (the hourly in-range pool liquidity applied to each swap): min 1.848e18 (Aug 19 21:00), max 4.506e19 (Aug 29 08:00), mean 8.82e18, median 6.99e18; first hour 6.927e18, last hour 4.431e18. A 1,000,000 USD position at the defaults (range ±15%, first swap price 1861.06) has L = 1.589e17 (`run_claude.build_position`), which is 2.243% of the pool's in-range liquidity in the first hour and 2.421% on average over the month (min 0.35%, max 7.92%). Chart `raw/charts/I_hourly_liquidity_vs_position.png`.

| Capital | Share, first hour | Share, month mean |
|---|---|---|
| 100k | 0.229% | 0.249% |
| 300k | 0.684% | 0.742% |
| 1M | 2.243% | 2.421% |
| 3M | 6.44% | 6.85% |
| 10M | 18.66% | 19.15% |

(The engine's own `mean_liquidity_share_pct` for the default run is 2.655%, SCENARIOS.md; it averages over in-range swaps rather than over hours.)

**Tick check.** For 5 swaps spread over the month, `floor(log(P_raw)/log(1.0001))` computed from the exact `sqrtPriceX96` string equals the stored `tick` in every case (`data_checks.json`, key `J_tick_check`). Recomputing from the float64 column disagrees on 96 of 159,990 rows that sit exactly on a tick boundary; the engine uses the stored `tick` for range tests (run_claude.py:503-504), so that rounding does not enter any result.

## 7. The app sees the files

AppTest, engine `swap_level`, `Mode` = `Real data (files)` (`raw/apptest_file_selectboxes.txt`):

```
Swaps file: options=['data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet'] value='data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet'
CEX price file: options=['data/ETHUSDT-aggTrades-2026-08.csv', 'data/ETHUSDT-klines-1h-2026-08.csv', 'data/ETHUSDT-klines-1m-2026-08.csv'] value='data/ETHUSDT-aggTrades-2026-08.csv'
Funding file: options=['(none — assume 0)', 'data/ETHUSDT-fundingRate-2026-08.csv'] value='(none — assume 0)'
```

Two defaults matter. The app pre-selects the aggTrades file when it exists (app.py:930 lists `*aggTrades*` before `*klines*`), and pre-selects no funding. The guide's default run uses the 1-minute klines and the funding file; SCENARIOS.md also reports the aggTrades and no-funding variants.

## 8. Cached data in the branch

Every `data/` file under 90 MB is committed with `git add -f` under `data-cache/` with the same name (6 files, 15.6 MB). The aggTrades file (2.19 GB) is not. `docs/guide/raw/DATA_MANIFEST.json` lists every file with window, rows and sha256. To restore: `cp data-cache/* data/`.

## Reproduction

```
python3 -c "import backtest as bt; print(bt.ensure_all_data(start='2026-08', end='2026-08', cex_kind='klines', klines_interval='1m', include_pool_hourly=True))"
python3 -c "import backtest as bt; print(bt.ensure_all_data(start='2026-08', end='2026-08', cex_kind='klines', klines_interval='1h'))"
python3 -c "import backtest as bt; print(bt.ensure_binance_csvs(['2026-08'], kinds=('aggTrades',)))"
python3 docs/guide/raw/fetch_swaps.py        # needs THEGRAPH_API_KEY; writes raw/swaps_raw_pages.parquet and raw/fetch.log
python3 docs/guide/raw/repair_swaps.py       # writes data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet
python3 docs/guide/raw/data_checks.py        # writes raw/data_checks.txt, .json and raw/charts/
```
