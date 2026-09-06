# APP_MAP

Map of the Streamlit app at deployed commit `e1bb6e7`. Every code claim cites `file:line` in this repo. Every claim about rendered text comes from a headless `streamlit.testing.v1.AppTest` run of `app.py` in Demo mode on 2026-09-06; the print-outs are in `docs/guide/raw/appmap_*.txt` and the harness is `docs/guide/raw/appmap_apptest.py`. No network call was made. Nothing outside `docs/guide/` was modified.

Vocabulary for a reader new to this domain:

- **LP** = liquidity provider on Uniswap v3. You post a two-asset inventory (ETH and USDC) inside a price band. Traders swap against it. You earn a fee on each swap. Your inventory drifts toward the asset that is falling. That drift is the position's delta.
- **Hedge** = a short ETH perpetual future on Binance sized to cancel the LP delta.
- **LVR** = loss-versus-rebalancing. The money arbitrageurs take from the LP when the pool price lags the exchange price.
- **Funding** = the periodic payment between longs and shorts on a perpetual future. In this app a positive number means the short (you) received it.
- **Swap events** = one row per pool trade, from the Ethereum chain. **Klines** = OHLC bars from Binance. **aggTrades** = individual Binance trades.

---

## 1. Sidebar widget map

### 1.1 How the sidebar is built

The sidebar is assembled at `app.py:862-1020` from four pieces:

| Piece | Function | Lines | Note |
|---|---|---|---|
| `Fetch data` expander | `_sidebar_fetch` | `app.py:192-293` | `@st.fragment`. Writes into `st.session_state["fetch_cfg"]`. |
| `Save / load config` expander | `_sidebar_config_persist` | `app.py:357-434` | `@st.fragment`. Reads and writes `st.session_state["assumptions"]`. |
| Engine, Data source, Backtest window | inline under `with st.sidebar:` | `app.py:866-1005` | Plain module-level widgets. Their values live in module globals (`engine_key`, `mode`, `swaps_path`, `cex_path`, `funding_path`, `demo_*`, `live_days`, `interval`, `win_start`, `win_end`). |
| Assumption sliders and run controls | `_sidebar_assumptions(engine_key)` | `app.py:717-855` | `@st.fragment`. Assumption widgets are declared as data in `ASSUMPTION_FIELDS` (`app.py:513-709`) and rendered by `_widget_for` (`app.py:461-505`) through `_render_fields` (`app.py:712-714`). Run controls are stored in `st.session_state["_run_controls"]` (`app.py:849-855`). |

`_widget_for` handles four widget kinds (`app.py:474-499`): `number` (`st.number_input`), `select_slider`, `select` (`st.selectbox`), and `yesno` (a `selectbox` with options `Yes`/`No` written back as a bool). A `derive` callback may write a second field after the widget (`app.py:504-505`); two entries use it: `fee_tier_bps` writes `fee_tier = bps / 10_000` (`app.py:638`) and `taker_fee_bps` writes `binance_taker_fee = bps / 10_000` (`app.py:648`).

None of the assumption widgets has a `key`. Each writes its value into the dict `st.session_state["assumptions"]` under the `field` name (`app.py:477, 486, 493, 496-499`). At Run time that dict becomes a `backtest.Assumptions` via `Assumptions.from_dict` (`app.py:1919`, `backtest.py:108-111`).

### 1.2 Count

`app.py` declares **59 sidebar widget call sites**. Counted from source: 8 in `_sidebar_fetch` (`app.py:204-289`), 3 in `_sidebar_config_persist` (`app.py:370-427`), 16 inline under `with st.sidebar:` (`app.py:867-1005`; the labels `Mode`, `CEX price file` and `Funding file` each appear twice, once per engine branch), 23 entries in `ASSUMPTION_FIELDS` (`app.py:513-709`), and 9 explicit widgets inside `_sidebar_assumptions` (`app.py:738, 786, 792, 798, 807, 814, 820, 826, 838`). Distinct labels: 56.

The number rendered at once depends on the mode. AppTest counts (`docs/guide/raw/appmap_widgets_*.txt`, harness line `##### <scenario>: N widgets`):

| Scenario | Widgets rendered in the sidebar | Raw file |
|---|---|---|
| swap_level + Demo (default) | 38 | `appmap_widgets_swap_level_demo.txt` |
| swap_level + Real data (files) | 37 | `appmap_widgets_swap_level_real.txt` |
| hourly + Live fetch | 33 | `appmap_widgets_hourly_live.txt` |
| hourly + Live + Absolute bounds + periodic + stress on | 37 | `appmap_widgets_hourly_live_abs_periodic_stress.txt` |
| hourly + Local CSV files | 33 | `appmap_widgets_hourly_local.txt` |

The earlier brief counted 29. That figure does not agree with any of the counts above (59 declared, 56 distinct labels, 33 to 38 rendered). No subset of the sidebar that I can construct from the code yields 29.

### 1.3 Sidebar widget table

Columns: **Label** as rendered; **key** (`None` = auto key); **Type**; **Default**; **Range / options**; **Writes** (where the value lands); **Consumer** (function that reads it); **Reaches an engine?** (`yes: file.func line` when the value changes a number inside `run_claude.py` or `kimi/uniswap_delta_hedge_backtest.py`; `no: UI only` when it only drives the UI or a data loader; `no: dead` when it is rendered but nothing reads it in the current mode). **Shown when** gives the condition for conditional widgets.

Defaults come from the AppTest dump (`value=`). Ranges come from the same dump (`min`, `max`, `step`, `options`). A `max` of `1.797e+308` means no upper bound was set.

#### Fetch data expander (`app.py:199-293`, always shown)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? |
|---|---|---|---|---|---|---|---|---|
| 1 | From (YYYY-MM) | `fetch_start` | text_input | `2024-01` | free text | `fetch_cfg["start"]` (`app.py:204`) | `bt.ensure_all_data` on `Fetch now` (`app.py:246-252`) and on auto-fetch at Run (`app.py:1060-1066`) | no: UI only (selects which Binance monthly files to download) |
| 2 | To (YYYY-MM) | `fetch_end` | text_input | `2024-01` | free text | `fetch_cfg["end"]` (`app.py:210`) | same as #1 | no: UI only |
| 3 | CEX price resolution | `fetch_cex_choice` | selectbox | `klines 1m (~5 MB/month) — recommended` | `klines 1m…`, `klines 1h…`, `aggTrades…` | `fetch_cfg["cex_kind"]`, `fetch_cfg["klines_interval"]` (`app.py:231-236`) | `bt.ensure_all_data(cex_kind=, klines_interval=)` (`app.py:249-250`, `backtest.py:868-892`) | no: UI only (selects a file to download; the engine reads whichever file is picked under Data source) |
| 4 | Fetch now | `fetch_binance_btn` | button | `False` | | triggers `bt.ensure_all_data` (`app.py:238-262`) | `backtest.ensure_binance_csvs` (`backtest.py:537-584`) | no: UI only |
| 5 | Swaps from (YYYY-MM-DD) | `fetch_swap_start` | text_input | `2024-01-01` | free text | `fetch_cfg["swap_start"]` (`app.py:273`) | `_run_swap_fetch` → `bt.ensure_pool_swaps` (`app.py:293, 329`) | no: UI only |
| 6 | Swaps to (YYYY-MM-DD) | `fetch_swap_end` | text_input | `2024-01-08` | free text | `fetch_cfg["swap_end"]` (`app.py:279`) | same as #5 | no: UI only. The help text is wrong; see C1. |
| 7 | Fetch swap events | `fetch_swaps_btn` | button | `False` | | calls `_run_swap_fetch` (`app.py:287, 292-293`) | `backtest.ensure_pool_swaps` (`backtest.py:828-865`) | no: UI only. Fails against the live subgraph; see C3. |
| 8 | Cancel | `fetch_swaps_cancel` | button | `False` | | `session_state["cancel_swap_fetch"] = True` (`app.py:289-290`) | polled by `cancel_cb` inside `fetch_pool_swaps` (`app.py:332`, `backtest.py:805`) | no: UI only |

#### Save / load config expander (`app.py:361-434`, always shown)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? |
|---|---|---|---|---|---|---|---|---|
| 9 | Download current config (JSON) | `None` | download_button | | | nothing; serves `{"schema_version": 1, "assumptions": {…}}` (`app.py:365-376`) | browser | no: UI only |
| 10 | Load config | `cfg_upload` | file_uploader | `None` | `.json` only | `session_state["assumptions"].update(...)`, `session_state["_last_upload_id"]`, then `st.rerun()` (`app.py:420-424`) | every assumption widget on the next render | indirectly (through the assumption fields it overwrites) |
| 11 | Reset all to defaults | `None` | button | `False` | | resets `assumptions` and `fetch_cfg`, deletes widget keys starting with `fetch_`, `win_`, `cfg_upload`, then `st.rerun()` (`app.py:427-434`) | | indirectly |

