#!/usr/bin/env python3
"""Numeric checks for docs/guide/SOURCES.md. Run from the repo root:
    python3 docs/guide/raw/sources_check.py > docs/guide/raw/sources_check.txt
Every number quoted in SOURCES.md comes from this output."""
import math, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/user/backtest-lp")
import run_claude as rc

np.set_printoptions(linewidth=140)
print("== 1. TickMath: floor(log(price)/log(1.0001)) == tick on the real pool file ==")
pq = "/home/user/backtest-lp/data/pool_hourly_0x88e6a0_2026-08-01_2026-08-31.parquet"
df = pd.read_parquet(pq)
print("rows:", len(df), "columns:", list(df.columns))
sub = df.iloc[[0, 1, 100, 360, 500, 720]]
ok_all = True
for ts, row in sub.iterrows():
    sq = row["sqrtPrice"] / 2**96
    price = sq * sq
    t_code = rc.tick_from_raw(price)
    t_wp = math.floor(math.log(price) / math.log(1.0001))
    eth = rc.eth_price_from_sqrtx96(row["sqrtPrice"])
    ok = (t_code == int(row["tick"])) and (t_wp == int(row["tick"]))
    ok_all &= ok
    print(f"{ts}  sqrtPrice={row['sqrtPrice']:.6e}  raw_price={price:.6e}  eth_price={eth:.2f}  "
          f"tick_file={int(row['tick'])}  tick_code={t_code}  tick_formula={t_wp}  match={ok}")
full = np.floor(np.log((df["sqrtPrice"] / 2**96) ** 2) / np.log(1.0001)).astype(int)
mism = int((full != df["tick"]).sum())
print(f"all rows: mismatches={mism} of {len(df)} (sample match={ok_all})")
print("also: 1.0001**tick vs (sqrtPrice/2^96)^2 ratio at row0 =", (1.0001 ** int(df['tick'].iloc[0])) / ((df['sqrtPrice'].iloc[0] / 2**96) ** 2))

print()
print("== 2. Amount formulas: code vs SqrtPriceMath.sol vs Elsts calculate_x/calculate_y ==")
cfg = rc.Config(capital_usd=1_000_000, range_width=0.15)
P0 = 3000.0
pos = rc.build_position(P0, cfg, pd.Timestamp("2026-08-01", tz="UTC"))
print(f"build_position(P={P0}, +-15%): tick_lower={pos.tick_lower} tick_upper={pos.tick_upper} "
      f"L={pos.liquidity:.6e} eth_lower={pos.eth_price_lower:.2f} eth_upper={pos.eth_price_upper:.2f}")
print("tick_lower % 10 =", pos.tick_lower % 10, " tick_upper % 10 =", pos.tick_upper % 10)
print(f"value_usd(P0) = {pos.value_usd(P0):.6f}  (target capital 1000000)")
def elsts_x(L, sp, sa, sb):
    sp = max(min(sp, sb), sa); return L * (sb - sp) / (sp * sb)
def elsts_y(L, sp, sa, sb):
    sp = max(min(sp, sb), sa); return L * (sp - sa)
def elsts_L(x, y, sp, sa, sb):
    if sp <= sa: return x * sa * sb / (sb - sa)
    if sp < sb:  return min(x * sp * sb / (sb - sp), y / (sp - sa))
    return y / (sb - sa)
