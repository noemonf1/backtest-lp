# SOURCES

Formula verification for the delta-hedged Uniswap v3 LP backtester in `run_claude.py` against its published sources.
Written 2026-09-06 (UTC). All line numbers refer to `/home/user/backtest-lp/run_claude.py` at the HEAD recorded in `ENVIRONMENT.md` unless another file is named.

Every number in this document was printed by `docs/guide/raw/sources_check.py`; the output is `docs/guide/raw/sources_check.txt`. The fetch attempts are logged in `docs/guide/raw/sources_fetch_log.txt`.

## How to read the marks

Each check carries exactly one mark:

- **matches**: the code implements the source equation, verified by reading both and, where possible, by computation.
- **differs**: the code and the source disagree. The text says which is wrong.
- **not checked**: the source could not be read, or the code does not use the concept.

## What could and could not be fetched

The egress proxy rejected every external HTTPS host except GitHub. Each URL was requested once; every one returned status 000 (TLS CONNECT refused). See `raw/sources_fetch_log.txt`.

| Source | Fetch result | Fallback used |
|---|---|---|
| Uniswap v3 whitepaper (`uniswap.org/whitepaper-v3.pdf`) | rejected | `git clone` of `Uniswap/v3-core`, commit `d0831dc` (2026-04-30): `TickMath.sol`, `SqrtPriceMath.sol`, `UniswapV3Factory.sol`, `UniswapV3Pool.sol` |
| Milionis, Moallemi, Roughgarden, Zhang, arXiv:2208.06046 | rejected (abs, pdf, export mirror) | none. The equations compared below are the ones quoted in the review brief. |
| Li, Papanicolaou, Schönleber, SSRN 4811111 | rejected | none |
| Elsts, "Liquidity Math in Uniswap v3" (PDF) | rejected | `git clone` of `atiselsts/uniswap-v3-liquidity-math`, commit `a1e991d` (2023-07-11): `uniswap-v3-liquidity-math.py` |
| Binance USD-M funding docs and FAQ | rejected | the real funding file `data/ETHUSDT-fundingRate-2026-08.csv` |

## Frame conventions the reader needs

The pool is ETH/USDC 0.05%. In the pool, token0 = USDC (6 decimals) and token1 = WETH (18 decimals). The pool's own price is `P_raw = token1/token0`, which is WETH per USDC, a number near 5e8 in raw units. The human ETH price is `P = 1e12 / P_raw` (`raw_from_eth_price`, lines 129-136). So a high ETH price is a low pool price and a low tick. The code stores every position in the pool frame (`tick_lower` is the upper ETH bound, line 164-165) and converts through one helper. Every source below is written in the pool frame. All comparisons are made in the pool frame, then restated in ETH-price terms where the reader needs it.

The code's `L` is the raw on-chain liquidity. The convenient human liquidity is `L_h = L * sqrt(1e12) / 1e18`; for the test position below `L = 1.247683e17` and `L_h = 124768.27` (`raw/sources_check.txt`, section 3).

---

## 1. Milionis, Moallemi, Roughgarden, Zhang. "Automated Market Making and Loss-Versus-Rebalancing." arXiv:2208.06046

Source text: not fetched. The equations below are as quoted in the review brief, and the same equation appears in the code's own docstring at line 788. The marks compare the code to those equations.

### 1a. Instantaneous LVR

Source equation (brief): `l(sigma, P) = (sigma^2 * P^2 / 2) * |V''(P)|`, where `V(P)` is the pool value in numeraire as a function of price and `sigma` is the instantaneous log volatility. For a constant-product pool `V = 2 L sqrt(P)`, so `|V''| = L / (2 P^{3/2})` and `l = sigma^2 * L * sqrt(P) / 4`.

Implemented (`_closed_form_lvr`, lines 785-800):

```
r = diff(log(p))                       # line 792, p = hedge-grid ETH price
var_per_step = var(r)                  # line 795
gamma = np.gradient(lp_delta, p)       # line 798, finite-difference dDelta/dP
inst = 0.5 * |gamma| * p^2 * var_per_step   # line 799
return nansum(inst[1:])                # line 800
```