#### Engine and Data source (`app.py:866-985`)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| 12 | Backtest engine | `None` | selectbox | `swap_level (reference)` | `swap_level (reference)`, `hourly (fast fallback)` | global `engine_key` (`app.py:877`) | `_sidebar_assumptions(engine_key)` (`app.py:1008`), `_execute_run` (`app.py:1920-1922`) | yes: picks `bt.run_swap_level` (`app.py:1938`) or `bt.run_hourly` (`app.py:1967`) | always |
| 13 | Mode | `None` | selectbox | `Demo (synthetic tape)` | `Demo (synthetic tape)`, `Real data (files)` | global `mode` (`app.py:886`) | `_load_swap_level_inputs` (`app.py:1083`), `is_demo` (`app.py:1920`) | no: UI only (chooses the loader) | engine = swap_level |
| 14 | Demo days | `None` | slider | `25` | 5 to 120, step 5 | global `demo_days` | `_demo` → `bt.make_demo_tape` (`app.py:1084, 153-154`) | yes: `run_claude.make_demo_data` line 363 (`days`) | swap_level + Demo |
| 15 | Annualised vol | `None` | slider | `0.65` | 0.20 to 1.50, step 0.05 | global `demo_vol` | same | yes: `run_claude.make_demo_data` line 378 (`vol_annual`) | swap_level + Demo |
| 16 | Starting ETH price (USD) | `None` | number_input | `3000.0` | unbounded, step 100 | global `demo_s0` | same | yes: `run_claude.make_demo_data` line 379 (`s0`) | swap_level + Demo |
| 17 | RNG seed | `None` | number_input | `7` | unbounded, step 1 | global `demo_seed` | same | yes: `run_claude.make_demo_data` line 375 (`seed`) | swap_level + Demo |
| 18 | Swaps file | `None` | selectbox | first `*swap*.parquet`/`.csv` found, else `(none)` | files from `_list_swap_files` (`app.py:132-143`) | global `swaps_path` | `_load_swaps` → `bt.load_swaps` (`app.py:1105, 158-159`) | no: UI only (selects the file) | swap_level + Real data |
| 19 | CEX price file | `None` | selectbox | first `*aggTrades*.csv` then `*klines*.csv` in `data/`, else `(none)` | `_list_files` (`app.py:930`) | global `cex_path` | `_load_cex` → `bt.load_cex_prices` (`app.py:1106`) | no: UI only | swap_level + Real data |
| 20 | Funding file | `None` | selectbox | `(none — assume 0)` | `(none — assume 0)` + `*fundingRate*.csv` | global `funding_path` | `_load_funding_series` (`app.py:1107-1111`) | no: UI only. `(none — assume 0)` gives an empty Series, which `run_claude.simulate_hedge` treats as zero funding (`run_claude.py:647`). | swap_level + Real data |
| 21 | Mode | `None` | selectbox | `Live fetch from Binance` | `Live fetch from Binance`, `Local CSV files` | global `mode` (`app.py:950`) | `_load_hourly_inputs` (`app.py:1119`) | no: UI only | engine = hourly |
| 22 | Lookback (days) | `None` | slider | `30` | 7 to 180, step 1 | global `live_days` | `_fetch_klines`, `_fetch_funding` (`app.py:1120, 177-184`) | no: UI only (window of the live download) | hourly + Live |
| 23 | Bar interval | `None` | selectbox | `1h` | `1h`, `4h`, `1d` | global `interval` | `_fetch_klines(interval)` (`app.py:1120`) | yes, indirectly: bar size sets `bar_hours` in `kimi …backtest.py:292` and the count of `rebalance_period` bars at line 376. See C12 and C13. | hourly + Live |
| 24 | CEX price file | `None` | selectbox | first CEX CSV in `data/`, else `(none)` | `_list_files` (`app.py:969`) | global `cex_path` | `_load_hourly_inputs` (`app.py:1122-1137`) | no: UI only | hourly + Local CSV |
| 25 | Funding file | `None` | selectbox | first `*fundingRate*.csv`, else `(none)` | `_list_files` (`app.py:970`) | global `funding_path` | `_load_hourly_inputs` (`app.py:1140-1144`) | no: UI only. `(none)` does **not** give zero funding; see C14. | hourly + Local CSV |

#### Backtest window expander (`app.py:988-1005`, always shown)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? |
|---|---|---|---|---|---|---|---|---|
| 26 | Window start (YYYY-MM-DD) | `win_start` | text_input | `""` | free text | global `win_start` | `_clip_series`, `_clip_swaps` (`app.py:1028-1042`, called at 1105-1108, 1131, 1141) | no: UI only (clips the input series). Not applied in hourly Live mode; see C7. |
| 27 | Window end (YYYY-MM-DD) | `win_end` | text_input | `""` | free text | global `win_end` | same | no: UI only. End day is excluded; see C8. |

