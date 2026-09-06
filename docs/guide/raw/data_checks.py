#!/usr/bin/env python3
"""Data-quality checks and tape statistics for the August 2026 guide.

Writes docs/guide/raw/data_checks.txt (this printout), data_checks.json
(machine-readable results) and PNG charts in docs/guide/raw/charts/.

Price convention (token0 = USDC 6 dec, token1 = WETH 18 dec):
    P_raw = (sqrtPriceX96 / 2**96)**2            (WETH-raw per USDC-raw)
    P_eth = 1e12 / P_raw                          (USDC per ETH)
i.e. run_claude.eth_price_from_sqrtx96.  Higher tick  ==>  LOWER ETH price.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import math
import os
import subprocess
import sys
from decimal import Decimal, getcontext

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from scipy import stats  # noqa: E402

sys.path.insert(0, "/home/user/backtest-lp")
import run_claude  # noqa: E402
import backtest as bt  # noqa: E402

ROOT = "/home/user/backtest-lp"
DATA = f"{ROOT}/data"
OUT = f"{ROOT}/docs/guide/raw"
CHARTS = f"{OUT}/charts"
os.makedirs(CHARTS, exist_ok=True)

F_SWAPS = f"{DATA}/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet"
F_HOURLY = f"{DATA}/pool_hourly_0x88e6a0_2026-08-01_2026-09-01.parquet"
F_HOURLY_OLD = f"{DATA}/pool_hourly_0x88e6a0_2026-08-01_2026-08-31.parquet"
F_K1M = f"{DATA}/ETHUSDT-klines-1m-2026-08.csv"
F_K1H = f"{DATA}/ETHUSDT-klines-1h-2026-08.csv"
F_FUND = f"{DATA}/ETHUSDT-fundingRate-2026-08.csv"
F_AGG = f"{DATA}/ETHUSDT-aggTrades-2026-08.csv"

FIGSIZE = (1400 / 150, 700 / 150)
DPI = 150

# --------------------------------------------------------------------------- #
# output plumbing
# --------------------------------------------------------------------------- #
_lines: list[str] = []


def P(*args):
    s = " ".join(str(a) for a in args)
    print(s)
    _lines.append(s)


def H(title):
    P("")
    P("=" * 96)
    P(title)
    P("=" * 96)


def fmt_ts(t):
    if t is None or (isinstance(t, float) and np.isnan(t)):
        return None
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M:%S%z")


def pct(a, q):
    return float(np.percentile(np.asarray(a, dtype=float), q))


def describe(a):
    a = np.asarray(a, dtype=float)
    a = a[~np.isnan(a)]
    return {
        "count": int(a.size),
        "mean": float(a.mean()),
        "median": float(np.median(a)),
        "std": float(a.std(ddof=1)) if a.size > 1 else None,
        "p1": pct(a, 1),
        "p5": pct(a, 5),
        "p95": pct(a, 95),
        "p99": pct(a, 99),
        "min": float(a.min()),
        "max": float(a.max()),
    }


def print_desc(d, unit=""):
    for k in ("count", "mean", "median", "std", "p1", "p5", "p95", "p99", "min", "max"):
        v = d.get(k)
        if v is None:
            continue
        if k == "count":
            P(f"  {k:>7}: {v}")
        else:
            P(f"  {k:>7}: {v:.6f}{unit}")


def sha256_shell(path):
    out = subprocess.run(["sha256sum", path], capture_output=True, text=True, check=True)
    return out.stdout.split()[0]


def save_fig(fig, name):
    path = f"{CHARTS}/{name}"
    fig.set_size_inches(*FIGSIZE)
    fig.savefig(path, dpi=DPI)
    plt.close(fig)
    P(f"  [chart saved] {path}")
    return path


def date_axis(ax):
    import matplotlib.dates as mdates
    ax.xaxis.set_major_locator(mdates.DayLocator(interval=3))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ax.tick_params(axis="x", labelrotation=0, labelsize=8)


def epoch_unit(v):
    m = float(v)
    for unit, hi in (("s", 1e11), ("ms", 1e14), ("us", 1e17), ("ns", 1e20)):
        if abs(m) < hi:
            return unit
    raise ValueError(m)


results: dict = {}

# --------------------------------------------------------------------------- #
# load
# --------------------------------------------------------------------------- #
swaps = pd.read_parquet(F_SWAPS)
hourly = pd.read_parquet(F_HOURLY)
hourly_old = pd.read_parquet(F_HOURLY_OLD)
k1m = pd.read_csv(F_K1M)
k1h = pd.read_csv(F_K1H)
fund = pd.read_csv(F_FUND)

k1m_unit = epoch_unit(k1m["open_time"].iloc[0])
k1h_unit = epoch_unit(k1h["open_time"].iloc[0])
fund_unit = epoch_unit(fund["calc_time"].iloc[0])
k1m["ts"] = pd.to_datetime(k1m["open_time"], unit=k1m_unit, utc=True)
k1h["ts"] = pd.to_datetime(k1h["open_time"], unit=k1h_unit, utc=True)
fund["ts"] = pd.to_datetime(fund["calc_time"], unit=fund_unit, utc=True)

swaps = swaps.sort_values(["block_number", "log_index"]).reset_index(drop=True)
swaps["block_time"] = pd.to_datetime(swaps["block_time"], utc=True)
swaps["price_eth"] = run_claude.eth_price_from_sqrtx96(swaps["sqrt_price_x96"].to_numpy())
swaps["usd"] = swaps["amount0"].abs() / 1e6

P("data_checks.py -- run at", pd.Timestamp.now('UTC').strftime("%Y-%m-%d %H:%M:%S UTC"))
P("pandas", pd.__version__, "numpy", np.__version__, "matplotlib", matplotlib.__version__,
  "scipy", __import__("scipy").__version__)
P(f"epoch units inferred: klines-1m open_time={k1m_unit}, klines-1h open_time={k1h_unit}, funding calc_time={fund_unit}")
P("Price formula used: P_eth = 1e12 / (sqrtPriceX96/2**96)**2  (run_claude.eth_price_from_sqrtx96)")
P(f"Sanity: first swap pool price = {swaps['price_eth'].iloc[0]:.4f} USDC/ETH; first Binance 1m close = {k1m['close'].iloc[0]}")

# --------------------------------------------------------------------------- #
# A. FILE MANIFEST
# --------------------------------------------------------------------------- #
H("A. FILE MANIFEST")
manifest = {}


def add_manifest(path, rows, first, last):
    st = os.stat(path)
    sha = sha256_shell(path)
    manifest[os.path.basename(path)] = {
        "path": path,
        "rows": int(rows),
        "first_timestamp": fmt_ts(first),
        "last_timestamp": fmt_ts(last),
        "size_bytes": int(st.st_size),
        "sha256": sha,
    }


add_manifest(F_SWAPS, len(swaps), swaps["block_time"].min(), swaps["block_time"].max())
add_manifest(F_HOURLY, len(hourly), hourly.index.min(), hourly.index.max())
add_manifest(F_HOURLY_OLD, len(hourly_old), hourly_old.index.min(), hourly_old.index.max())
add_manifest(F_K1M, len(k1m), k1m["ts"].min(), k1m["ts"].max())
add_manifest(F_K1H, len(k1h), k1h["ts"].min(), k1h["ts"].max())
add_manifest(F_FUND, len(fund), fund["ts"].min(), fund["ts"].max())

# aggTrades: header check + line count + first/last transact_time via shell; never loaded fully
with open(F_AGG, "r") as fh:
    agg_first_line = fh.readline().strip()
agg_has_header = agg_first_line.startswith("agg_trade_id")
wc = subprocess.run(["wc", "-l", F_AGG], capture_output=True, text=True, check=True)
agg_lines = int(wc.stdout.split()[0])
agg_rows = agg_lines - (1 if agg_has_header else 0)
head2 = subprocess.run(["head", "-n", "2", F_AGG], capture_output=True, text=True, check=True).stdout.splitlines()
tail1 = subprocess.run(["tail", "-n", "1", F_AGG], capture_output=True, text=True, check=True).stdout.strip()
agg_first_row = head2[1] if agg_has_header else head2[0]
agg_first_tt = int(agg_first_row.split(",")[5])
agg_last_tt = int(tail1.split(",")[5])
agg_unit = epoch_unit(agg_first_tt)
add_manifest(F_AGG, agg_rows,
             pd.to_datetime(agg_first_tt, unit=agg_unit, utc=True),
             pd.to_datetime(agg_last_tt, unit=agg_unit, utc=True))
manifest[os.path.basename(F_AGG)]["has_header"] = bool(agg_has_header)
manifest[os.path.basename(F_AGG)]["line_count_incl_header"] = agg_lines
manifest[os.path.basename(F_AGG)]["first_transact_time_raw"] = agg_first_tt
manifest[os.path.basename(F_AGG)]["last_transact_time_raw"] = agg_last_tt
manifest[os.path.basename(F_AGG)]["transact_time_unit"] = agg_unit

for name, m in manifest.items():
    P(f"{name}")
    P(f"    rows={m['rows']:,}  size={m['size_bytes']:,} B")
    P(f"    first={m['first_timestamp']}  last={m['last_timestamp']}")
    P(f"    sha256={m['sha256']}")
    if "has_header" in m:
        P(f"    header={m['has_header']}  lines_incl_header={m['line_count_incl_header']:,}  "
          f"transact_time unit={m['transact_time_unit']} raw first={m['first_transact_time_raw']} last={m['last_transact_time_raw']}")
results["A_file_manifest"] = manifest

# --------------------------------------------------------------------------- #
# B. SWAPS PER DAY / HOUR-OF-DAY
# --------------------------------------------------------------------------- #
H("B. SWAPS PER DAY (UTC) AND PER HOUR-OF-DAY")
swaps["day"] = swaps["block_time"].dt.floor("D")
per_day = swaps.groupby("day").size()
all_days = pd.date_range("2026-08-01", "2026-08-31", freq="D", tz="UTC")
per_day = per_day.reindex(all_days, fill_value=0)
P(f"{'day':<12}{'swaps':>8}")
for d, n in per_day.items():
    P(f"{d.strftime('%Y-%m-%d'):<12}{int(n):>8}")
P(f"{'TOTAL':<12}{int(per_day.sum()):>8}")
P(f"  days with swaps: {(per_day > 0).sum()} / 31;  min day {int(per_day.min())} ({per_day.idxmin().date()}), "
  f"max day {int(per_day.max())} ({per_day.idxmax().date()}), mean/day {per_day.mean():.2f}")
hod = swaps.groupby(swaps["block_time"].dt.hour).size().reindex(range(24), fill_value=0)
P("")
P(f"{'hour(UTC)':<10}{'swaps':>8}{'share':>9}")
for h_, n in hod.items():
    P(f"{h_:<10}{int(n):>8}{n / hod.sum() * 100:>8.2f}%")
P(f"  busiest hour-of-day: {int(hod.idxmax())}:00 UTC ({int(hod.max())} swaps); quietest: {int(hod.idxmin())}:00 UTC ({int(hod.min())})")
results["B_swaps_per_day"] = {
    "per_day": {d.strftime("%Y-%m-%d"): int(n) for d, n in per_day.items()},
    "total": int(per_day.sum()),
    "min_day": {"date": per_day.idxmin().strftime("%Y-%m-%d"), "swaps": int(per_day.min())},
    "max_day": {"date": per_day.idxmax().strftime("%Y-%m-%d"), "swaps": int(per_day.max())},
    "mean_per_day": float(per_day.mean()),
    "per_hour_of_day_utc": {int(h_): int(n) for h_, n in hod.items()},
    "busiest_hour_utc": int(hod.idxmax()),
    "quietest_hour_utc": int(hod.idxmin()),
}

# --------------------------------------------------------------------------- #
# C. USD VOLUME PER DAY
# --------------------------------------------------------------------------- #
H("C. USD VOLUME PER DAY: sum|amount0|/1e6 (swaps) vs poolHourDatas.volumeUSD")
vol_swaps_day = swaps.groupby("day")["usd"].sum().reindex(all_days, fill_value=0.0)
hourly_aug = hourly[(hourly.index >= "2026-08-01") & (hourly.index < "2026-09-01")]
vol_hourly_day = hourly_aug["volumeUSD"].groupby(hourly_aug.index.floor("D")).sum().reindex(all_days, fill_value=0.0)
P(f"(hourly file rows used for August: {len(hourly_aug)}; the 745th row {fmt_ts(hourly.index.max())} is excluded from the month totals)")
P(f"{'day':<12}{'swaps_usd':>18}{'hourly_volumeUSD':>18}{'ratio':>9}")
rows_c = {}
for d in all_days:
    a, b = float(vol_swaps_day[d]), float(vol_hourly_day[d])
    r = a / b if b else None
    rows_c[d.strftime("%Y-%m-%d")] = {"swaps_usd": a, "hourly_volumeUSD": b, "ratio": r}
    P(f"{d.strftime('%Y-%m-%d'):<12}{a:>18,.2f}{b:>18,.2f}{(r if r is not None else float('nan')):>9.4f}")
tot_a, tot_b = float(vol_swaps_day.sum()), float(vol_hourly_day.sum())
P(f"{'TOTAL':<12}{tot_a:>18,.2f}{tot_b:>18,.2f}{tot_a / tot_b:>9.4f}")
ratios_c = np.array([v["ratio"] for v in rows_c.values() if v["ratio"] is not None])
P(f"  daily ratio: min {ratios_c.min():.4f}  max {ratios_c.max():.4f}  mean {ratios_c.mean():.4f}")
P(f"  hourly file volumeUSD total incl. Sep-01 00:00 row: {float(hourly['volumeUSD'].sum()):,.2f}")
results["C_usd_volume_per_day"] = {
    "per_day": rows_c,
    "total_swaps_usd": tot_a,
    "total_hourly_volumeUSD_august": tot_b,
    "total_hourly_volumeUSD_all_745_rows": float(hourly["volumeUSD"].sum()),
    "ratio_total": tot_a / tot_b,
    "ratio_daily_min": float(ratios_c.min()),
    "ratio_daily_max": float(ratios_c.max()),
    "ratio_daily_mean": float(ratios_c.mean()),
}

# --------------------------------------------------------------------------- #
# D. HOURLY swap-derived volume vs volumeUSD
# --------------------------------------------------------------------------- #
H("D. HOURLY: sum|amount0|/1e6 over swaps in hour  /  poolHourDatas.volumeUSD")
swaps["hour"] = swaps["block_time"].dt.floor("h")
vol_swaps_hour = swaps.groupby("hour")["usd"].sum()
dh = pd.DataFrame({"swaps_usd": vol_swaps_hour}).join(hourly_aug[["volumeUSD"]], how="right").fillna({"swaps_usd": 0.0})
n_zero = int((dh["volumeUSD"] == 0).sum())
dh_nz = dh[dh["volumeUSD"] > 0].copy()
dh_nz["ratio"] = dh_nz["swaps_usd"] / dh_nz["volumeUSD"]
n_hours_no_swaps = int((dh["swaps_usd"] == 0).sum())
d_desc = describe(dh_nz["ratio"])
n_out = int(((dh_nz["ratio"] < 0.9) | (dh_nz["ratio"] > 1.1)).sum())
P(f"hours in August hourly file: {len(dh)}; hours with volumeUSD == 0 skipped: {n_zero}; hours with zero swaps in parquet: {n_hours_no_swaps}")
P("ratio distribution:")
print_desc(d_desc)
P(f"  hours with ratio outside [0.9, 1.1]: {n_out} of {len(dh_nz)}")
worst = dh_nz.reindex(dh_nz["ratio"].sub(1).abs().sort_values(ascending=False).index).head(10)
P("  10 hours with |ratio-1| largest:")
for ts_, r_ in worst.iterrows():
    P(f"    {fmt_ts(ts_)}  swaps_usd={r_['swaps_usd']:>14,.2f}  volumeUSD={r_['volumeUSD']:>14,.2f}  ratio={r_['ratio']:.4f}")
fig, ax = plt.subplots()
ax.hist(dh_nz["ratio"], bins=80, color="#4c72b0", edgecolor="white")
ax.axvline(1.0, color="black", lw=1, ls="--")
ax.set_xlabel("sum|amount0|/1e6 over swaps in hour  /  poolHourDatas.volumeUSD")
ax.set_ylabel("number of hours")
ax.set_title(f"Hourly swap-derived USD volume vs subgraph volumeUSD, Aug 2026 (n={len(dh_nz)} hours, median={d_desc['median']:.4f})")
fig.tight_layout()
chart_d = save_fig(fig, "D_hourly_volume_ratio_hist.png")
results["D_hourly_volume_ratio"] = {
    "hours_total": int(len(dh)), "hours_skipped_volumeUSD_zero": n_zero, "hours_with_zero_swaps": n_hours_no_swaps,
    "ratio": d_desc, "hours_outside_0.9_1.1": n_out,
    "worst_10": [{"hour": fmt_ts(t), "swaps_usd": float(r["swaps_usd"]), "volumeUSD": float(r["volumeUSD"]), "ratio": float(r["ratio"])}
                 for t, r in worst.iterrows()],
    "chart": chart_d,
}

# --------------------------------------------------------------------------- #
# E. POOL PRICE vs BINANCE
# --------------------------------------------------------------------------- #
H("E. POOL PRICE (after swap) vs BINANCE 1m CLOSE of the containing minute")
swaps["minute"] = swaps["block_time"].dt.floor("min")
kmap = k1m.set_index("ts")["close"]
swaps["bn_close"] = swaps["minute"].map(kmap)
matched = swaps.dropna(subset=["bn_close"]).copy()
matched["basis_bps"] = (matched["price_eth"] / matched["bn_close"] - 1) * 1e4
e_all = describe(matched["basis_bps"])
P(f"swaps matched to a Binance minute: {len(matched)} of {len(swaps)} (unmatched: {len(swaps) - len(matched)})")
P("basis_bps = (pool_price / binance_close - 1) * 1e4, ALL swaps:")
print_desc(e_all, " bps")
last_min = matched.groupby("minute").tail(1).copy()  # swaps sorted by block_number, log_index
e_last = describe(last_min["basis_bps"])
P(f"LAST swap of each minute only ({len(last_min)} minutes with >=1 swap out of {len(k1m)} klines):")
print_desc(e_last, " bps")
P(f"  share of last-swap-per-minute |basis| <= 5 bps: {(last_min['basis_bps'].abs() <= 5).mean() * 100:.2f}%;  <= 10 bps: {(last_min['basis_bps'].abs() <= 10).mean() * 100:.2f}%;  > 25 bps: {(last_min['basis_bps'].abs() > 25).mean() * 100:.2f}%")
ext = last_min.reindex(last_min["basis_bps"].abs().sort_values(ascending=False).index).head(5)
P("  5 largest |basis| minutes (last swap per minute):")
for _, r_ in ext.iterrows():
    P(f"    {fmt_ts(r_['block_time'])} block {int(r_['block_number'])}  pool={r_['price_eth']:.2f}  binance={r_['bn_close']:.2f}  basis={r_['basis_bps']:+.1f} bps")
# charts
fig, ax = plt.subplots()
ax.plot(last_min["minute"], last_min["basis_bps"], lw=0.4, color="#4c72b0")
ax.axhline(0, color="black", lw=0.8)
ax.set_xlabel("time (UTC), August 2026")
date_axis(ax)
ax.set_ylabel("basis, bps  (pool / Binance close - 1) x 1e4")
ax.set_title(f"Pool price vs Binance ETHUSDT 1m close, last swap per minute (n={len(last_min)}, mean={e_last['mean']:+.2f} bps, std={e_last['std']:.2f})")
fig.tight_layout()
chart_e1 = save_fig(fig, "E_basis_bps_timeseries.png")
fig, ax = plt.subplots()
ax.plot(k1m["ts"], k1m["close"], lw=0.6, color="#dd8452", label="Binance ETHUSDT perp 1m close")
ax.plot(last_min["minute"], last_min["price_eth"], lw=0.6, color="#4c72b0", alpha=0.8, label="Uniswap v3 pool price after last swap in minute")
ax.set_xlabel("time (UTC), August 2026")
date_axis(ax)
ax.set_ylabel("USDC (USDT) per ETH")
ax.set_title("ETH price: Uniswap v3 0.05% pool vs Binance perp, August 2026")
ax.legend(loc="upper left")
fig.tight_layout()
chart_e2 = save_fig(fig, "E_pool_vs_binance_price.png")
results["E_pool_vs_binance_basis"] = {
    "swaps_matched": int(len(matched)), "swaps_unmatched": int(len(swaps) - len(matched)),
    "basis_bps_all_swaps": e_all, "basis_bps_last_swap_per_minute": e_last,
    "minutes_with_swaps": int(len(last_min)), "klines_1m_total": int(len(k1m)),
    "share_abs_le_5bps": float((last_min["basis_bps"].abs() <= 5).mean()),
    "share_abs_le_10bps": float((last_min["basis_bps"].abs() <= 10).mean()),
    "share_abs_gt_25bps": float((last_min["basis_bps"].abs() > 25).mean()),
    "largest_5": [{"block_time": fmt_ts(r["block_time"]), "block_number": int(r["block_number"]), "pool_price": float(r["price_eth"]),
                   "binance_close": float(r["bn_close"]), "basis_bps": float(r["basis_bps"])} for _, r in ext.iterrows()],
    "charts": [chart_e1, chart_e2],
}

# --------------------------------------------------------------------------- #
# F. FUNDING
# --------------------------------------------------------------------------- #
H("F. FUNDING (Binance USD-M ETHUSDT)")
rates = fund["last_funding_rate"].astype(float)
intervals = sorted(fund["funding_interval_hours"].unique().tolist())
tod = sorted(set(fund["ts"].dt.strftime("%H:%M:%S")))
tod_rounded = sorted(set(fund["ts"].dt.round("min").dt.strftime("%H:%M")))
interval_h = float(intervals[0]) if len(intervals) == 1 else None
ann = float(rates.mean()) * (24 / interval_h) * 365 * 100 if interval_h else None
P(f"prints: {len(fund)}; first {fmt_ts(fund['ts'].min())}; last {fmt_ts(fund['ts'].max())}")
P(f"distinct funding_interval_hours: {intervals}")
P(f"times of day (exact): {tod}")
P(f"times of day (rounded to minute): {tod_rounded}")
P(f"rate min {rates.min():+.8f}  max {rates.max():+.8f}  mean {rates.mean():+.8f}  median {rates.median():+.8f}")
P(f"count positive {int((rates > 0).sum())}, negative {int((rates < 0).sum())}, zero {int((rates == 0).sum())}")
P("sign convention as stored: positive value = longs pay shorts (Binance convention)")
P(f"annualised mean = mean * (24/{interval_h:g}) * 365 = {ann:.4f}% per year")
P(f"sum of rates over the month = {rates.sum():+.8f}  (a 1 USD short earns this fraction; = {rates.sum() * 100:+.4f}%)")
P(f"expected prints at {interval_h:g}h over 31 days = {31 * 24 / interval_h:g}")
results["F_funding"] = {
    "prints": int(len(fund)), "first": fmt_ts(fund["ts"].min()), "last": fmt_ts(fund["ts"].max()),
    "distinct_interval_hours": intervals, "times_of_day_exact": tod, "times_of_day_rounded": tod_rounded,
    "rate_min": float(rates.min()), "rate_max": float(rates.max()), "rate_mean": float(rates.mean()), "rate_median": float(rates.median()),
    "count_positive": int((rates > 0).sum()), "count_negative": int((rates < 0).sum()), "count_zero": int((rates == 0).sum()),
    "sign_convention": "positive = longs pay shorts (Binance)",
    "annualised_mean_pct": ann, "sum_of_rates": float(rates.sum()), "expected_prints": 31 * 24 / interval_h,
}

# --------------------------------------------------------------------------- #
# G. INTER-BLOCK SPACING
# --------------------------------------------------------------------------- #
H("G. INTER-BLOCK SPACING from distinct (block_number, block_time) pairs in the swaps")
blocks = swaps[["block_number", "block_time"]].drop_duplicates().sort_values("block_number").reset_index(drop=True)
n_bn = int(blocks["block_number"].nunique())
P(f"distinct (block_number, block_time) pairs: {len(blocks)}; distinct block_numbers: {n_bn} "
  f"({'consistent: one time per block' if n_bn == len(blocks) else 'WARNING: some blocks have >1 block_time'})")
P(f"block_number min {int(blocks['block_number'].min())}  max {int(blocks['block_number'].max())}  span {int(blocks['block_number'].max() - blocks['block_number'].min())} blocks")
dbn = blocks["block_number"].diff().iloc[1:].to_numpy()
dt = blocks["block_time"].diff().iloc[1:].dt.total_seconds().to_numpy()
spacing = dt / dbn
g_desc = describe(spacing)
P("per-block spacing = dt / d(block_number) over consecutive distinct blocks:")
print_desc(g_desc, " s")
P(f"  share of per-block spacing exactly 12.0 s: {(spacing == 12.0).mean() * 100:.2f}%")
adj = dt[dbn == 1]
share12 = float((adj == 12.0).mean())
P(f"consecutive blocks with block_number diff == 1: count {adj.size}, mean dt {adj.mean():.4f} s, median {np.median(adj):.1f} s, "
  f"share exactly 12 s: {share12 * 100:.2f}%, min {adj.min():.0f} s, max {adj.max():.0f} s")
vals, cnts = np.unique(adj, return_counts=True)
P("  dt value counts for diff==1: " + ", ".join(f"{v:g}s:{c}" for v, c in zip(vals, cnts)))
gap_vals, gap_cnts = np.unique(dbn, return_counts=True)
P("  block-number gap distribution (first 12): " + ", ".join(f"{int(v)}:{c}" for v, c in list(zip(gap_vals, gap_cnts))[:12]) + f" ... max gap {int(dbn.max())}")
P(f"  overall wall-clock span / block span = {(blocks['block_time'].iloc[-1] - blocks['block_time'].iloc[0]).total_seconds() / (blocks['block_number'].iloc[-1] - blocks['block_number'].iloc[0]):.4f} s/block")
P(f"  swaps per block with swaps: mean {len(swaps) / len(blocks):.3f}, max {int(swaps.groupby('block_number').size().max())}")
results["G_block_spacing"] = {
    "distinct_block_time_pairs": int(len(blocks)), "distinct_blocks": n_bn,
    "block_min": int(blocks["block_number"].min()), "block_max": int(blocks["block_number"].max()),
    "per_block_spacing_s": g_desc, "share_per_block_spacing_exactly_12s": float((spacing == 12.0).mean()),
    "adjacent_blocks": {"count": int(adj.size), "mean_dt_s": float(adj.mean()), "median_dt_s": float(np.median(adj)),
                        "share_exactly_12s": share12, "min_dt_s": float(adj.min()), "max_dt_s": float(adj.max()),
                        "dt_value_counts": {f"{v:g}": int(c) for v, c in zip(vals, cnts)}},
    "block_gap_counts_first_12": {int(v): int(c) for v, c in list(zip(gap_vals, gap_cnts))[:12]}, "max_block_gap": int(dbn.max()),
    "wallclock_seconds_per_block_overall": float((blocks["block_time"].iloc[-1] - blocks["block_time"].iloc[0]).total_seconds() / (blocks["block_number"].iloc[-1] - blocks["block_number"].iloc[0])),
    "swaps_per_block_mean": float(len(swaps) / len(blocks)), "swaps_per_block_max": int(swaps.groupby("block_number").size().max()),
}

# --------------------------------------------------------------------------- #
# H. TAPE STATISTICS
# --------------------------------------------------------------------------- #
H("H. TAPE STATISTICS from Binance ETHUSDT 1m klines")
k = k1m.sort_values("ts").reset_index(drop=True)
gaps = k["ts"].diff().dt.total_seconds().iloc[1:]
P(f"klines: {len(k)}; first open_time {fmt_ts(k['ts'].iloc[0])}; last open_time {fmt_ts(k['ts'].iloc[-1])}; "
  f"expected 31*1440 = {31 * 1440}; minute gaps != 60 s: {int((gaps != 60).sum())}")
r = np.log(k["close"]).diff().dropna().to_numpy()
imax = int(np.argmax(np.abs(r)))
row_max = k.iloc[imax + 1]
kurt = float(stats.kurtosis(r, fisher=True, bias=True))
skew = float(stats.skew(r, bias=True))
kurt_manual = float(np.mean((r - r.mean()) ** 4) / np.mean((r - r.mean()) ** 2) ** 2 - 3)
ac_abs = float(np.corrcoef(np.abs(r[:-1]), np.abs(r[1:]))[0, 1])
ac_sgn = float(np.corrcoef(r[:-1], r[1:])[0, 1])
vol_1m = float(r.std(ddof=1) * math.sqrt(525600))
vol_1m_pop = float(r.std(ddof=0) * math.sqrt(525600))
daily_close = k.groupby(k["ts"].dt.floor("D"))["close"].last()
rd = np.log(daily_close).diff().dropna().to_numpy()
vol_d = float(rd.std(ddof=1) * math.sqrt(365))
m_open, m_close = float(k["open"].iloc[0]), float(k["close"].iloc[-1])
m_high, m_low = float(k["high"].max()), float(k["low"].min())
hi_row, lo_row = k.loc[k["high"].idxmax()], k.loc[k["low"].idxmin()]
n_05 = int((np.abs(r) > 0.005).sum())
n_1 = int((np.abs(r) > 0.01).sum())
n_02 = int((np.abs(r) > 0.002).sum())
P(f"1-minute log returns: n = {r.size}; mean {r.mean():+.3e}; std (ddof=1) {r.std(ddof=1):.6e}")
P(f"max |r_1m| = {r[imax]:+.6f} ({r[imax] * 100:+.4f}%) at {fmt_ts(row_max['ts'])} (kline open_time): "
  f"open {row_max['open']} high {row_max['high']} low {row_max['low']} close {row_max['close']} vol {row_max['volume']}  (prev close {k['close'].iloc[imax]})")
P(f"excess kurtosis (scipy.stats.kurtosis fisher=True, bias=True) = {kurt:.4f}   [manual m4/m2^2-3 = {kurt_manual:.4f}]")
P(f"skew (scipy.stats.skew) = {skew:.4f}")
P(f"lag-1 autocorr |r| = {ac_abs:.4f};  lag-1 autocorr signed r = {ac_sgn:.4f}")
P(f"realised annualised vol from 1m: std(r_1m, ddof=1)*sqrt(525600) = {vol_1m * 100:.2f}%  (ddof=0: {vol_1m_pop * 100:.2f}%)")
P(f"realised annualised vol from daily close-to-close (UTC days, {rd.size} returns from {len(daily_close)} daily closes): std*sqrt(365) = {vol_d * 100:.2f}%")
P(f"month open {m_open}  close {m_close}  high {m_high} (at {fmt_ts(hi_row['ts'])})  low {m_low} (at {fmt_ts(lo_row['ts'])})")
P(f"close-to-close over month (last close / first open - 1) = {(m_close / m_open - 1) * 100:+.4f}%;  log = {math.log(m_close / m_open):+.6f}")
P(f"minutes with |r| > 0.2%: {n_02};  > 0.5%: {n_05};  > 1%: {n_1}")
# charts
fig, ax = plt.subplots()
ax.plot(k["ts"], k["close"], lw=0.6, color="#4c72b0")
ax.scatter([row_max["ts"]], [row_max["close"]], color="red", zorder=5, s=40,
           label=f"max 1m move {r[imax] * 100:+.2f}% at {fmt_ts(row_max['ts'])}")
ax.set_xlabel("time (UTC), August 2026")
date_axis(ax)
ax.set_ylabel("ETHUSDT 1m close")
ax.set_title(f"Binance ETHUSDT perp, August 2026: open {m_open:.2f}, close {m_close:.2f}, high {m_high:.2f}, low {m_low:.2f}")
ax.legend(loc="upper left")
fig.tight_layout()
chart_h1 = save_fig(fig, "H_close_with_max_move.png")
fig, ax = plt.subplots()
bins = np.linspace(-0.01, 0.01, 201)
ax.hist(r, bins=bins, density=True, color="#4c72b0", edgecolor="none", label="1m log returns (empirical)")
xs = np.linspace(-0.01, 0.01, 800)
ax.plot(xs, stats.norm.pdf(xs, r.mean(), r.std(ddof=1)), color="#c44e52", lw=1.5, label=f"normal, same mean/std (std={r.std(ddof=1) * 1e4:.2f} bps)")
ax.set_yscale("log")
ax.set_ylim(bottom=1.0 / (r.size * (bins[1] - bins[0])) / 3, top=None)  # one observation per bin is the empirical floor
ax.set_xlabel("1-minute log return (clipped to +/-1% for display)")
ax.set_ylabel("density (log scale)")
ax.set_title(f"ETHUSDT 1m log returns, Aug 2026: excess kurtosis {kurt:.1f}, skew {skew:+.2f}, n={r.size}")
ax.legend(loc="upper right")
fig.tight_layout()
chart_h2 = save_fig(fig, "H_1m_return_hist.png")
results["H_tape_stats"] = {
    "klines": int(len(k)), "minute_gaps_not_60s": int((gaps != 60).sum()), "n_returns": int(r.size),
    "mean_r_1m": float(r.mean()), "std_r_1m_ddof1": float(r.std(ddof=1)),
    "max_abs_r_1m": {"log_return": float(r[imax]), "pct": float(r[imax] * 100), "open_time_utc": fmt_ts(row_max["ts"]),
                     "open": float(row_max["open"]), "high": float(row_max["high"]), "low": float(row_max["low"]),
                     "close": float(row_max["close"]), "prev_close": float(k["close"].iloc[imax]), "volume": float(row_max["volume"])},
    "excess_kurtosis": kurt, "excess_kurtosis_method": "scipy.stats.kurtosis(fisher=True, bias=True)", "excess_kurtosis_manual": kurt_manual,
    "skew": skew, "acf1_abs_returns": ac_abs, "acf1_signed_returns": ac_sgn,
    "realised_vol_1m_annualised": vol_1m, "realised_vol_1m_annualised_ddof0": vol_1m_pop,
    "realised_vol_daily_annualised": vol_d, "n_daily_returns": int(rd.size), "n_daily_closes": int(len(daily_close)),
    "month_open": m_open, "month_close": m_close, "month_high": m_high, "month_high_time": fmt_ts(hi_row["ts"]),
    "month_low": m_low, "month_low_time": fmt_ts(lo_row["ts"]),
    "month_return_close_over_open": m_close / m_open - 1, "month_log_return": math.log(m_close / m_open),
    "minutes_abs_r_gt_0.2pct": n_02, "minutes_abs_r_gt_0.5pct": n_05, "minutes_abs_r_gt_1pct": n_1,
    "charts": [chart_h1, chart_h2],
}

# --------------------------------------------------------------------------- #
# I. LIQUIDITY
# --------------------------------------------------------------------------- #
H("I. IN-RANGE LIQUIDITY (hourly `liquidity`) and the $1M position's L")
liq = hourly_aug["liquidity"].astype(float)
P(f"hourly liquidity rows (August): {len(liq)}; NaN: {int(liq.isna().sum())}; zeros: {int((liq == 0).sum())}")
P(f"  min {liq.min():.6e} at {fmt_ts(liq.idxmin())};  max {liq.max():.6e} at {fmt_ts(liq.idxmax())}")
P(f"  mean {liq.mean():.6e};  median {liq.median():.6e}")
P(f"  first hour {fmt_ts(liq.index[0])}: {liq.iloc[0]:.6e};  last hour {fmt_ts(liq.index[-1])}: {liq.iloc[-1]:.6e};  "
  f"Sep-01 00:00 row: {float(hourly['liquidity'].iloc[-1]):.6e}")
P(f"  max/min ratio {liq.max() / liq.min():.3f}")
sig = inspect.signature(run_claude.build_position)
P(f"run_claude.build_position{sig}")
P("  body:")
for ln in inspect.getsource(run_claude.build_position).splitlines():
    P("    " + ln)
A = bt.Assumptions()
cfg = run_claude.Config()
P(f"bt.Assumptions() defaults: capital_usd={A.capital_usd}, range_width={A.range_width}")
P(f"run_claude.Config() defaults: capital_usd={cfg.capital_usd}, range_width={cfg.range_width}, tick_spacing={cfg.tick_spacing}")
assert A.capital_usd == cfg.capital_usd and A.range_width == cfg.range_width
P0 = float(swaps["price_eth"].iloc[0])
t0 = swaps["block_time"].iloc[0]
pos = run_claude.build_position(P0, cfg, t0)
L1 = float(pos.liquidity)
P(f"P0 = first swap pool price = {P0:.4f} USDC/ETH at {fmt_ts(t0)}")
P(f"$1M position at +/-{cfg.range_width:.0%}: ticks [{pos.tick_lower}, {pos.tick_upper}] "
  f"= ETH price [{pos.eth_price_lower:.2f}, {pos.eth_price_upper:.2f}], L = {L1:.6e}, "
  f"value check {pos.value_usd(P0):,.2f} USD, max_delta {pos.max_delta_eth:.4f} ETH")
share = L1 / (L1 + liq)
P(f"share = L_pos / (L_pos + hourly liquidity):  first hour {share.iloc[0]:.6f} ({share.iloc[0] * 100:.4f}%),  "
  f"mean {share.mean():.6f} ({share.mean() * 100:.4f}%),  min {share.min():.6f},  max {share.max():.6f},  last hour {share.iloc[-1]:.6f}")
P(f"  (compare: naive L_pos / hourly liquidity at first hour = {L1 / liq.iloc[0]:.6f})")
P("")
P(f"{'capital':>12}{'L_position':>16}{'share_first_hour':>18}{'share_month_mean':>18}{'share_min':>12}{'share_max':>12}")
cap_rows = {}
for cap in (100_000, 300_000, 1_000_000, 3_000_000, 10_000_000):
    pc = run_claude.build_position(P0, cfg, t0, capital_usd=cap)
    Lc = float(pc.liquidity)
    sc = Lc / (Lc + liq)
    cap_rows[str(cap)] = {"L": Lc, "share_first_hour": float(sc.iloc[0]), "share_month_mean": float(sc.mean()),
                          "share_min": float(sc.min()), "share_max": float(sc.max())}
    P(f"{cap:>12,}{Lc:>16.4e}{sc.iloc[0]:>18.6f}{sc.mean():>18.6f}{sc.min():>12.6f}{sc.max():>12.6f}")
fig, ax = plt.subplots()
ax.plot(liq.index, liq.values, lw=0.8, color="#4c72b0", label="hourly in-range liquidity (poolHourDatas)")
ax.axhline(L1, color="#c44e52", lw=1.2, ls="--", label=f"$1M position L at +/-15% = {L1:.3e}")
ax.set_yscale("log")
ax.set_xlabel("time (UTC), August 2026")
date_axis(ax)
ax.set_ylabel("liquidity L (raw units, log scale)")
ax.set_title(f"Pool in-range liquidity vs a $1M +/-15% position (share at first hour {share.iloc[0] * 100:.3f}%, month mean {share.mean() * 100:.3f}%)")
ax.legend(loc="lower right")
fig.tight_layout()
chart_i = save_fig(fig, "I_hourly_liquidity_vs_position.png")
results["I_liquidity"] = {
    "hourly_rows_august": int(len(liq)),
    "min": float(liq.min()), "min_at": fmt_ts(liq.idxmin()), "max": float(liq.max()), "max_at": fmt_ts(liq.idxmax()),
    "mean": float(liq.mean()), "median": float(liq.median()),
    "first_hour": {"ts": fmt_ts(liq.index[0]), "liquidity": float(liq.iloc[0])},
    "last_hour": {"ts": fmt_ts(liq.index[-1]), "liquidity": float(liq.iloc[-1])},
    "sep01_row": {"ts": fmt_ts(hourly.index[-1]), "liquidity": float(hourly["liquidity"].iloc[-1])},
    "defaults": {"capital_usd": A.capital_usd, "range_width": A.range_width, "tick_spacing": cfg.tick_spacing},
    "P0": P0, "P0_time": fmt_ts(t0),
    "position_1M": {"tick_lower": int(pos.tick_lower), "tick_upper": int(pos.tick_upper), "eth_price_lower": float(pos.eth_price_lower),
                    "eth_price_upper": float(pos.eth_price_upper), "L": L1, "value_usd_check": float(pos.value_usd(P0)),
                    "max_delta_eth": float(pos.max_delta_eth)},
    "share_1M": {"first_hour": float(share.iloc[0]), "month_mean": float(share.mean()), "min": float(share.min()),
                 "max": float(share.max()), "last_hour": float(share.iloc[-1])},
    "share_by_capital": cap_rows, "chart": chart_i,
}

# --------------------------------------------------------------------------- #
# J. TICK CHECK
# --------------------------------------------------------------------------- #
H("J. TICK CHECK: floor(ln(P_raw)/ln(1.0001)) == tick using the exact sqrtPriceX96 string")
getcontext().prec = 80
Q96D = Decimal(2) ** 96
LN_BASE = Decimal("1.0001").ln()
idxs = [0] + [int(round(len(swaps) * f)) for f in (0.2, 0.4, 0.6, 0.8)]
j_rows = []
all_ok = True
for i in idxs:
    row = swaps.iloc[i]
    s_int = int(row["sqrt_price_x96_str"])
    p_raw = (Decimal(s_int) / Q96D) ** 2
    tick_c = int((p_raw.ln() / LN_BASE).to_integral_value(rounding="ROUND_FLOOR"))
    p_eth = float(Decimal(10) ** 12 / p_raw)
    ok = tick_c == int(row["tick"])
    all_ok &= ok
    # also check the float64 column vs the exact string
    rel = abs(float(row["sqrt_price_x96"]) - s_int) / s_int
    P(f"  row {i:>6}  {fmt_ts(row['block_time'])}  block {int(row['block_number'])}")
    P(f"      sqrt_price_x96_str = {row['sqrt_price_x96_str']}   (float64 col rel err {rel:.2e})")
    P(f"      P_raw = {p_raw:.6E}   tick(stored) = {int(row['tick'])}   tick(computed) = {tick_c}   {'OK' if ok else 'MISMATCH'}")
    P(f"      price = 1e12 / P_raw = {p_eth:.4f} USDC/ETH   (Binance close that minute: {row['bn_close']})")
    j_rows.append({"row": i, "block_time": fmt_ts(row["block_time"]), "block_number": int(row["block_number"]),
                   "sqrt_price_x96_str": row["sqrt_price_x96_str"], "tick_stored": int(row["tick"]), "tick_computed": tick_c,
                   "ok": bool(ok), "p_raw": f"{p_raw:.12E}", "price_usdc_per_eth": p_eth,
                   "binance_close": (None if pd.isna(row["bn_close"]) else float(row["bn_close"]))})
# full-sample check with float64 (cheap): float log on the float64 column
tick_f = np.floor(np.log((swaps["sqrt_price_x96"] / 2**96) ** 2) / np.log(1.0001)).astype(int)
n_mismatch_f = int((tick_f != swaps["tick"]).sum())
P(f"  full-sample float64 check: floor(ln((sqrt_price_x96/2^96)^2)/ln 1.0001) != tick on {n_mismatch_f} of {len(swaps)} rows "
  f"(float rounding can flip rows sitting exactly on a tick boundary)")
P(f"  all 5 exact checks OK: {all_ok}")
results["J_tick_check"] = {"rows": j_rows, "all_ok": bool(all_ok), "float64_full_sample_mismatches": n_mismatch_f}

# --------------------------------------------------------------------------- #
# K. STOCK-CODE DEDUP KEY
# --------------------------------------------------------------------------- #
H("K. ROWS SHARING THE STOCK DEDUP KEY (block_number, block_time, sqrt_price_x96)  [backtest.py:813-815]")
key = ["block_number", "block_time", "sqrt_price_x96"]
dup_mask = swaps.duplicated(subset=key, keep=False)
n_dup_rows = int(dup_mask.sum())
n_groups = int(swaps[dup_mask].groupby(key).ngroups) if n_dup_rows else 0
n_lost = int(n_dup_rows - n_groups)
P(f"rows sharing the key with another row: {n_dup_rows} of {len(swaps)} in {n_groups} groups; "
  f"drop_duplicates(keep='first') would drop {n_lost} rows")
dup_key_str = swaps.duplicated(subset=["block_number", "block_time", "sqrt_price_x96_str"], keep=False)
P(f"same check on the exact string column: {int(dup_key_str.sum())} rows")
float_only = swaps[dup_mask & ~dup_key_str]
P(f"rows that collide ONLY because float64 rounds two distinct sqrtPriceX96 strings to one value: {len(float_only)}")
for _, rr in float_only.iterrows():
    P(f"      block {int(rr['block_number'])} log_index {int(rr['log_index']):>4}  amount0 {rr['amount0']:>+16,.0f}  amount1 {rr['amount1']:>+22,.0f}  str={rr['sqrt_price_x96_str']}")
dust = swaps[(swaps["amount0"].abs() <= 1) & (swaps["amount1"] == 0)]
P(f"dust rows in the whole file (|amount0| <= 1 raw and amount1 == 0): {len(dust)}; of these {int(dup_mask[dust.index].sum())} share the dedup key with another row")
dup_full = swaps.duplicated(subset=["block_number", "log_index"], keep=False)
P(f"rows sharing (block_number, log_index): {int(dup_full.sum())}  (true duplicates)")
examples = []
if n_dup_rows:
    grp = swaps[dup_mask].groupby(key)
    for gi, (kk, g) in enumerate(grp):
        if gi >= 3:
            break
        P(f"  example group {gi + 1}: block {int(kk[0])} {fmt_ts(kk[1])} sqrt_price_x96={kk[2]:.6e}")
        ex = []
        for _, rr in g.iterrows():
            P(f"      log_index {int(rr['log_index']):>4}  amount0 {rr['amount0']:>+20,.0f}  amount1 {rr['amount1']:>+26,.0f}  "
              f"str={rr['sqrt_price_x96_str']}")
            ex.append({"log_index": int(rr["log_index"]), "amount0": float(rr["amount0"]), "amount1": float(rr["amount1"]),
                       "sqrt_price_x96_str": rr["sqrt_price_x96_str"]})
        examples.append({"block_number": int(kk[0]), "block_time": fmt_ts(kk[1]), "sqrt_price_x96": float(kk[2]), "rows": ex})
    usd_lost = float(swaps[dup_mask].groupby(key)["usd"].apply(lambda s: s.iloc[1:].sum()).sum())
    P(f"  USD volume in the rows keep='first' would drop: {usd_lost:,.2f} ({usd_lost / swaps['usd'].sum() * 100:.4f}% of month volume)")
else:
    usd_lost = 0.0
results["K_dedup_key"] = {"rows_sharing_key": n_dup_rows, "groups": n_groups, "rows_dropped_by_keep_first": n_lost,
                          "rows_sharing_key_exact_string": int(dup_key_str.sum()), "rows_sharing_block_logindex": int(dup_full.sum()),
                          "rows_colliding_float64_only": int(len(float_only)),
                          "float64_only_collisions": [{"block_number": int(rr["block_number"]), "log_index": int(rr["log_index"]), "amount0": float(rr["amount0"]),
                                                        "amount1": float(rr["amount1"]), "sqrt_price_x96_str": rr["sqrt_price_x96_str"]} for _, rr in float_only.iterrows()],
                          "dust_rows_total": int(len(dust)), "dust_rows_sharing_key": int(dup_mask[dust.index].sum()),
                          "usd_volume_dropped": usd_lost, "examples": examples}

# --------------------------------------------------------------------------- #
# SUMMARY
# --------------------------------------------------------------------------- #
H("SUMMARY")
summary = {
    "total_swaps": int(len(swaps)),
    "total_usd_volume_swaps": tot_a,
    "total_usd_volume_hourly_file_august": tot_b,
    "volume_ratio_swaps_over_hourly": tot_a / tot_b,
    "basis_bps_all_swaps_mean": e_all["mean"], "basis_bps_all_swaps_p1": e_all["p1"], "basis_bps_all_swaps_p99": e_all["p99"],
    "basis_bps_lastmin_mean": e_last["mean"], "basis_bps_lastmin_p1": e_last["p1"], "basis_bps_lastmin_p99": e_last["p99"],
    "funding_annualised_mean_pct": ann, "funding_sum_month": float(rates.sum()),
    "block_spacing_median_s": g_desc["median"], "adjacent_block_share_12s": share12,
    "max_1m_move_pct": float(r[imax] * 100), "max_1m_move_time": fmt_ts(row_max["ts"]),
    "realised_vol_1m_pct": vol_1m * 100, "realised_vol_daily_pct": vol_d * 100,
    "excess_kurtosis_1m": kurt,
    "share_1M_first_hour": float(share.iloc[0]), "share_1M_month_mean": float(share.mean()),
    "dedup_key_rows": n_dup_rows,
}
for kk, vv in summary.items():
    P(f"  {kk}: {vv}")
results["summary"] = summary
results["charts"] = sorted(os.listdir(CHARTS))

with open(f"{OUT}/data_checks.json", "w") as fh:
    json.dump(results, fh, indent=2, default=str)
with open(f"{OUT}/data_checks.txt", "w") as fh:
    fh.write("\n".join(_lines) + "\n")
print(f"\nwrote {OUT}/data_checks.json and {OUT}/data_checks.txt")