Since `lp_delta = dV/dP`, `gamma = V''(P)`, and `inst` is `(1/2) * sigma^2 dt * P^2 * |V''|` per grid step. Summing over steps is the time integral of `l`. **matches**.

Note on units: the docstring at line 786 writes the closed form as `sigma^2 * L * sqrt(P_raw) / 4`. That is the paper's constant-product form denominated in token1 (WETH). The function body computes `(1/2) sigma^2 P^2 |V''|` in USD. The two agree after the decimal conversion `L_h = L * sqrt(1e12) / 1e18` and multiplying by the ETH price. The computed check in 1c confirms the USD number.

### 1b. The sigma the code uses

`sigma^2 dt` is `var_per_step`, the sample variance of one-step log returns on the hedge grid (line 792-795). The grid is `cex.resample("60s").last()` with `grid_seconds = 60` by default (line 114, line 596). The `cex` series is Binance USD-M ETHUSDT aggTrades resampled to 1 s (line 315). So sigma is a realised 60-second volatility from the perp tape, held constant over the whole backtest window, and not annualised. No annualisation is needed because the per-step variance is summed step by step.

Check on a synthetic 60-second GBM path with input annual volatility 0.65 and 20,000 steps: `var_per_step = 7.975e-07`, which annualises to `sqrt(7.975e-07 * 525600) = 0.6474` (`raw/sources_check.txt`, section 3). **matches**.

One simplification: the paper's integral uses the instantaneous `sigma_t^2`. The code uses one window-average variance times a path-dependent gamma. The two coincide unless volatility and the position's gamma are correlated inside the window. This is a modelling choice, not an error against the formula, and the function is labelled "sanity check only" (line 786).

### 1c. Concentrated-range V''(P)

For a v3 range order the brief states `|V''(P)| = L / (2 P^{3/2})` inside the range and 0 outside.

Implemented: the delta is `lp_delta = L * (clip(sqrt(P_raw), sa, sb) - sa) / 1e18` (line 607). In the ETH frame `sqrt(P_raw) = sqrt(1e12 / P)`, so inside the range `d(delta)/dP = -L_h / (2 P^{3/2})` and outside the range the clip makes delta constant, so the derivative is 0. The code does not write this analytically; it takes `np.gradient` of the delta series (line 798).

Computed check (`raw/sources_check.txt`, section 3), test position built by `build_position(3000, +-15%)`: on 20,000 grid steps with 19,998 in range, the finite-difference gamma over the analytic `L_h / (2 P^{3/2})` has median 1.0000 with 5th/95th percentiles `[0.99999919, 1.00000079]`. The closed-form LVR is 27,603.10 USD with the code's gamma and 27,601.79 USD with the analytic gamma; ratio 1.0000. `_closed_form_lvr()` itself returns 27,603.10 USD on the same frame. **matches**.

Robustness note, not a source disagreement: `np.gradient(f, x)` divides by consecutive price differences. When two or more consecutive grid prices are identical (a quiet minute, or a klines tape), the affected points become `nan`: 2 nan for one repeated price, 4 nan for two (`raw/sources_check.txt`, section 3). `nansum` on line 800 then drops those steps silently. The total stays finite but slightly under-counts. This only affects the sanity-check number, not the path LVR.

### 1d. Path-realised LVR and the fee-net execution price

Source definition (brief): LVR is the value of the rebalancing trade at the CEX price minus what the AMM received for it. In the paper's accounting, LP profit equals fees minus LVR, with LVR measured on the pool's fee-less execution.

Implemented (`attribute_swaps`, lines 515-525):

```
a0_net = a0 * (1 - fee) if a0 > 0 else a0     # line 517, fee stripped from the input leg only
a1_net = a1 * (1 - fee) if a1 > 0 else a1     # line 518
eth_qty = |a1_net| / 1e18;  usd_qty = |a0_net| / 1e6      # lines 519-520
p_exec = usd_qty / eth_qty                    # line 522, the pool's fee-net execution price
taker_pnl = (+1 if ETH left the pool else -1) * eth_qty * (p_cex - p_exec)   # line 524
lvr_usd = taker_pnl * share                   # line 525
```