#### Position sizing expander (`app.py:731-756`, always shown, contents vary)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| 28 | Capital deployed (USD) | `None` | number_input | `1000000.0` | min 1000, step 50000 | `assumptions["capital_usd"]` | `Assumptions.capital_usd` | yes: `run_claude.build_position` line 218/234 (sizes `L`), `run_backtest` line 694 (gas column), 721 (capital base); `kimi …backtest.py:298-299` (50/50 split), 428 (`lp_pnl`) | always |
| 29 | Range width (± fraction around spot) | `None` | select_slider | `0.15` | `0.02, 0.05, 0.1, 0.15, 0.25, 0.5, 0.75` | `assumptions["range_width"]` | `Assumptions.range_width` | yes: `run_claude.build_position` line 219-220; hourly: `backtest.run_hourly` line 434-435 derives `lower_price`/`upper_price` from the first close | swap_level always (`app.py:734`); hourly when Range specification = Symmetric (`app.py:747`) |
| 30 | Range specification | `None` | selectbox | `Symmetric ± around spot` | `Symmetric ± around spot`, `Absolute price bounds` | local `range_mode`; sets `lower_price`/`upper_price` to `None` in the Symmetric branch (`app.py:748`) | `_sidebar_assumptions` | no: UI only (chooses which of #29 or #31/#32 to render) | engine = hourly |
| 31 | Lower price (USDC/ETH) | `None` | number_input | `1800.0` | unbounded, step 50 | `assumptions["lower_price"]` | `Assumptions.lower_price` | yes: `backtest._to_kimi_config` line 338 → `kimi …backtest.py:263` (`tick_lower`) | hourly + Absolute |
| 32 | Upper price (USDC/ETH) | `None` | number_input | `2200.0` | unbounded, step 50 | `assumptions["upper_price"]` | `Assumptions.upper_price` | yes: `backtest.py:339` → `kimi …backtest.py:264` (`tick_upper`) | hourly + Absolute |
| 33 | Our share of pool active liquidity | `None` | number_input | `0.001` | 0.00001 to 0.5, step 0.0005, format `%.5f` | `assumptions["pool_share"]` | `Assumptions.pool_share` | yes: `backtest.py:340` → `kimi …backtest.py:348` (`our_share_of_pool`). Has no effect in hourly Local CSV mode because volume is zero; see C11. | engine = hourly |
| 34 | Pool volume as fraction of Binance quote volume | `None` | number_input | `0.08` | 0.001 to 1.0, step 0.01 | `assumptions["pool_volume_multiplier"]` | `Assumptions.pool_volume_multiplier` | yes: `backtest.run_hourly` line 442-446 builds `pool_volume_24h`, read at `kimi …backtest.py:342-344`. Zero effect in Local CSV mode; see C11. Scaling wrong for 4h/1d bars; see C12. | engine = hourly |
| 35 | Reposition when out of range? | `None` | selectbox (yesno) | `Yes` | `Yes`, `No` | `assumptions["reposition"]` (bool) | `Assumptions.reposition` | yes: `run_claude.build_position_schedule` line 557 | engine = swap_level |
| 36 | Reposition buffer (hours) | `None` | number_input | `6.0` | min 0, step 1 | `assumptions["reposition_buffer_hours"]` | `Assumptions.reposition_buffer_hours` | yes: `run_claude.build_position_schedule` line 558, 567 | engine = swap_level |

#### Hedge policy expander (`app.py:758-767`, always shown, contents vary)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| 37 | Hedge band (fraction of max delta) | `None` | select_slider | `0.03` | `0.005, 0.01, 0.02, 0.03, 0.05, 0.1, 0.25` | `assumptions["hedge_band"]` | `Assumptions.hedge_band` | yes: `run_claude.simulate_hedge` line 610, 617 | engine = swap_level |
| 38 | Rebalance mode | `None` | selectbox | `threshold` | `threshold`, `periodic` | `assumptions["rebalance_mode"]` | `Assumptions.rebalance_mode` | yes: `backtest.py:334` → `kimi …backtest.py:375` | engine = hourly |
| 39 | Rebalance threshold (fraction of \|delta\|) | `None` | select_slider | `0.05` | `0.01, 0.02, 0.05, 0.1, 0.2` | `assumptions["rebalance_threshold_pct"]` | `Assumptions.rebalance_threshold_pct` | yes: `backtest.py:335` → `kimi …backtest.py:381` | hourly + threshold |
| 40 | Rebalance every (hours) | `None` | selectbox | `24` | `1, 4, 8, 12, 24, 48` | `assumptions["rebalance_period_h"]` | `Assumptions.rebalance_period_h` | yes: `backtest.py:336` → `kimi …backtest.py:376` (counted in bars, not hours; see C13) | hourly + periodic |
| 41 | Perp leverage | `None` | selectbox | `4` | `1, 2, 3, 4, 5, 10` | `assumptions["leverage"]` | `Assumptions.leverage` | swap_level yes: `run_claude.run_backtest` line 720. hourly **no: dead** (not passed in `backtest._to_kimi_config` lines 331-342; see C9) | always (`app.py:767`) |
| 42 | Margin buffer (× notional/leverage) | `None` | number_input | `1.5` | min 1.0, step 0.1 | `assumptions["margin_buffer"]` | `Assumptions.margin_buffer` | swap_level yes: `run_claude.py:720`. hourly **no: dead** (C9) | always |

#### Cost model expander (`app.py:769-777`, collapsed by default)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| 43 | LP fee tier (bps) | `None` | selectbox | `5` | `1, 5, 30, 100` | `assumptions["fee_tier"] = bps / 10_000` via `derive` (`app.py:638`) | `Assumptions.fee_tier` | yes: `run_claude.attribute_swaps` line 511-512 (fees), 517-518 (fee-net execution); `kimi …backtest.py:348` (`pool_fee_rate`). Tick spacing stays 10; see C22. | always |
| 44 | Binance taker fee (bps) | `None` | selectbox | `4.5` | `2.0, 3.0, 4.0, 4.5, 5.0` | `assumptions["taker_fee_bps"]` and `assumptions["binance_taker_fee"] = bps / 10_000` via `derive` (`app.py:648`) | `Assumptions.taker_fee_bps` (swap_level), `Assumptions.binance_taker_fee` (hourly) | yes: `run_claude.simulate_hedge` line 624; `kimi …backtest.py:400` | always |
| 45 | Half-spread when using klines (bps) | `None` | number_input | `0.5` | min 0, step 0.1 | `assumptions["half_spread_bps"]` | `Assumptions.half_spread_bps` | yes: `run_claude.simulate_hedge` line 624 (applied for every CEX source; see C15) | engine = swap_level |
| 46 | Linear impact (bps per $100k clip) | `None` | number_input | `0.8` | min 0, step 0.1 | `assumptions["impact_bps_per_100k"]` | `Assumptions.impact_bps_per_100k` → `Config.impact_coef_bps_per_100k` (`backtest.py:130`) | yes: `run_claude.simulate_hedge` line 625 | engine = swap_level |
| 47 | Gas per reposition (USD) | `None` | number_input | `40.0` | min 0, step 5 | `assumptions["gas_usd_per_reposition"]` | `Assumptions.gas_usd_per_reposition` | yes: `run_claude.build_position_schedule` line 571 and `run_backtest` line 693 (charged twice; see C20) | engine = swap_level |
| 48 | Swap cost to re-ratio on reposition (bps) | `None` | number_input | `5.0` | min 0, step 0.5 | `assumptions["lp_rebalance_swap_bps"]` | `Assumptions.lp_rebalance_swap_bps` | yes: `run_claude.py:570` and `694` (charged twice; C20) | engine = swap_level |
| 49 | Flat slippage on hedge trades (bps) | `None` | number_input | `1.0` | min 0, step 0.5 | `assumptions["slippage_bps"]` | `Assumptions.slippage_bps` | yes: `backtest.py:341` → `kimi …backtest.py:404` | engine = hourly |

#### Mechanics (advanced) expander (`app.py:779-781`, swap_level only, collapsed)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| 50 | Hedge decision grid (s) | `None` | selectbox | `60` | `10, 30, 60, 120, 300` | `assumptions["grid_seconds"]` | `Assumptions.grid_seconds` | yes: `run_claude.py:548` (reposition walk), 596 (hedge grid), 679 (fee aggregation) | engine = swap_level |
| 51 | Fee markout window (s) | `None` | selectbox | `0` | `0, 60, 300, 900` | `assumptions["markout_seconds"]` | `Assumptions.markout_seconds` | yes: `run_claude.attribute_swaps` line 491 (shifts the CEX mark time used for both `fee_usd` in WETH and `lvr_usd`) | engine = swap_level |

#### Analysis + Run expander (`app.py:783-844`, always shown, contents vary)

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| 52 | Run range × hedge-band sweep | `None` | checkbox | `False` | | `_run_controls["do_sweep"]` (`app.py:850`) → global `do_sweep` (`app.py:1016`) | `_execute_run` (`app.py:1994`) → `_render_sweep` | yes: each cell calls `run_claude.run_backtest` (`backtest.py:291`) | engine = swap_level |
| 53 | Rolling-window distribution (30d) | `None` | checkbox | `False` | | `_run_controls["do_rolling"]` → global `do_rolling` | `ResultRenderer.rolling` (`app.py:1987-1988, 1419`) | no: UI only (post-processes `net_usd`) | always |
| 54 | Backtest by month | `None` | checkbox | `False` | | `_run_controls["do_by_month"]` → global `do_by_month` | `_render_by_month` (`app.py:1991-1992, 1655`) | yes: one `bt.run_swap_level` or `bt.run_hourly` per month (`app.py:1718, 1729`) | always |
| 55 | Stress test (price shock) | `None` | checkbox | `False` | | local `do_stress` (`app.py:807`) | gates #56 to #58 | no: UI only | engine = hourly |
| 56 | Shock over last N days | `None` | number_input | `7` | 1 to 90, step 1 | `stress_cfg["days"]` (`app.py:833`) | `_apply_stress_shock` (`app.py:1944-1945, 1876-1905`) | yes, indirectly: rewrites the OHLC columns handed to `bt.run_hourly` (`app.py:1967`) | hourly + stress on |
| 57 | Total price move (%) | `None` | slider | `-20` | -50 to 50, step 1 | `stress_cfg["pct"] = pct / 100` (`app.py:834`) | same | yes, indirectly (same) | hourly + stress on |
| 58 | Shape | `None` | selectbox | `linear` | `linear`, `step` | `stress_cfg["shape"]` (`app.py:835`) | same (`app.py:1896-1900`) | yes, indirectly (same) | hourly + stress on |
| 59 | Run label (optional) | `None` | text_input | `""` | free text, placeholder `e.g. 'wide range, tight band'` | `_run_controls["run_label"]` → global `run_label` | `_push_run` (`app.py:1969, 1197`) | no: UI only | always |

### 1.4 Main-area widget table

Everything below is inside `tab_run` (`app.py:1862, 1868`). Captured after two Demo runs with sweep, rolling and by-month enabled (`docs/guide/raw/appmap_main_after_run.txt`).

| # | Label | key | Type | Default | Range / options | Writes | Consumer | Reaches an engine? | Shown when |
|---|---|---|---|---|---|---|---|---|---|
| M1 | Run backtest | `None` | button (primary) | `False` | | global `run` (`app.py:1869`) | `_execute_run` (`app.py:2000-2002`) | yes: `bt.run_swap_level` (`app.py:1938`) or `bt.run_hourly` (`app.py:1967`) | always |
| M2 | Download summary (JSON) | `None` | download_button | | | nothing; serves `json.dumps(res.summary)` (`app.py:1357-1363`) | browser | no: UI only | after a successful run |
| M3 | Download timeseries (CSV) | `None` | download_button | | | nothing; serves `res.timeseries.to_csv()` (`app.py:1367-1373`) | browser | no: UI only | after a successful run |
| M4 | X axis (columns) | `sweep_x_field` | selectbox | `hedge_band` (shown as `Hedge band`) | six `bt.SWEEPABLE_FIELDS` keys (`backtest.py:182-213`) | local `x_field` (`app.py:1467-1474`) | `bt.sweep_swap_level_axes` (`app.py:1576-1582`) | yes: substituted into each cell's `Assumptions` (`backtest.py:287-292`) | swap_level, sweep checked, after run |
| M5 | X values (comma-separated) | `sweep_x_values` | text_input | `0.005, 0.01, 0.02, 0.05, 0.1` | free text, parsed by `_parse` (`app.py:1506-1511`) | local `x_values` | same | yes (same) | same |
| M6 | Y axis (rows) | `sweep_y_field` | selectbox | `range_width` | the five fields other than X (`app.py:1486`) | local `y_field` | same | yes (same) | same |
| M7 | Y values (comma-separated) | `sweep_y_values` | text_input | `0.02, 0.05, 0.1, 0.15, 0.25, 0.5` | free text | local `y_values` | same | yes (same) | same |
| M8 | Cancel sweep | `cancel_sweep_btn` | button | `False` | | `session_state["cancel_sweep"] = True` (`app.py:1544-1545`) | `cancel_cb` in `sweep_swap_level_axes` (`backtest.py:285`) | no: UI only | while the sweep runs (`app.py:1542-1545`; the placeholder is emptied at 1586) |
| M9 | Overlay runs (up to 5) | `None` | multiselect | last two run labels | labels of stored runs (`app.py:1814-1821`) | local `picked` | `_render_comparison` (`app.py:1805-1855`) | no: UI only | two or more runs in `session_state["runs"]` (`app.py:1807`) |

Containers that are not input widgets: top tabs `Backtest` / `Documentation` (`app.py:1862`); chart tabs `Cumulative` / `Daily bars` (`app.py:1385`); five documentation sub-tabs (`app.py:2022-2024`); expanders `Full summary`, `Timeseries (first 500 rows)` (`app.py:1452-1455`), `Full sweep table` (`app.py:1651`), `Full monthly table` (`app.py:1801`). Nine metric tiles are output only (`app.py:1293-1351`).

The by-month panel has no widgets of its own; it is driven by sidebar checkbox #54 and renders a Plotly bar chart plus a table (`app.py:1655-1802`). The stress shock has no main-area controls; it is applied from sidebar widgets #56 to #58 before the hourly engine runs (`app.py:1944-1954`).

---

## 2. Engines and adapter

### 2.1 Call tree

```
app.py:1869   run = st.button("Run backtest")
app.py:2000   if run: _execute_run()
app.py:1908   _execute_run()
app.py:1919     a = bt.Assumptions.from_dict(st.session_state["assumptions"])     backtest.py:108-111
app.py:1922     if engine_key == "swap_level":
app.py:1923       _load_swap_level_inputs()                                        app.py:1082-1115
app.py:1084         Demo:  _demo() -> bt.make_demo_tape()                          app.py:152-154, backtest.py:481-489
                             -> run_claude.make_demo_data()                        run_claude.py:363-450
app.py:1105         Real:  _load_swaps() -> bt.load_swaps() -> run_claude.load_swaps()      backtest.py:459, run_claude.py:276-281
app.py:1106                _load_cex()   -> bt.load_cex_prices() -> run_claude.load_cex_prices()  backtest.py:463, run_claude.py:295-315
app.py:1108                _load_funding_series() -> bt.load_funding_series() -> run_claude.load_funding()  backtest.py:467, run_claude.py:318-324
app.py:1099                (if no CEX file) _auto_fetch_missing_binance() -> bt.ensure_all_data()  app.py:1045-1079, backtest.py:868-903
app.py:1938       res = bt.run_swap_level(a, swaps, cex, funding)                  backtest.py:169-176
backtest.py:175     cfg = _to_claude_config(a)                                     backtest.py:119-136
backtest.py:176     run_claude.run_backtest(swaps, cex, funding, cfg)              run_claude.py:674-782
run_claude.py:675     build_position_schedule(cex, cfg)                            run_claude.py:543-580
run_claude.py:553,572   build_position(price, cfg, when, capital)                  run_claude.py:211-235
run_claude.py:676     attribute_swaps(swaps, cex, pos_frame, cfg)                  run_claude.py:458-535   (fees, LVR per swap)
run_claude.py:677     simulate_hedge(cex, pos_frame, positions, funding, cfg)      run_claude.py:588-659   (hedge trades, cost, funding)
run_claude.py:679-716 join, gas column, net_usd identity, cum_net
run_claude.py:719-775 capital base, APRs, Sharpe, drawdown, closed-form LVR check  (_closed_form_lvr run_claude.py:785-800)
backtest.py:176     _normalise_swap_level(res) -> BacktestResult                   backtest.py:139-166
app.py:1939     else (hourly):
app.py:1940       _load_hourly_inputs()                                            app.py:1118-1148
app.py:1120         Live:  _fetch_klines() -> bt.fetch_binance_klines() -> BinanceDataFetcher.get_perp_klines()   app.py:177-179, backtest.py:492-502, kimi/uniswap_delta_hedge_backtest.py:115-164
                           _fetch_funding() -> bt.fetch_binance_funding() -> BinanceDataFetcher.get_funding_rates()  app.py:182-184, backtest.py:505-513, kimi …:167-208
app.py:1131         Local: _load_cex() then resample to 1h OHLC, volume = quote_volume = 0.0     app.py:1131-1139
app.py:1141                _load_funding_df() -> bt.load_funding_df()              app.py:172-174, backtest.py:472-478
app.py:1945       (if stress on) _apply_stress_shock(klines, stress_cfg)           app.py:1876-1905
app.py:1967       res = bt.run_hourly(a, klines, funding)                          backtest.py:417-451
backtest.py:426     cfg = _to_kimi_config(a)                                       backtest.py:326-342
backtest.py:429-437 derive lower/upper from first close if not set
backtest.py:442-446 pool_volume_24h = quote_volume * pool_volume_multiplier * 24
backtest.py:448-450 DeltaHedgeBacktest(cfg).run(klines, funding); .summarize()    kimi/uniswap_delta_hedge_backtest.py:261-264, 266-457, 473-504
backtest.py:451     _normalise_hourly(results, summary, cfg) -> BacktestResult     backtest.py:345-414
app.py:1969     _push_run(res, run_label, st.session_state["assumptions"])         app.py:1182-1205
app.py:1982-1997 ResultRenderer(res): verdict, metrics, export_buttons, charts, rolling, raw_expanders   app.py:1230-1455
app.py:1992     (if by-month) _render_by_month()  -> bt.run_swap_level / bt.run_hourly per month   app.py:1655-1802
app.py:1995     (if sweep)    _render_sweep()     -> bt.sweep_swap_level_axes()    app.py:1516-1652, backtest.py:247-318
app.py:1997     _render_comparison()                                               app.py:1805-1855
```

`BacktestResult` (`backtest.py:48-58`) is the common envelope: `engine`, `summary` dict, `timeseries` DataFrame with columns `price, lp_value, lp_delta, hedge_delta, fee_usd, lvr_usd, funding_usd, hedge_cost_usd, gas_usd, net_usd, cum_net`, optional `attribution`, and `extras`.

### 2.2 `Assumptions` dataclass (`backtest.py:66-111`)

24 fields. Column **Maps to** gives the exact engine config field. `Config` is `run_claude.Config` (`run_claude.py:95-121`), built by `_to_claude_config` (`backtest.py:119-136`). `KimiConfig` is `kimi/uniswap_delta_hedge_backtest.py:225-246` `BacktestConfig`, built by `_to_kimi_config` (`backtest.py:326-342`).

| Field | Default (`backtest.py` line) | swap_level | hourly | Maps to |
|---|---|---|---|---|
| `capital_usd` | `1_000_000.0` (72) | yes | yes | `Config.capital_usd` (121); `KimiConfig.initial_capital_usd` (337) |
| `range_width` | `0.15` (73) | yes | yes, only when `lower_price`/`upper_price` are unset (`backtest.py:429-437`) | `Config.range_width` (122); hourly: derived `lower_price = p0*(1-w)`, `upper_price = p0*(1+w)` (434-435) |
| `lower_price` | `None` (75) | no | yes | `KimiConfig.lower_price` (338); `None` becomes `0.0`, which triggers the derivation above |
| `upper_price` | `None` (76) | no | yes | `KimiConfig.upper_price` (339) |
| `reposition` | `True` (77) | yes | no | `Config.reposition` (123) |
| `reposition_buffer_hours` | `6.0` (78) | yes | no | `Config.reposition_buffer_hours` (124) |
| `gas_usd_per_reposition` | `40.0` (79) | yes | no | `Config.gas_usd_per_reposition` (125) |
| `lp_rebalance_swap_bps` | `5.0` (80) | yes | no | `Config.lp_rebalance_swap_bps` (126) |
| `hedge_band` | `0.03` (83) | yes | no | `Config.hedge_band` (127) |
| `rebalance_threshold_pct` | `0.05` (84) | no | yes | `KimiConfig.rebalance_threshold` (335) |
| `rebalance_mode` | `"threshold"` (85) | no | yes | `KimiConfig.rebalance_mode` (334) |
| `rebalance_period_h` | `24` (86) | no | yes | `KimiConfig.rebalance_period` (336) |
| `taker_fee_bps` | `4.5` (87) | yes | no (only through `binance_taker_fee`) | `Config.taker_fee_bps` (128) |
| `binance_taker_fee` | `0.0004` (88) | no | yes | `KimiConfig.binance_taker_fee` (333). Kept equal to `taker_fee_bps / 10_000` by `app.py:648` and `backtest._normalise_assumptions_dict` (216-223). The dataclass defaults disagree: 4.5 bps versus 0.0004 = 4.0 bps. |
| `half_spread_bps` | `0.5` (89) | yes | no | `Config.half_spread_bps` (129) |
| `impact_bps_per_100k` | `0.8` (90) | yes | no | `Config.impact_coef_bps_per_100k` (130) |
| `slippage_bps` | `1.0` (91) | no | yes | `KimiConfig.slippage_bps` (341) |
| `leverage` | `4.0` (92) | yes | no | `Config.leverage` (131) |
| `margin_buffer` | `1.5` (93) | yes | no | `Config.margin_buffer` (132) |
| `grid_seconds` | `60` (96) | yes | no | `Config.grid_seconds` (133) |
| `markout_seconds` | `0` (97) | yes | no | `Config.markout_seconds` (134) |
| `pool_share` | `0.001` (98) | no | yes | `KimiConfig.our_share_of_pool` (340) |
| `pool_volume_multiplier` | `0.08` (99) | no | yes | not a config field; used in `run_hourly` to build the `pool_volume_24h` column (`backtest.py:442-446`) |
| `fee_tier` | `0.0005` (103) | yes | yes | `Config.fee_tier` (135); `KimiConfig.pool_fee_rate` (332) |

`Config.tick_spacing` (`run_claude.py:117`) is not in `Assumptions` and stays at 10 for every fee tier.

### 2.3 What each engine computes

**swap_level** (`run_claude.py`):

- Position: symmetric band around the first grid price, tick-aligned, liquidity `L = capital / value_per_unit_L` (`build_position`, 211-235). Repositioning walks the CEX tape on a `grid_seconds` grid and re-centres when price has been outside the band for `reposition_buffer_hours` (543-580).
- Fees: per swap, `fee = input_amount × fee_tier × share`, where `share = L_you / (L_active + L_you)` for in-range swaps (505-513). `L_active` is the `liquidity` column of the swap file.
- LVR: per swap, the taker's profit at the CEX mark on the fee-net execution price, times `share` (515-525). The CEX mark is the last 1-second price at or before `block_time + markout_seconds` (490-495).
- Hedge: on the `grid_seconds` grid, trade when `|lp_delta + hedge| > hedge_band × max_delta` (606-620). Cost = notional × (half_spread + taker) bps + notional × (notional / 1e5) × impact bps (622-625). Hedge PnL is the perp mark-to-market (628-629).
- Funding: at each funding stamp, `cash = -(hedge × price) × rate`; a short receives when the rate is positive (644-657).
- Net: `lp_pnl + hedge_pnl + fee + funding − hedge_cost − gas` (700-707). Capital base = `capital + max_notional / leverage × margin_buffer` (719-721). All APRs divide by that base and by `days / 365` (723-728).

**hourly** (`kimi/uniswap_delta_hedge_backtest.py`, via `backtest.run_hourly`):

- Position: fixed tick band from `lower_price`/`upper_price` (263-264); liquidity from a 50/50 USD split at the first close (297-305). No repositioning.
- Fees: only in range, `fees = pool_volume_24h × bar_hours / 24 × pool_fee_rate × our_share_of_pool` (339-349). `pool_volume_24h` is synthesised by `backtest.py:442-446` as `quote_volume × pool_volume_multiplier × 24`.
- LVR: not computed. `lvr_usd` is set to `0.0` and `lvr_apr_pct` to `0.0` in `_normalise_hourly` (`backtest.py:362, 382, 392`).
- Hedge: rebalance at bar 0, then by threshold (`|target − hedge| / |hedge| > rebalance_threshold`, 379-382) or every `rebalance_period` bars (375-376). Cost = notional × taker fee + notional × slippage bps (399-405). Hedge PnL is realised plus unrealised against an average entry price (392-396, 424-425).
- Funding: `funding_pnl = −hedge_eth × price × rate_8h × bar_hours / 8` (353-365). A missing funding table yields `0.0001` per 8 h (461-462, 471).
- Net: `lp_value − capital + hedge_realised + hedge_unrealised + fees + funding − binance_fees − slippage` (428-430). `net_apr` uses integer `days / 365.25` (476-479, 494) while `_normalise_hourly` computes the component APRs with `days / 365` (`backtest.py:348-353`). The component APRs therefore do not sum to the net APR. Capital base = `capital_usd` with no margin (`backtest.py:374-375`); `sharpe` is `None` (404).

### 2.4 Who imports what

Output of `grep -n "^import\|^from" app.py backtest.py run_claude.py run_kimi.py kimi/*.py`, saved verbatim in `docs/guide/raw/appmap_imports.txt`:

```
app.py:16:from __future__ import annotations
app.py:18:import contextlib
app.py:19:import json
app.py:20:import logging
app.py:21:import os
app.py:22:import time
app.py:23:import traceback
app.py:24:from copy import deepcopy
app.py:25:from pathlib import Path
app.py:27:import numpy as np
app.py:28:import pandas as pd
app.py:29:import plotly.graph_objects as go
app.py:30:import streamlit as st
app.py:32:import backtest as bt
backtest.py:22:from __future__ import annotations
backtest.py:24:import dataclasses
backtest.py:25:from dataclasses import dataclass, field
backtest.py:26:from pathlib import Path
backtest.py:27:from typing import Optional
backtest.py:29:import numpy as np
backtest.py:30:import pandas as pd
backtest.py:32:import run_claude
backtest.py:33:from kimi.uniswap_delta_hedge_backtest import (
run_claude.py:60:from __future__ import annotations
run_claude.py:62:import argparse
run_claude.py:63:import io
run_claude.py:64:import math
run_claude.py:65:import os
run_claude.py:66:import sys
run_claude.py:67:import zipfile
run_claude.py:68:from dataclasses import asdict, dataclass, field
run_claude.py:69:from datetime import datetime, timezone
run_claude.py:70:from typing import Iterable, Optional
run_claude.py:72:import numpy as np
run_claude.py:73:import pandas as pd
run_kimi.py:10:import json
run_kimi.py:11:import time
run_kimi.py:12:from dataclasses import dataclass
run_kimi.py:13:from datetime import datetime, timedelta
run_kimi.py:14:from typing import Literal, Optional, Tuple
run_kimi.py:16:import numpy as np
run_kimi.py:17:import pandas as pd
run_kimi.py:18:import requests
kimi/demo_backtest.py:8:import pandas as pd
kimi/demo_backtest.py:9:import numpy as np
kimi/demo_backtest.py:10:import matplotlib.pyplot as plt
kimi/demo_backtest.py:11:from dataclasses import dataclass
kimi/demo_backtest.py:12:from typing import Tuple, Literal
kimi/demo_backtest.py:13:from datetime import datetime, timedelta
kimi/uniswap_delta_hedge_backtest.py:10:import pandas as pd
kimi/uniswap_delta_hedge_backtest.py:11:import numpy as np
kimi/uniswap_delta_hedge_backtest.py:12:import requests
kimi/uniswap_delta_hedge_backtest.py:13:import time
kimi/uniswap_delta_hedge_backtest.py:14:from dataclasses import dataclass
kimi/uniswap_delta_hedge_backtest.py:15:from typing import Optional, Tuple, Literal
kimi/uniswap_delta_hedge_backtest.py:16:from datetime import datetime, timedelta
kimi/uniswap_delta_hedge_backtest.py:17:import json
kimi/uniswap_real_data_backtest.py:13:import pandas as pd
kimi/uniswap_real_data_backtest.py:14:import numpy as np
kimi/uniswap_real_data_backtest.py:15:import requests
kimi/uniswap_real_data_backtest.py:16:import time
kimi/uniswap_real_data_backtest.py:17:from dataclasses import dataclass
kimi/uniswap_real_data_backtest.py:18:from typing import Optional, Tuple, Literal
kimi/uniswap_real_data_backtest.py:19:from datetime import datetime, timedelta
kimi/uniswap_real_data_backtest.py:20:import json
kimi/uniswap_real_data_fetcher.py:12:import requests
kimi/uniswap_real_data_fetcher.py:13:import pandas as pd
kimi/uniswap_real_data_fetcher.py:14:import numpy as np
kimi/uniswap_real_data_fetcher.py:15:from datetime import datetime, timedelta
kimi/uniswap_real_data_fetcher.py:16:import time
kimi/uniswap_real_data_fetcher.py:17:from typing import Optional, List
```

The only project-module imports are `app.py:32` (`backtest`), `backtest.py:32` (`run_claude`) and `backtest.py:33` (`kimi.uniswap_delta_hedge_backtest`). Neither `run_perplexity` nor `run1` appears. A repo-wide grep for `run_perplexity` and `\brun1\b` over Python, TOML, Makefile, Procfile and Markdown files returns one hit, `Makefile:9: .venv/bin/python run_claude.py --demo --demo-days 25 --sweep --out run1`, where `run1` is only an output-file prefix.

Conclusions:

- `run_perplexity.py` is imported by nothing. It is a standalone script (`run_perplexity.py:1-48` config block, `backtest()` at line 376) that pulls Binance 1h klines and funding plus Uniswap `poolHourData` and tick data from the legacy hosted subgraph URL (`run_perplexity.py:40`), and simulates an hourly hedged LP with fee income estimated from `feeGrowthInside`. It writes to `output/`.
- `run1.py` is imported by nothing. It is an earlier standalone variant of the same idea (`run1.py:1-46`, `backtest()` at line 305) using `poolHourData` only, fee income from `feesUSD × active_liquidity_share`, and the same legacy subgraph URL (`run1.py:40`). The files `run1_*.csv` and `run1_pnl.png` in the repo root are outputs of `make demo-cli`, which runs `run_claude.py --demo`, not `run1.py` (`Makefile:7-9`).
- `run_kimi.py` is also imported by nothing. It is a reformatted copy of `kimi/uniswap_delta_hedge_backtest.py` (`diff -w` shows only whitespace and import-order differences).

---

## 3. Documentation tab text and contradictions

Source of the text: the AppTest run, direct children of each sub-tab, saved by `docs/guide/raw/appmap_doc_tabs_extract.py` to `docs/guide/raw/appmap_doc_tabs.txt`. The strings are literal markdown in `app.py:2010-2186`; the rendered text is identical to the source strings.

### 3.1 Top-level text (`app.py:2010-2020`)

```
This app backtests a **delta-hedged Uniswap v3 LP position**. You provide liquidity to ETH/USDC inside a price band, and short an equivalent amount of ETH on Binance perpetual futures so your value stays roughly independent of ETH price. The question is whether the fees you earn on-chain cover LVR (loss-vs-rebalancing), funding, and the cost of running the hedge.
> **Net PnL = Fees − LVR − Funding cost − Hedge execution − Gas**
```

### 3.2 Sub-tab `Engines` (`app.py:2026-2043`)

```
| | swap_level (reference) | hourly (fast fallback) |
|---|---|---|
| Data granularity | Swap-level pool events + 1s CEX | Hourly OHLC only |
| LVR | Path-exact from swap-level events | Not modelled |
| Hedge execution | Half-spread + taker fee + linear impact | Flat bps slippage |
| Repositioning | Buffer-based, gas + swap cost modelled | Not modelled |
| Data required | Swap parquet + Binance klines/aggTrades | Binance klines only |
| Speed | Slower (millions of events) | Fast |
| Best for | Accurate PnL attribution | Quick parameter exploration |

The swap-level engine has a **Demo (synthetic tape)** mode that generates
GBM prices and simulated swap events — useful for validating the engine or
exploring parameters without downloading anything.
```

### 3.3 Sub-tab `Data` (`app.py:2045-2064`)

```
- **Uniswap swaps** — parquet with per-swap events. Two ways to obtain:
  1. **Auto-fetch from the subgraph** via **Fetch data → Fetch swap
     events**. Requires `THEGRAPH_API_KEY` in your env
     ([free key here](https://thegraph.com/studio/)).
     Slow — ~2–3M events per month for ETH/USDC 0.05%.
  2. Or drop your own `*swap*.parquet` dump in the repo root or `data/`
     — cryo, Dune, and Allium exports all work. Required columns:
     `block_number, block_time, amount0, amount1, sqrt_price_x96,
     liquidity, tick` (aliases handled automatically).
- **Binance klines / aggTrades** — auto-downloaded from
  `data.binance.vision`. Cached in `data/`. `load_cex_prices` handles
  both formats and resamples to 1-second bars internally.
- **Binance funding rates** — 8-hour funding history, auto-downloaded.
- **Uniswap pool hourly aggregates** — optional, needs a Graph API key.
  Disabled by default; 1-minute klines suffice for exploration.
```

### 3.4 Sub-tab `Inputs` (`app.py:2066-2117`)

```
### Fetch data
- **From / To (YYYY-MM)** — month range for Binance CSV downloads. Months
  already cached on disk are skipped.
- **CEX price resolution** — klines 1m (default, ~5 MB/mo) / klines 1h
  (smallest) / aggTrades (~3.5 GB/mo, only for path-exact LVR).

### Backtest window
- Optional YYYY-MM-DD start/end. Clips *all* input series before handing
  them to the engine, so you can backtest a slice of downloaded data
  without re-downloading.

### Engine
- **swap_level** (reference, needs swap events) vs **hourly** (fast
  fallback, klines only).

### LP leg
- **Capital deployed** — notional put into the LP position.
- **Range width** — position covers `[spot × (1−w), spot × (1+w)]`.
  Tighter = more fees per dollar, but exits range more often.
- **Absolute price bounds** (hourly only) — pin the range.
- **Reposition** — re-centre after N hours out of range. Costs gas +
  swap slippage.
- **Pool share** / **volume multiplier** (hourly) — proxies for fees
  when we don't have real pool volume data.

### Hedge leg
- **Hedge band** (swap_level) — rebalance when residual delta exceeds
  this fraction of max ETH delta.
- **Rebalance mode** (hourly) — *threshold* (drift %) or *periodic*
  (fixed hours).
- **Binance taker fee** — perp taker fee (bps).
- **Half-spread** (swap_level) — bid-ask approximation when using klines.
- **Linear impact** (swap_level) — market impact model for large clips.
- **Flat slippage** (hourly) — simpler analog.
- **Perp leverage / margin buffer** — `margin = notional / leverage × buffer`.

### Mechanics (swap_level)
- **Hedge decision grid (s)** — how often the hedge is checked.
- **Fee markout window (s)** — CEX mark delay for measuring fill quality.

### LP fee tier — 1 / 5 / 30 / 100 bps.

### Run controls
- **Range × hedge-band sweep** — Cartesian grid.
- **Rolling-window distribution** — split into 30-day windows, plot APR
  variation across them (regime dependency check).
- **Run label** — annotates this run in the comparison table.
```

### 3.5 Sub-tab `Outputs` (`app.py:2119-2162`)

```
### Verdict banner
Green if net APR > 0 (fees cover costs), red otherwise. Shows the
capital-base breakdown so you can see how much of it is LP vs hedge
margin.

### Top-row metrics
- **Net PnL (USD)** — total PnL over the backtest window.
- **Fees APR** — annualised fee income as % of capital base.
- **LVR APR** — annualised loss-vs-rebalancing. Path-exact in
  swap_level, n/a in hourly.
- **Funding APR** — annualised funding paid/received on the perp.
  Positive = you collect funding.
- **Hedge cost APR** — taker fees + spread + impact + slippage.

### Second-row metrics
- **Fees / LVR** — coverage ratio.
- **% time in range** — fees only accrue when in-range.
- **Sharpe (daily)** — daily-net-PnL Sharpe, annualised by √365.
- **Max drawdown** — worst peak-to-trough dip.

### Charts
- **Cumulative PnL** — running sum of net PnL.
- **PnL attribution** — cumulative (line) or daily (stacked bar).
  Cumulative shows which term is winning; daily shows which day cost
  or earned what.
- **ETH price** — reference price used to compute delta and mark hedge.
- **LP delta vs Hedge delta** — should track closely; gaps are
  discrete-hedge error.
- **30-day rolling APR** (if enabled) — regime dependency check.

### Sweep (swap_level only)
Interactive Plotly heatmap over `range_width × hedge_band`. Hover shows
per-cell Net APR, Sharpe, Fees/LVR. Callout points at the best cell.

### Run comparison
Overlay of cumulative-PnL curves for up to 5 stored runs, plus a KPI
table. Runs are pushed onto a bounded stack in session state; the last
five persist across button clicks in the same session (not across page
reloads).
```

### 3.6 Sub-tab `Interpreting` (`app.py:2164-2186`)

```
- If **Fees APR < |LVR APR|**, the position loses money to informed
  flow faster than it earns from uninformed flow — no hedge
  configuration can save it.
- If **Fees APR > |LVR APR|** but **Net APR < 0**, hedge costs or
  funding are eating the surplus. Try wider hedge band, higher fee
  tier, or a period with more favourable funding.
- **% time in range** below ~80% usually means the range is too tight
  for the vol regime. Widen it and re-run.
- The **sweep** helps: look for the cell with the highest net APR *and*
  reasonable Sharpe — a great APR with negative Sharpe means one lucky
  window carried the result.
- The **rolling-APR distribution** shows regime dependency: a strategy
  with median APR ≈ 0 but wide spread is a coin flip, not an edge.
- Use **run comparison** to A/B-test parameter changes without losing
  the baseline.
```

Below that text the tab shows a divider, the subheader `Assumptions dataclass (source of truth)`, and `st.help(bt.Assumptions)` (`app.py:2184-2186`), which renders the class signature with all 24 defaults and the docstring `Everything the UI can twiddle. Superset of what either engine needs; each engine picks the fields it cares about.` (`docs/guide/raw/appmap_help_info.txt`).

### 3.7 Contradictions between the text and the code

Each item: the quoted sentence, where it is rendered, the code, and the correct statement. Items marked **verified by run** have a numeric check in `docs/guide/raw/`.

**C1. `Swaps to` is exclusive, not inclusive.**
Text: `Last day to fetch (inclusive).` (help of `Swaps to`, `app.py:281`). Also `ensure_pool_swaps` docstring `Download individual pool swaps for [start, end]` (`backtest.py:836`).
Code: `end_ts = int(pd.Timestamp(end, tz="UTC").timestamp())` is midnight at the start of the `end` date (`backtest.py:754`), and the query filters `timestamp_lt: $ts_lt` (`backtest.py:738`, bound at 767).
Correct: swaps are fetched for `[start 00:00, end 00:00)`. The `Swaps to` day itself is excluded. The default `2024-01-01` to `2024-01-08` covers seven days, not eight.

**C2. "~2–3M swaps per month" is an order of magnitude too high.**
Text: `Slow: ~2–3M swaps/month for ETH/USDC 0.05%` (`app.py:270`), `~2–3M swaps per month for ETH/USDC 0.05%` (`app.py:283`), `Slow — ~2–3M events per month for ETH/USDC 0.05%` (Data tab, `app.py:2052`).
Evidence: the eight swap slices fetched in Phase 1 (`docs/guide/raw/swaps_slice_0..7.parquet`) hold 159,798 swaps between epoch 1785542447 and 1788220799, which is 31.0 days (2026-07-31 to 2026-08-31). No code computes or supports the 2–3M figure.
Correct: the pool produced about 160 k swaps in the sampled month. Fetch time is dominated by 1000-row pages (`backtest.py:732`), so about 160 pages per month.

**C3. The documented subgraph auto-fetch does not work.**
Text: `Auto-fetch from the subgraph via Fetch data → Fetch swap events` (Data tab, `app.py:2049-2050`), and the expander caption `Fetched from the Uniswap v3 subgraph via The Graph gateway` (`app.py:266-267`).
Code: `fetch_pool_swaps` requests the field `liquidity` on every `Swap` (`backtest.py:748`). The gateway rejects the query with `Type Swap has no field liquidity` (`docs/guide/raw/schema_probe.json`, recorded in `docs/guide/ENVIRONMENT.md`). The loader then requires that column (`run_claude.py:248, 262-266`).
Correct: clicking `Fetch swap events` raises on every call at commit `e1bb6e7`. Swap files must come from another source, and the per-swap `liquidity` column must be obtained elsewhere (the Phase 1 slices lack it and would fail `load_swaps`).

**C4. "via The Graph gateway" and "still works".**
Text: `Fetched from the Uniswap v3 subgraph via The Graph gateway — requires THEGRAPH_API_KEY` (`app.py:266-268`). Docstring: `Tries the legacy hosted endpoint first (still works for read-only, no key)` (`backtest.py:588-589`).
Code: `_query_uniswap_subgraph` posts first to `https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3` (`backtest.py:595-597`), then to the gateway only when the key is set (598-604). The final error always says `Set THEGRAPH_API_KEY in your env to use the gateway` (620-623), even when the key was set and the gateway itself returned the error.
Correct: the legacy endpoint returns `301` to an error page (`ENVIRONMENT.md`, network table); every call spends one failed request there before reaching the gateway. The error text is misleading when the key is present.

**C5. File-size figures.**
Text: `aggTrades (~3.5 GB/month)` (`app.py:221`; Inputs tab `app.py:2073`; `backtest.py:547, 882`), `klines 1h (~200 KB/month)` (`app.py:220`).
Evidence: `data/ETHUSDT-aggTrades-2026-08.csv` is 2,187,210,885 bytes (2.19 GB); `data/ETHUSDT-klines-1h-2026-08.csv` is 89,791 bytes; `data/ETHUSDT-klines-1m-2026-08.csv` is 4,947,296 bytes (the `~5 MB` figure is right).
Correct: the 1h figure is about 90 KB per month and the aggTrades figure is volume-dependent and was 2.2 GB for the month on disk.

**C6. Pool hourly aggregates are not optional from the UI.**
Text: `Uniswap pool hourly aggregates — optional, needs a Graph API key. Disabled by default` (Data tab, `app.py:2061-2062`).
Code: both calls to `bt.ensure_all_data` pass `include_pool_hourly=False` as a literal (`app.py:251, 1065`). No widget sets it. Nothing in the engines reads a pool-hourly file.
Correct: the app never downloads or uses pool hourly aggregates. The `data/pool_hourly_*.parquet` on disk came from a direct call outside the app.

**C7. The backtest window is ignored in hourly Live mode.**
Text: `Clips *all* input series before handing them to the engine` (Inputs tab, `app.py:2076-2077`); expander caption `If set, series are clipped to [start, end] before the engine sees them` (`app.py:990-991`).
Code: `_load_hourly_inputs` returns the live klines and funding directly without calling `_clip_series` (`app.py:1119-1120`). Clipping is applied only in the file-based branches (`app.py:1105-1108, 1131, 1141`).
Correct: in `hourly + Live fetch from Binance`, `Window start` and `Window end` have no effect.

**C8. `Window end` excludes the end day.**
Text: `Clip the loaded data to end on this date (inclusive).` (`app.py:1003`).
Code: `s1 = pd.Timestamp(end, tz="UTC")` is midnight at the start of the end date, and the clip is `s.loc[s0:s1]` (`app.py:1032-1033`) or `block_time <= s1` (`app.py:1041`).
Correct: the window ends at `end 00:00:00`. Only the first instant of the end day can be included.

**C9. Leverage and margin buffer do nothing in the hourly engine.**
Text: `Perp leverage / margin buffer — margin = notional / leverage × buffer` (Inputs tab, `app.py:2103`), `Capital deployed … Together with Perp leverage × margin buffer, this determines the capital base used as the APR denominator` (`app.py:518-521`), `Perp leverage … Margin required = max_notional / leverage × margin_buffer` (`app.py:616-618`).
Code: `_to_kimi_config` passes neither field (`backtest.py:331-342`). `_normalise_hourly` sets `capital_base_usd = round(cap)` with no margin (`backtest.py:374-375`). Both widgets are rendered for both engines (`app.py:767`).
Correct: the formula holds only for swap_level (`run_claude.py:719-721`). In hourly mode the two widgets are dead, the capital base equals `Capital deployed`, and the verdict caption shows `hedge margin $0`.

**C10. Hourly hedge execution is taker fee plus slippage, not slippage alone.**
Text: `Hedge execution | … | Flat bps slippage` (Engines table, `app.py:2033`).
Code: `binance_fee = trade_notional * binance_taker_fee` (`kimi …backtest.py:400`) and `slippage_cost = trade_notional * slippage_bps / 10000` (404); both are summed into `hedge_cost_usd` (`backtest.py:364`).
Correct: hourly hedge cost = taker fee + flat slippage.

**C11. Hourly Local CSV mode always reports zero fees. Verified by run.**
Text: `Pool share / volume multiplier (hourly) — proxies for fees when we don't have real pool volume data` (Inputs tab, `app.py:2091-2092`); `Fees earned = pool_volume × fee_tier × share` (`app.py:553`); `it multiplies Binance quote volume by this` (`app.py:561-562`).
Code: `_load_hourly_inputs` sets `klines["quote_volume"] = 0.0` (`app.py:1139`), so `pool_volume_24h = 0` (`backtest.py:444-446`) and `fees_earned = 0` (`kimi …backtest.py:344, 348`). `docs/guide/raw/appmap_hourly_local_check.txt`: `fees_usd = 0` with `pool_share=0.01`, `multiplier=0.5`.
Correct: with `hourly + Local CSV files` the fee leg is identically zero regardless of these two widgets. Fees are non-zero only in Live mode, where Binance supplies `quote_volume` (`kimi …backtest.py:157, 164`).

**C12. The volume proxy assumes 1-hour bars. Verified by run.**
Text: `Bar interval … 1h is standard` (`app.py:965-966`) and the volume-multiplier help above.
Code: `pool_volume_24h = quote_volume * multiplier * 24` (`backtest.py:445`) is later multiplied by `bar_hours / 24` (`kimi …backtest.py:344`). The `24` cancels only when `bar_hours = 1`. `appmap_hourly_local_check.txt`: same total volume on 4h bars gives 3.96× the fees of 1h bars.
Correct: with `Bar interval = 4h` fees are overstated 4×, with `1d` 24×.

**C13. `Rebalance every (hours)` counts bars.**
Text: label `Rebalance every (hours)` and help `force a rebalance every N hours` (`app.py:605-611`); Inputs tab `periodic (fixed hours)` (`app.py:2097-2098`).
Code: `if i - last_rebalance_idx >= self.cfg.rebalance_period` (`kimi …backtest.py:376`), where `i` is the bar index.
Correct: the value is a bar count. It equals hours only when `Bar interval = 1h`.

**C14. A missing funding file in hourly Local mode does not mean zero funding. Verified by run.**
Text: `Funding file … Empty = funding assumed zero.` (`app.py:983-984`); Engines table `Data required … Binance klines only` (`app.py:2035`); engine help `hourly just needs Binance klines` (`app.py:873`).
Code: the app passes `pd.DataFrame({"fundingRate": []})` (`app.py:1143`); `_get_funding_rate` returns `0.0001` for an empty frame (`kimi …backtest.py:461-462`). `appmap_hourly_local_check.txt`: `funding_usd = 2957`, `funding_apr_pct = 5.68` with no funding file; `0` with an explicit zero series.
Correct: with no funding file the hourly engine assumes +0.01 % per 8 h (about 11 % per year on the hedge notional), credited to the short. The swap_level path is correct: `(none — assume 0)` yields an empty Series and zero funding (`app.py:1110`, `run_claude.py:647`).

**C15. Half-spread is charged for every CEX source, not only klines.**
Text: label `Half-spread when using klines (bps)` (`app.py:652`), help `Because klines only give last-trade price…` (`app.py:654-656`), Inputs tab `Half-spread (swap_level) — bid-ask approximation when using klines` (`app.py:2100`).
Code: `cost = notional * (half_spread_bps + taker_fee_bps) / 1e4` unconditionally (`run_claude.py:624`). Nothing in `run_claude.py` knows whether the CEX series came from klines or aggTrades.
Correct: the half-spread is always charged. Set it to 0 by hand if the CEX file is aggTrades and you want no spread charge.

**C16. "Path-exact" LVR depends on the CEX file.**
Text: `LVR | Path-exact from swap-level events` (`app.py:2032`), `swap_level is path-exact` (`app.py:871`), `LVR APR … Path-exact in swap_level` (`app.py:2130`), `aggTrades … path-exact LVR` (`app.py:221`, `2073`).
Code: LVR marks each swap at the last CEX price at or before `block_time + markout_seconds` (`run_claude.py:491-495`). With a klines file the CEX series is the bar close forward-filled to 1 s (`run_claude.py:307-315`), so the mark is stale by up to one bar. The module docstring itself says klines give `noisier LVR` (`run_claude.py:42`).
Correct: fees are exact per swap for any CEX file. LVR is exact only with aggTrades. The recommended default `klines 1m` gives a mark up to 60 s stale, and `klines 1h` up to one hour.

**C17. The sweep is not fixed to range × hedge band and not 25 cells.**
Text: checkbox `Run range × hedge-band sweep` with help `Slow (25 backtests on real data can take minutes)` (`app.py:787-790`); Inputs tab `Range × hedge-band sweep — Cartesian grid` (`app.py:2112`); Outputs tab `heatmap over range_width × hedge_band` (`app.py:2153`).
Code: the axis pickers accept any two of six fields (`app.py:1458-1513`, `backtest.py:182-213`). Default grids are 5 hedge bands × 6 range widths = 30 cells (`backtest.py:185, 190`); the AppTest run logged `total_cells=30` (`docs/guide/raw/appmap_apptest_stderr.txt`).
Correct: the sweep is over any two of `range_width, hedge_band, fee_tier, taker_fee_bps, leverage, grid_seconds`; the default run is 30 backtests.

**C18. The verdict has three states, not two.**
Text: `Green if net APR > 0 (fees cover costs), red otherwise.` (`app.py:2123`).
Code: when `days < 30` the banner is orange `INSUFFICIENT WINDOW` regardless of sign (`app.py:1213, 1259-1262`). The AppTest run rendered `### :orange[INSUFFICIENT WINDOW (5d < 30d)] — net **21.28% APR**` (`appmap_main_after_run.txt`).
Correct: orange below 30 days; otherwise green if net APR > 0, red if not.

**C19. Hourly LVR APR renders `0.00%`, not `n/a`; Fees/LVR renders `nan`; Sharpe renders `None`.**
Text: `LVR APR — … n/a in hourly` (`app.py:2130-2131`).
Code: `_normalise_hourly` sets `lvr_apr_pct = 0.0` (`backtest.py:392`), and the metric prints `n/a` only when the value is `None` (`app.py:1301`). `fee_over_lvr` is `float("nan")` (`backtest.py:383`) and is printed through an f-string (`app.py:1330`). `sharpe` is `None` (`backtest.py:404`) and `s.get("sharpe", "n/a")` returns the stored `None`, printed as `None` (`app.py:1342`).
Correct: in hourly mode the tiles read `0.00%`, `nan` and `None`.

**C20. Reposition costs are charged twice in swap_level. Verified by run.**
Text: `Reposition — re-centre after N hours out of range. Costs gas + swap slippage.` (`app.py:2089-2090`), `Repositioning | Buffer-based, gas + swap cost modelled` (`app.py:2034`), `Costs gas + LP swap slippage per reposition` (`app.py:569-570`).
Code: `build_position_schedule` writes the capital down by `capital × lp_rebalance_swap_bps / 1e4 + gas_usd_per_reposition` before building the new position (`run_claude.py:569-572`). That write-down lowers `lp_value_usd` (639) and enters `net_usd` through `lp_pnl_usd` (699-701). Then `run_backtest` adds `gas_usd_per_reposition + capital_usd × lp_rebalance_swap_bps / 1e4` to the `gas_usd` column at each reposition (690-695) and subtracts `gas_usd` again in `net_usd` (706). `docs/guide/raw/appmap_reposition_check.txt` (demo tape, 2 % range, 1 h buffer): 13 repositions, write-down total 6,763 USD, `gas_usd` column total 7,020 USD, both subtracted.
Correct: each reposition costs about twice the stated amount in `net_usd`. The `gas_usd` column also uses the original `capital_usd` rather than the current position value (694), so the second charge does not shrink as capital erodes. The `Gas` series in the attribution chart shows the second charge only.

**C21. Run controls list is incomplete.**
Text: `### Run controls` lists sweep, rolling window and run label (`app.py:2111-2115`).
Code: the same expander also renders `Backtest by month` (`app.py:798`) and, for hourly, `Stress test (price shock)` with three sub-controls (`app.py:807-831`).
Correct: five run controls exist, two of which are undocumented.

**C22. `LP fee tier` is applied to fees but not to tick spacing or the demo tape.**
Text: `LP fee tier — 1 / 5 / 30 / 100 bps.` (`app.py:2109`); help `Uniswap pool fee tier. 1 = 0.01%, 5 = 0.05%, 30 = 0.30%, 100 = 1.00%` (`app.py:636-637`).
Code: `Config.tick_spacing` is fixed at 10 (`run_claude.py:84, 117`) and is not in `Assumptions`; `build_position` aligns the band to that spacing (`run_claude.py:223-228`). `make_demo_data` generates swaps with the constant `FEE_TIER = 0.0005` (`run_claude.py:83, 405, 427`) whatever the widget says.
Correct: changing the tier changes only the fee rate credited and the fee-net execution price. The tick grid stays that of the 0.05 % pool, and in Demo mode the synthetic takers keep paying 0.05 %.

**C23. Page caption says "symmetric".**
Text: `Sizes a symmetric LP position` (`app.py:92`).
Code: hourly `Absolute price bounds` pins arbitrary `lower_price`/`upper_price` (`app.py:738-750`, `backtest.py:338-339`).
Correct: symmetric for swap_level; symmetric or absolute for hourly.

### 3.8 Sidebar help strings checked against the code

Every sidebar help string was compared with what the code does with the value. Strings not listed here are consistent with the code (`app.py` lines given in section 1.3). The ones that are not:

| Widget | Help text problem | Item |
|---|---|---|
| Swaps to (YYYY-MM-DD) | says inclusive; end day excluded | C1 |
| Swaps to / Pool swap events caption | 2–3M swaps/month | C2, C3 |
| CEX price resolution | aggTrades size; "path-exact" | C5, C16 |
| Window end (YYYY-MM-DD) | says inclusive; end day excluded | C8 |
| Backtest window caption | not applied in hourly Live mode | C7 |
| Capital deployed (USD) | capital-base sentence holds only for swap_level | C9 |
| Perp leverage / Margin buffer | dead in hourly | C9 |
| Our share of pool active liquidity / Pool volume as fraction… | zero effect in hourly Local mode; wrong scaling for 4h/1d bars | C11, C12 |
| Rebalance every (hours) | counts bars | C13 |
| Funding file (hourly Local) | "Empty = funding assumed zero" is false | C14 |
| Half-spread when using klines (bps) | always applied | C15 |
| Backtest engine | "path-exact" | C16 |
| Run range × hedge-band sweep | any two fields; 30 cells not 25 | C17 |
| Gas per reposition / Swap cost to re-ratio | charged twice | C20 |
| LP fee tier (bps) | tick spacing and demo tape unchanged | C22 |
| Reposition buffer (hours) | `0 = reposition immediately` is one grid step late: the first out-of-range step only records `out_since` and `continue`s (`run_claude.py:565-566`); the reposition fires on the next grid step (567). With the default 60 s grid the lag is 60 s. Not counted as a contradiction. | |

Help strings that are correct and worth knowing: `Hedge band` (`run_claude.py:610, 617`), `Rebalance threshold` (`kimi …backtest.py:379-382`, fraction of the current hedge size), `Fee markout window` (`run_claude.py:491`), `Hedge decision grid` (`run_claude.py:548, 596`), `Linear impact` formula (`run_claude.py:625`), `Binance taker fee … Applies to every hedge rebalance trade` (`run_claude.py:624`, `kimi …backtest.py:400`), `Funding APR … Positive = you collected` (`run_claude.py:655`, `kimi …backtest.py:364`), `Backtest by month … Needs ≥ 2 months` (`app.py:1691`), `Rolling-window … overlapping 30-day windows` (`app.py:1176-1179`), `Stress test … Hourly-engine only` (`app.py:806, 1944`).

**Total contradictions listed: 23 (C1 to C23).**

---

## 4. State and caching

### 4.1 `st.session_state` keys

Set by the app itself:

| Key | Set at | Content |
|---|---|---|
| `fetch_cfg` | `app.py:112` (default `DEFAULT_FETCH`, `app.py:101-108`) | dict `start, end, cex_kind, klines_interval, swap_start, swap_end`; mutated in place by `_sidebar_fetch` (`app.py:204-236, 273-279`); reset at `app.py:429` |
| `assumptions` | `app.py:113` (`bt.Assumptions().to_dict()`) | the 24-field dict every assumption widget writes into; replaced by upload (`app.py:421`) and reset (`app.py:428`) |
| `runs` | `app.py:114` | list of stored run entries, capped at 5 (`app.py:1204-1205`) |
| `cancel_swap_fetch` | `app.py:115`; set `True` by the Cancel button (`app.py:290`), cleared at `app.py:304, 354` | bool polled during the swap fetch |
| `_last_upload_id` | `app.py:422` | id of the last processed config upload, so a rerun does not re-apply it (`app.py:392`) |
| `_run_controls` | `app.py:849-855` | dict `do_sweep, do_rolling, do_by_month, run_label, stress_cfg`, the bridge out of the `_sidebar_assumptions` fragment; read at `app.py:1009-1020` |
| `cancel_sweep` | `app.py:1537-1538`; set `True` by `Cancel sweep` (`app.py:1545`) | bool polled by the sweep |

Widget-owned keys (created by Streamlit for widgets declared with `key=`): `fetch_start, fetch_end, fetch_cex_choice, fetch_binance_btn, fetch_swap_start, fetch_swap_end, fetch_swaps_btn, fetch_swaps_cancel, cfg_upload, win_start, win_end, sweep_x_field, sweep_x_values, sweep_y_field, sweep_y_values, cancel_sweep_btn`. The full list observed after two runs is in `docs/guide/raw/appmap_apptest_stdout.txt` under `session_state keys after runs`. `Reset all to defaults` deletes the keys starting with `fetch_`, `win_` and `cfg_upload` (`app.py:431-433`).

### 4.2 `st.cache_data` loaders (`app.py:152-184`)

| Function | Cache key | Wraps |
|---|---|---|
| `_demo(days, s0, vol, seed)` | the four numbers | `bt.make_demo_tape` → `run_claude.make_demo_data` |
| `_load_swaps(path)` | path string | `bt.load_swaps` → `run_claude.load_swaps` |
| `_load_cex(path)` | path string | `bt.load_cex_prices` → `run_claude.load_cex_prices` (1 s resample) |
| `_load_funding_series(path)` | path string | `bt.load_funding_series` → `run_claude.load_funding` (Series) |
| `_load_funding_df(path)` | path string | `bt.load_funding_df` (DataFrame with `fundingRate`) |
| `_fetch_klines(days, interval)` | days, interval | `bt.fetch_binance_klines` (live Binance API) |
| `_fetch_funding(days)` | days | `bt.fetch_binance_funding` (live Binance API) |

The file loaders key on the path only; the comment at `app.py:146-148` states that files are treated as immutable once written. A re-downloaded or edited file with the same name is served from cache until the process restarts. The live fetchers key on `(days, interval)`, so a second Live run with the same lookback reuses the first download for the life of the process.

The engines themselves are not cached. Every `Run backtest` click reruns the engine (`app.py:1938, 1967`).

### 4.3 What `_push_run` stores (`app.py:1182-1205`)

One dict per run, appended to `session_state["runs"]`, list trimmed to the last 5:

| Field | Value |
|---|---|
| `label` | the `Run label` text, or `Run N` where N is the position in the list at push time (`app.py:1197`) |
| `engine` | `res.engine` (`swap_level` or `hourly`) |
| `summary` | `deepcopy(res.summary)` |
| `daily_cum_net` | `res.timeseries["cum_net"].resample("1D").last().ffill()` (`app.py:1192-1195`); the full timeseries is not kept |
| `params` | `deepcopy(st.session_state["assumptions"])`, the 24-field dict as it was at run time (`app.py:1969, 1201`) |
| `when` | `pd.Timestamp.now(tz="UTC")` |

`_render_comparison` (`app.py:1805-1855`) uses `label`, `engine`, `summary` and `daily_cum_net`. It plots the selected `daily_cum_net` series on one line chart and builds a KPI table from `summary` keys `net_apr_pct, fee_apr_pct, lvr_apr_pct, funding_apr_pct, hedge_cost_apr_pct, sharpe, pct_time_in_range`, coercing missing or `None` values to NaN with `_num` (`app.py:1156-1169`). `params` is stored but nothing reads it. Runs live only in the browser session; a page reload starts a new session with an empty list.

### 4.4 `Save / load config` (`app.py:357-434`)

**Download.** `Download current config (JSON)` serves `backtest_config.json`, MIME `application/json`, containing:

```
{
  "schema_version": 1,
  "assumptions": { ...24 fields... }
}
```

`schema_version` is `CONFIG_SCHEMA_VERSION = 1` (`app.py:87`). The `assumptions` object is `st.session_state["assumptions"]` verbatim (`app.py:365-369`). The default document is saved in `docs/guide/raw/appmap_config_default.json`; its keys, in order, are `capital_usd, range_width, lower_price, upper_price, reposition, reposition_buffer_hours, gas_usd_per_reposition, lp_rebalance_swap_bps, hedge_band, rebalance_threshold_pct, rebalance_mode, rebalance_period_h, taker_fee_bps, binance_taker_fee, half_spread_bps, impact_bps_per_100k, slippage_bps, leverage, margin_buffer, grid_seconds, markout_seconds, pool_share, pool_volume_multiplier, fee_tier`. `fetch_cfg`, the engine choice, the data-source choices, the backtest window and the run controls are **not** saved.

**Load.** `Load config` accepts one `.json` file (upload cap 5 MB, `.streamlit/config.toml`). Accepted shapes (`app.py:394-419`): the envelope above, or a bare flat dict of assumption fields (legacy; loads with a warning). A missing `schema_version` warns; a version above 1 warns; a version below 1 loads silently (the comment at `app.py:83-86` says a lower version warns, but no such branch exists). The dict is validated by `bt.Assumptions.from_dict` (`app.py:420`), which silently drops unknown keys (`backtest.py:110-111`), then merged into the session dict with `.update()` (`app.py:421`), so unknown keys do persist in `session_state["assumptions"]` and in later downloads until reset. A `st.rerun()` follows so the widgets re-read the new values (`app.py:424`).

**Reset.** `Reset all to defaults` restores `assumptions` and `fetch_cfg` to their defaults, deletes the widget keys named above, and reruns (`app.py:427-434`).

---

## 5. Raw files produced for this map

| File | Content |
|---|---|
| `docs/guide/raw/appmap_apptest.py` | AppTest harness: five sidebar scenarios, docs tab, two demo runs with sweep, rolling and by-month |
| `docs/guide/raw/appmap_apptest_stdout.txt`, `appmap_apptest_stderr.txt` | full harness output and the app log (`sweep … total_cells=30`) |
| `docs/guide/raw/appmap_widgets_swap_level_demo.txt`, `…_swap_level_real.txt`, `…_hourly_live.txt`, `…_hourly_live_abs_periodic_stress.txt`, `…_hourly_local.txt` | sidebar widget dumps with label, key, type, value, limits, options, help |
| `docs/guide/raw/appmap_widgets_main_before_run.txt`, `appmap_main_after_run.txt` | main-area dumps |
| `docs/guide/raw/appmap_doc_tabs_extract.py`, `appmap_doc_tabs.txt` | Documentation tab and five sub-tabs, verbatim |
| `docs/guide/raw/appmap_help_info.txt` | the `st.help(bt.Assumptions)` block |
| `docs/guide/raw/appmap_config_default.json` | the default `Save / load config` document |
| `docs/guide/raw/appmap_reposition_check.py`, `.txt` | numeric check for C20 |
| `docs/guide/raw/appmap_hourly_local_check.py`, `.txt` | numeric checks for C11, C12, C14 |

Sidebar widget count: **59 declared call sites in `app.py` (56 distinct labels; 33 to 38 rendered at once)**. Contradictions found: **23**.