sa, sb, L = pos.sqrt_a, pos.sqrt_b, pos.liquidity
for P in (2400.0, 2600.0, 3000.0, 3400.0, 3600.0):
    sp = float(rc.sqrt_from_eth_price(P))
    a0, a1 = pos.amounts_raw(sp)
    x, y = elsts_x(L, sp, sa, sb), elsts_y(L, sp, sa, sb)
    sol0 = L * (sb - max(min(sp, sb), sa)) / (max(min(sp, sb), sa) * sb)   # SqrtPriceMath getAmount0Delta
    sol1 = L * (max(min(sp, sb), sa) - sa)                                    # SqrtPriceMath getAmount1Delta
    Lb = elsts_L(a0, a1, sp, sa, sb)
    print(f"P={P:7.1f} in_range={sa < sp < sb}  amt0(code)={a0:.6e} x(Elsts)={x:.6e} amt0(Sol)={sol0:.6e} "
          f"relerr={abs(a0-x)/max(abs(x),1e-30):.1e}  amt1(code)={a1:.6e} y(Elsts)={y:.6e} amt1(Sol)={sol1:.6e} "
          f"relerr={abs(a1-y)/max(abs(y),1e-30):.1e}  L_back(Elsts get_liquidity)={Lb:.6e} relerr={abs(Lb-L)/L:.1e}")

print()
print("== 3. LVR closed form: |V''(P)| analytic vs np.gradient used in _closed_form_lvr ==")
# analytic: delta(P) = L*(sqrt(SCALE/P) - sa)/1e18 inside range -> dDelta/dP = -L*sqrt(SCALE)/(2e18) * P^-1.5
Lh = L * math.sqrt(rc.SCALE) / 10**rc.DEC1      # liquidity in human units (ETH*sqrt(USD))
print(f"L_raw={L:.6e}  L_human = L_raw*sqrt(1e12)/1e18 = {Lh:.6f}")
rng = np.random.default_rng(0)
n = 20_000
dt = 60 / rc.SECONDS_PER_YEAR
sig = 0.65
r = rng.normal(-0.5 * sig**2 * dt, sig * math.sqrt(dt), n)
p = P0 * np.exp(np.cumsum(r))
delta = pos.delta_eth(p)
gam_fd = np.gradient(delta, p)
inr = (p > pos.eth_price_lower) & (p < pos.eth_price_upper)
gam_an = np.where(inr, Lh / (2 * p**1.5), 0.0)
print(f"path: n={n} steps of 60s, min P={p.min():.1f} max P={p.max():.1f}, steps in range={int(inr.sum())}")
fin = np.isfinite(gam_fd)
print(f"np.gradient finite values: {int(fin.sum())} of {n}; non-finite: {int((~fin).sum())}")
mask = fin & inr
print(f"in-range median |gamma_fd|/gamma_analytic = {np.median(np.abs(gam_fd[mask]) / gam_an[mask]):.4f}")
print(f"in-range 5th/95th pct of that ratio = {np.percentile(np.abs(gam_fd[mask]) / gam_an[mask], [5, 95])}")
var_step = float(np.var(np.diff(np.log(p))))
lvr_fd = float(np.nansum((0.5 * np.abs(gam_fd) * p**2 * var_step)[1:]))
lvr_an = float(np.sum((0.5 * gam_an * p**2 * var_step)[1:]))
print(f"var per 60s step = {var_step:.3e}  -> annualised sigma = sqrt(var*525600) = {math.sqrt(var_step * 525600):.4f} (input 0.65)")
print(f"closed-form LVR, finite-difference gamma (code) = {lvr_fd:.2f} USD")
print(f"closed-form LVR, analytic gamma L/(2P^1.5)      = {lvr_an:.2f} USD   ratio code/analytic = {lvr_fd/lvr_an:.4f}")
# what the function itself returns on the same path
dfx = pd.DataFrame({"eth_price": p, "lp_delta": delta})
print(f"_closed_form_lvr() on the same frame = {rc._closed_form_lvr(dfx, None, cfg):.2f} USD")
# repeated prices (a klines tape or a quiet grid) break np.gradient
p2 = p.copy(); p2[1000:1003] = p2[999]
g2 = np.gradient(pos.delta_eth(p2), p2)
print(f"with 3 repeated consecutive prices: non-finite gradient values = {int((~np.isfinite(g2)).sum())} "
      f"(nan={int(np.isnan(g2).sum())}, inf={int(np.isinf(g2).sum())}); "
      f"nansum result finite? {np.isfinite(np.nansum((0.5*np.abs(g2)*p2**2*var_step)[1:]))}")