`p_cex` is the last Binance quote at or before the swap's block time plus `markout_seconds` (default 0; lines 491-495). `share = L_you / (L_active + L_you)` while the swap tick is inside the position (lines 502-506), which is the linear-in-L scaling the paper implies.

Fees are booked separately on the gross input token (lines 511-513). Because the fee is stripped from the input leg before `p_exec` is formed, the LVR is measured on what actually entered the reserves, and `fee_usd - lvr_usd` equals the LP's gross markout `markout_usd` (line 533). Worked example from `raw/sources_check.txt`, section 4: a taker buys 1 ETH for 3001 USDC gross while the CEX prints 3002. `fee_usd = 1.5005`, fee-net USDC into reserves 2999.4995, `p_exec = 2999.4995`, `lvr_usd = 2.5005`, and `fee_usd - lvr_usd = -1.0000`, which is the LP's true loss of 1 USD versus the CEX. Fee-inclusive LVR would double count the fee against `fee_usd`. **matches**.

---

## 2. Li, Papanicolaou, Schönleber. "The Implied Impermanent Loss in Decentralized Liquidity Provision." SSRN 4811111

Earlier circulated as "Implied Impermanent Loss: A Cross-Sectional Analysis of Decentralized Liquidity Pools". Siddharth Naik is a co-author of the sibling paper "Yield Farming for Liquidity Provision" (SSRN 4422213), not of 4811111.

Search performed: `grep -n "implied|Implied|impermanent|Impermanent|\bIL\b|divergence"` over `run_claude.py`, `run_kimi.py`, `kimi/*.py`, `app.py`, `backtest.py`. Zero hits. A case-insensitive search for the author names, "ssrn" and the abstract id also returned zero hits in every code and guide file.

Conclusion: no code in the repository implements any concept from this paper (no implied-IL surface, no IL-from-option-prices, no cross-sectional IL measure). The backtester measures LVR, not impermanent loss. The paper is cited here only for the distinction between impermanent loss (the unhedged holder's divergence loss, which depends on the price at exit) and LVR (the running cost of the delta-hedged position, which accrues along the path and is what a hedged LP pays). Equations: **not checked**.

---

## 3. Uniswap v3 whitepaper and `TickMath.sol` / `SqrtPriceMath.sol`

Whitepaper PDF: **not checked** (fetch rejected). The on-chain library is the binding source and was read from `Uniswap/v3-core` commit `d0831dc`.

### 3a. Tick to sqrt price

Source (`contracts/libraries/TickMath.sol:18`, `:23`): `getSqrtRatioAtTick(tick)` "Calculates sqrt(1.0001^tick) * 2^96". Inverse (`TickMath.sol:56`, `:61`): `getTickAtSqrtRatio` returns "the greatest tick value such that getRatioAtTick(tick) <= ratio", which is `floor(log_1.0001(price))`.

Implemented:

```
raw_from_tick(tick) = 1.0001 ** tick                                  # line 151-152
tick_from_raw(p_raw) = floor(log(p_raw) / log(1.0001))                # line 147-148
sqrt_a = sqrt(1.0001 ** tick_lower);  sqrt_b = sqrt(1.0001 ** tick_upper)   # lines 171-176
eth_price_from_sqrtx96(s) = 1e12 / (s / 2^96)^2                       # lines 142-144
```

Computed check on the real pool file `data/pool_hourly_0x88e6a0_2026-08-01_2026-08-31.parquet` (721 hourly rows, columns `sqrtPrice`, `tick`). For each row, `price = (sqrtPrice / 2^96)^2` and `floor(log(price) / log(1.0001))` was compared to the file's `tick` (`raw/sources_check.txt`, section 1):

| ts (UTC) | sqrtPrice | raw price | ETH price | tick in file | tick from formula | tick from `tick_from_raw` |
|---|---|---|---|---|---|---|
| 2026-08-01 00:00 | 1.835438e33 | 5.366862e8 | 1863.29 | 201019 | 201019 | 201019 |
| 2026-08-01 01:00 | 1.834888e33 | 5.363644e8 | 1864.40 | 201013 | 201013 | 201013 |
| 2026-08-05 04:00 | 1.834277e33 | 5.360070e8 | 1865.65 | 201006 | 201006 | 201006 |
| 2026-08-16 00:00 | 1.826856e33 | 5.316787e8 | 1880.84 | 200925 | 200925 | 200925 |
| 2026-08-21 20:00 | 1.604016e33 | 4.098814e8 | 2439.73 | 198323 | 198323 | 198323 |
| 2026-08-31 00:00 | 1.607956e33 | 4.118976e8 | 2427.79 | 198372 | 198372 | 198372 |

Over all 721 rows: 0 mismatches. At row 0, `1.0001^tick / price = 0.99997088`, which is below 1 and above `1/1.0001`, as the floor requires. **matches**.

### 3b. Tick spacing for the 0.05% pool

Source (`contracts/UniswapV3Factory.sol:26`): `feeAmountTickSpacing[500] = 10`.

Implemented: `TICK_SPACING = 10` (line 84), used by `align_tick` (lines 155-156) in `build_position` (lines 223-228). For the test position both bounds satisfy `tick % 10 == 0` (`raw/sources_check.txt`, section 2). **matches**.

### 3c. In-range condition

Source (`contracts/UniswapV3Pool.sol:328`, `:336`): a position's liquidity is active when `tickLower <= tick < tickUpper`.

Implemented: `in_range = (tick >= tick_lower) & (tick < tick_upper)` (lines 207-208 and 502-504). **matches**.

### 3d. Token amounts from liquidity and two sqrt prices

Source (`contracts/libraries/SqrtPriceMath.sol:146-147`, function at `:153`): `getAmount0Delta = liquidity * (sqrt(upper) - sqrt(lower)) / (sqrt(upper) * sqrt(lower))`. Source (`SqrtPriceMath.sol:176`, function at `:182`): `getAmount1Delta = liquidity * (sqrt(upper) - sqrt(lower))`.

Implemented (`Position.amounts_raw`, lines 192-195, and `_lp_value`, lines 662-666):

```
s = clip(sqrt_p, sqrt_a, sqrt_b)
amt0 = L * (1/s - 1/sqrt_b)        # = L * (sqrt_b - s) / (s * sqrt_b)
amt1 = L * (s - sqrt_a)
value_usd = amt0 / 1e6 + amt1 / 1e18 * P      # lines 202-204
```

With `lower = s` and `upper = sqrt_b` for token0, and `lower = sqrt_a`, `upper = s` for token1, these are the two Solidity formulas evaluated in floating point without the rounding directions the contract applies. Computed comparison at ETH prices 2400, 2600, 3000, 3400, 3600 for the test position: the relative error between the code's `amt0` and the Solidity expression is at most 4.4e-15 and for `amt1` it is 0 (`raw/sources_check.txt`, section 2). Out of range on the low side (P = 2400) the code holds only WETH; out of range on the high side (P = 3600) it holds only USDC, as the clip requires. **matches**.

The delta the hedge uses is `lp_delta = L * (s - sqrt_a) / 1e18` (line 607), which is `amt1` in ETH units. Since `value = amt0/1e6 + (amt1/1e18) * P` and the token0 leg is the position's USD, `dV/dP = amt1/1e18` is the position's delta. This is the same statement as `delta_eth` (lines 197-200). **matches**.

---

## 4. Elsts. "Liquidity Math in Uniswap v3" (technical note) and `atiselsts/uniswap-v3-liquidity-math`

PDF: not fetched. The companion Python file at commit `a1e991d` was read; it states it follows the technical note.

### 4a. Amounts from L and range

Source (`uniswap-v3-liquidity-math.py:34-40`):

```
calculate_x(L, sp, sa, sb): sp = clip(sp, sa, sb); return L * (sb - sp) / (sp * sb)
calculate_y(L, sp, sa, sb): sp = clip(sp, sa, sb); return L * (sp - sa)
```

Implemented: `amt0 = L * (1/s - 1/sqrt_b)`, `amt1 = L * (s - sqrt_a)` with the same clip (lines 192-195). Elsts writes his examples with `x` = ETH as token0 and `y` = USDC as token1 and `P` in USDC per ETH. In this pool token0 is USDC and token1 is WETH, and the pool price is WETH per USDC. The code's `amt0` is therefore Elsts' `x` (the token0 leg) and `amt1` is Elsts' `y`, with `sp = sqrt(1e12 / P_eth)`. The algebra is identical; only the labelling of which leg is the "risky" one is swapped, and the code handles that by converting through `raw_from_eth_price`. Computed: relative error between the code and `calculate_x` / `calculate_y` at five prices is at most 4.4e-15 (`raw/sources_check.txt`, section 2). **matches**.

### 4b. L from a USD amount and a range

Source (`uniswap-v3-liquidity-math.py:13-28`): Elsts sizes from token amounts. `get_liquidity_0(x, sa, sb) = x * sa * sb / (sb - sa)`, `get_liquidity_1(y, sa, sb) = y / (sb - sa)`, and inside the range `get_liquidity = min(get_liquidity_0(x, sp, sb), get_liquidity_1(y, sa, sp))`.

Implemented (`build_position`, lines 232-234): the code sizes from a USD value instead. It builds a probe position with `L = 1`, values it at the entry price, and sets `L = capital / value_of_one_unit_of_L`:

```
L = C / [ (1/sp - 1/sb) / 1e6  +  (sp - sa) * P / 1e18 ]
```

This is the value function of 4a solved for `L` at a target value `C`. It produces a position whose two legs are both fully used, which is the case where Elsts' `min` returns two equal candidates. Computed: for `C = 1,000,000` at `P = 3000`, `value_usd(P) = 1000000.000000`, and feeding the resulting `amt0`, `amt1` back into Elsts' `get_liquidity` returns the same `L` with relative error at most 1.3e-16 at every tested price, in and out of range (`raw/sources_check.txt`, section 2). **matches**.

The tick alignment widens the requested +-15% band outward to the 10-tick grid: the requested bounds 2550 and 3450 become 2547.86 and 3452.99 (`raw/sources_check.txt`, section 2). This is the correct direction; rounding inward would leave the entry price nearer the edge than requested.

---

## 5. Binance USD-M perpetual funding

Documentation pages: **not checked** (both fetches rejected). Interval and timing were established from the real file `data/ETHUSDT-fundingRate-2026-08.csv`. The sign convention is Binance's published one: a positive rate means longs pay shorts; a negative rate means shorts pay longs. Payment per settlement is `notional * rate`.

### 5a. Interval and timing, from the file

`raw/sources_check.txt`, section 5:

| Item | Value |
|---|---|
| columns | `calc_time, funding_interval_hours, last_funding_rate` |
| rows dated in August 2026 | 93 (31 days x 3) |
| distinct `funding_interval_hours` | `[8]` |
| times of day (floored to the second) | 00:00:00 x31, 08:00:00 x31, 16:00:00 x31 |
| largest offset of `calc_time` from an exact 8h boundary | 26 ms |
| mean `last_funding_rate` | 0.00005191 per 8h |
| annualised mean, `rate * 3 * 365` | 0.056846, i.e. 5.6846% |
| min / max / count negative | -0.00000869 / 0.00010000 / 5 |

So the file confirms an 8-hour interval settled at 00:00, 08:00 and 16:00 UTC. The rate is stated per 8-hour period, so the annualisation factor is 3 x 365 = 1095. The ceiling of 0.00010000 (0.01% per 8h) is hit in the sample.

`load_funding` (lines 318-324) reads `calc_time` and `last_funding_rate` (lines 321-322) and parses the epoch as milliseconds by magnitude (`_epoch_to_datetime`, lines 284-292); the first three index stamps print as `2026-08-01 00:00:00.001`, `08:00:00`, `16:00:00` UTC. **matches**.

### 5b. How the code applies funding to the hedge

The hedge is a short perp when the LP is long ETH: `h = -lp_delta[0]` (line 614), so `hedge` is negative in ETH. Implemented (lines 644-657):

```
k = index of the grid step at or before each funding stamp     # lines 650-654
cash = -(hedge[k] * p[k]) * rate                                # line 655
funding_usd[k] += cash
```

Source convention: `payment_to_short = +notional * rate`. With `hedge < 0`, `-(hedge * p)` is the positive short notional, so `cash = +notional * rate`. Computed (`raw/sources_check.txt`, section 6): short 100 ETH at 3000 with rate +0.0001 gives `+30.00` USD; with rate -0.0001 gives `-30.00`; a long 100 ETH with rate +0.0001 gives `-30.00`. A positive rate is credited to the short. That is correct: the delta-hedged LP collects funding in the sample, and the August mean of +0.00005191 per 8h is a receipt for the short. The comment at line 645 states the same. `funding_usd` enters `net_usd` with a plus sign (line 704). **matches**.

Timing detail: the funding stamp `00:00:00.001` maps to the `00:00:00` grid step because `searchsorted(..., side="right") - 1` picks the last step at or before the stamp (line 651-653). The notional used is the hedge in force at that step. This is the settlement rule Binance applies: the position held at the funding timestamp pays or receives.

---

## Summary of marks

| # | Check | Mark |
|---|---|---|
| 1a | MMRZ instantaneous LVR `(sigma^2 P^2 / 2) * abs(V''(P))` vs `_closed_form_lvr` (lines 785-800) | **matches** (against the equation as quoted; paper text not fetched) |
| 1b | Sigma used: realised 60 s log-return variance on the perp hedge grid, constant over the window, summed per step (lines 792-795) | **matches** |
| 1c | Concentrated `abs(V''(P)) = L_h / (2 P^{3/2})` inside the range, 0 outside; finite-difference gamma equals analytic to 1e-6 (line 798) | **matches** |
| 1d | Path LVR on the fee-net execution price versus the CEX mark; fee booked separately; `fee - lvr = markout` (lines 511-533) | **matches** |
| 2 | SSRN 4811111, Li, Papanicolaou, Schönleber: no concept used in any code file; cited for IL vs LVR distinction only | **not checked** |
| 3 (whitepaper) | Uniswap v3 whitepaper PDF | **not checked** |
| 3a | `TickMath.sol` tick <-> sqrt price, 721/721 rows of the real pool file agree (lines 147-152) | **matches** |
| 3b | Tick spacing 10 for fee 500 (`UniswapV3Factory.sol:26`; line 84) | **matches** |
| 3c | In-range `tickLower <= tick < tickUpper` (`UniswapV3Pool.sol:328,336`; lines 207-208, 502-504) | **matches** |
| 3d | `SqrtPriceMath.sol` amount0 / amount1 deltas vs `amounts_raw`, `_lp_value`, `delta_eth` (lines 192-204, 607, 662-666) | **matches** |
| 4a | Elsts `calculate_x` / `calculate_y` vs `amounts_raw` (lines 192-195) | **matches** |
| 4b | Elsts `get_liquidity` vs `build_position` sizing `L = C / value(L=1)` (lines 232-234) | **matches** |
| 5 (docs) | Binance funding documentation pages | **not checked** |
| 5a | 8 h interval at 00:00 / 08:00 / 16:00 UTC, 93 rows in August 2026, mean 0.00005191, annualised 5.6846%; `load_funding` (lines 318-324) | **matches** |
| 5b | Positive rate credited to the short hedge, `cash = -(hedge * p) * rate` (line 655) | **matches** |

No check returned **differs**. Two implementation notes that are not source disagreements: the closed-form sanity check drops grid steps where consecutive prices repeat (1c), and it uses a window-average variance rather than an instantaneous one (1b).