p3 = p.copy(); p3[1000] = p3[999]
g3 = np.gradient(pos.delta_eth(p3), p3)
print(f"with 2 repeated consecutive prices: non-finite = {int((~np.isfinite(g3)).sum())} "
      f"(nan={int(np.isnan(g3).sum())}, inf={int(np.isinf(g3).sum())}); "
      f"nansum result finite? {np.isfinite(np.nansum((0.5*np.abs(g3)*p3**2*var_step)[1:]))}")

print()
print("== 4. Path LVR sign and fee-net execution (attribute_swaps arithmetic on one synthetic swap) ==")
fee = cfg.fee_tier
# taker buys 1 ETH from the pool, pays 3001 USDC gross, CEX mark 3002
a0, a1, p_cex = 3001e6, -1e18, 3002.0
a0n = a0 * (1 - fee); a1n = a1
p_exec = (a0n / 1e6) / (abs(a1n) / 1e18)
taker_pnl = (1.0 if a1n < 0 else -1.0) * abs(a1n) / 1e18 * (p_cex - p_exec)
print(f"taker buys 1 ETH for 3001 USDC gross; fee={fee}; CEX={p_cex}")
print(f"  fee_usd (gross USDC in * fee) = {a0*fee/1e6:.4f}")
print(f"  fee-net USDC reaching reserves = {a0n/1e6:.4f}; p_exec = {p_exec:.4f}; lvr_usd (share=1) = {taker_pnl:.4f}")
print(f"  gross markout = 1*(3002-3001) = {1*(3002-3001):.4f}; fee - lvr = {a0*fee/1e6 - taker_pnl:.4f}")

print()
print("== 5. Binance USD-M funding file ==")
fcsv = "/home/user/backtest-lp/data/ETHUSDT-fundingRate-2026-08.csv"
fd = pd.read_csv(fcsv)
fd["t"] = pd.to_datetime(fd["calc_time"], unit="ms", utc=True)
print("columns:", list(fd.columns[:3]))
print("rows:", len(fd), " first:", fd["t"].iloc[0], " last:", fd["t"].iloc[-1])
print("distinct funding_interval_hours:", sorted(fd["funding_interval_hours"].unique().tolist()))
tod = fd["t"].dt.floor("s").dt.strftime("%H:%M:%S")
print("distinct times of day (floored to the second) and counts:")
print(tod.value_counts().sort_index().to_string())
print("max |calc_time - nearest 8h boundary| in ms:", int((fd["calc_time"] % (8 * 3600 * 1000)).apply(lambda m: min(m, 8*3600*1000 - m)).max()))
aug = fd[(fd["t"] >= "2026-08-01") & (fd["t"] < "2026-09-01")]
print("rows dated in August 2026:", len(aug), "(31 days * 3 = 93)")
mean_rate = float(aug["last_funding_rate"].mean())
print(f"mean last_funding_rate (Aug 2026) = {mean_rate:.8f}")
print(f"annualised mean = rate * 3 * 365 = {mean_rate * 3 * 365:.6f} = {mean_rate*3*365*100:.4f}%")
print(f"min={aug['last_funding_rate'].min():.8f} max={aug['last_funding_rate'].max():.8f} "
      f"n_negative={int((aug['last_funding_rate']<0).sum())}")
print("load_funding() index head:", rc.load_funding(fcsv).index[:3].tolist())

print()
print("== 6. Funding sign as applied in simulate_hedge: cash = -(hedge*p) * rate ==")
for hedge, rate in ((-100.0, 0.0001), (-100.0, -0.0001), (100.0, 0.0001)):
    cash = -(hedge * 3000.0) * rate
    print(f"hedge={hedge:+.0f} ETH ({'short' if hedge<0 else 'long'}), p=3000, rate={rate:+.4f}: funding_usd={cash:+.2f}")
