"""Charts for SCENARIOS.md and GUIDE.md, from the raw run outputs only.
Every figure is written to docs/guide/charts/<name>.png at 1400x700 px, 150 dpi.
"""
import gzip
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

RAW = Path("/home/user/backtest-lp/docs/guide/raw")
OUT = Path("/home/user/backtest-lp/docs/guide/charts")
OUT.mkdir(exist_ok=True)
IDX = json.load(open(RAW / "scenarios_index.json"))
HIDX = json.load(open(RAW / "scenarios_hourly_index.json"))
FIG = dict(figsize=(1400 / 150, 700 / 150), dpi=150)
C = {"net": "#1f77b4", "fees": "#2ca02c", "lvr": "#d62728", "fund": "#17becf", "hedge": "#ff7f0e", "gas": "#9467bd", "price": "#7f7f7f", "k": "#111111"}
MADE = []


def ts(name):
    with gzip.open(RAW / name / "timeseries.csv.gz", "rt") as fh:
        return pd.read_csv(fh, index_col=0, parse_dates=True)


def save(fig, name, note):
    fig.tight_layout()
    fig.savefig(OUT / f"{name}.png")
    plt.close(fig)
    MADE.append({"file": f"charts/{name}.png", "note": note})
    print("wrote", name)


def bars_by_term(names, labels, xlabel, fname, title, apr=False):
    keys = ["fees_usd", "lvr_usd", "funding_usd", "hedge_cost_usd", "gas_reposition_usd", "net_usd"]
    if apr:
        keys = ["fee_apr_pct", "lvr_apr_pct", "funding_apr_pct", "hedge_cost_apr_pct", "net_apr_pct"]
    fig, ax = plt.subplots(**FIG)
    x = np.arange(len(names))
    w = 0.8 / len(keys)
    for i, k in enumerate(keys):
        vals = []
        for n in names:
            v = IDX[n][k]
            if not apr and k in ("lvr_usd", "hedge_cost_usd", "gas_reposition_usd"):
                v = -v
            vals.append(v)
        col = {"fees_usd": C["fees"], "fee_apr_pct": C["fees"], "lvr_usd": C["lvr"], "lvr_apr_pct": C["lvr"], "funding_usd": C["fund"],
               "funding_apr_pct": C["fund"], "hedge_cost_usd": C["hedge"], "hedge_cost_apr_pct": C["hedge"], "gas_reposition_usd": C["gas"],
               "net_usd": C["net"], "net_apr_pct": C["net"]}[k]
        lab = {"fees_usd": "fees", "lvr_usd": "-LVR (klines mark)", "funding_usd": "funding", "hedge_cost_usd": "-hedge cost", "gas_reposition_usd": "-gas", "net_usd": "net (path-exact)",
               "fee_apr_pct": "fee APR", "lvr_apr_pct": "LVR APR (sign as shown by app)", "funding_apr_pct": "funding APR", "hedge_cost_apr_pct": "hedge cost APR", "net_apr_pct": "net APR"}[k]
        ax.bar(x + (i - len(keys) / 2 + 0.5) * w, vals, w, label=lab, color=col)
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("% APR on capital base" if apr else "USD over 31 days")
    ax.set_title(title)
    ax.legend(fontsize=7, ncol=3)
    ax.grid(axis="y", alpha=0.3)
    save(fig, fname, title)


# ---- default run ----------------------------------------------------------
d = ts("default")
fig, ax = plt.subplots(**FIG)
ax.plot(d.index, d["cum_net"], color=C["net"], lw=1.5, label="cumulative net P&L, path-exact (USD)")
ax.plot(d.index, d["fee_usd"].cumsum(), color=C["fees"], lw=1, label="cumulative fees")
ax.plot(d.index, (d["lp_pnl_usd"] + d["hedge_pnl_usd"]).cumsum(), color=C["lvr"], lw=1, label="cumulative hedged inventory P&L (LP value + perp)")
ax.axhline(0, color="k", lw=0.5)
ax2 = ax.twinx()
ax2.plot(d.index, d["eth_price"], color=C["price"], lw=0.7, alpha=0.7, label="ETH price (right axis)")
ax2.set_ylabel("ETHUSDT perp price")
ax.set_ylabel("USD")
ax.set_title("Default run: cumulative P&L terms, real August 2026 tape")
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="lower left")
ax.grid(alpha=0.3)
save(fig, "default_cum_pnl", "Default run cumulative net, fees, hedged inventory P&L, with ETH price")

fig, ax = plt.subplots(**FIG)
ax.plot(d.index, d["lp_delta"], color="#8c564b", lw=1, label="LP delta (ETH held by the position)")
ax.plot(d.index, -d["hedge_delta"], color="#e377c2", lw=1, label="perp short (ETH, sign flipped)")
ax.set_ylabel("ETH")
ax.set_title("Default run: LP delta and the perp hedge, 60 s grid, band 3% of max delta")
ax.legend(fontsize=8)
ax.grid(alpha=0.3)
save(fig, "default_delta_hedge", "Default run LP delta vs hedge")

# reconciliation: path-exact hedged loss vs LVR measures
agg = ts("default_aggtrades")
m60 = ts("markout_60")
fig, ax = plt.subplots(**FIG)
ax.plot(d.index, -(d["lp_pnl_usd"] + d["hedge_pnl_usd"]).cumsum(), color=C["k"], lw=1.6, label="hedged inventory loss, path-exact (klines run)")
ax.plot(d.index, d["lvr_usd"].cumsum(), color=C["lvr"], lw=1.2, label="per-swap LVR, 1m-kline mark (default run)")
ax.plot(agg.index, agg["lvr_usd"].cumsum(), color="#ff7f0e", lw=1.2, label="per-swap LVR, aggTrades 1s mark")
ax.plot(m60.index, m60["lvr_usd"].cumsum(), color="#2ca02c", lw=1.0, ls="--", label="per-swap LVR, 1m klines + 60 s markout")
cf = json.load(open(RAW / "default" / "summary.json"))["closed_form_lvr_usd"]
ax.axhline(cf, color="#9467bd", lw=1, ls=":", label=f"closed-form ½σ²P²|V''| integral = {cf:,.0f}")
ax.axhline(0, color="k", lw=0.5)
ax.set_ylabel("USD, cumulative")
ax.set_title("LVR reconciliation: four measures of the same cost")
ax.legend(fontsize=7)
ax.grid(alpha=0.3)
save(fig, "lvr_reconciliation", "Cumulative LVR: path-exact vs per-swap (klines / aggTrades / markout) vs closed form")

# daily attribution bars for default
dd = d[["fee_usd", "lvr_usd", "funding_usd", "hedge_cost_usd", "gas_usd", "net_usd"]].resample("1D").sum()
fig, ax = plt.subplots(**FIG)
x = np.arange(len(dd))
ax.bar(x - 0.3, dd["fee_usd"], 0.2, color=C["fees"], label="fees")
ax.bar(x - 0.1, -dd["lvr_usd"], 0.2, color=C["lvr"], label="-LVR (klines mark)")
ax.bar(x + 0.1, -dd["hedge_cost_usd"] - dd["gas_usd"], 0.2, color=C["hedge"], label="-hedge cost - gas")
ax.bar(x + 0.3, dd["net_usd"], 0.2, color=C["net"], label="net (path-exact)")
ax.set_xticks(x[::3])
ax.set_xticklabels([t.strftime("%d") for t in dd.index[::3]])
ax.set_xlabel("day of August 2026")
ax.set_ylabel("USD per day")
ax.set_title("Default run: daily P&L terms")
ax.legend(fontsize=8)
ax.grid(axis="y", alpha=0.3)
save(fig, "default_daily_bars", "Default run daily bars")

# ---- sweeps ---------------------------------------------------------------
bars_by_term([f"range_{w:.2f}" for w in (0.02, 0.05, 0.10, 0.15, 0.25, 0.50)], ["±2%", "±5%", "±10%", "±15%", "±25%", "±50%"],
             "range width", "sweep_range", "Range width sweep (hedge band 3%, $1M)")
bars_by_term([f"band_{b:.3f}" for b in (0.005, 0.01, 0.02, 0.03, 0.05, 0.10)], ["0.5%", "1%", "2%", "3%", "5%", "10%"],
             "hedge band (fraction of max delta)", "sweep_band", "Hedge band sweep (range ±15%, $1M)")
fig, ax = plt.subplots(**FIG)
names = [f"band_{b:.3f}" for b in (0.005, 0.01, 0.02, 0.03, 0.05, 0.10)]
ax.plot([IDX[n]["n_hedge_trades"] for n in names], [IDX[n]["hedge_cost_usd"] for n in names], "o-", color=C["hedge"], label="hedge execution cost (USD)")
for n, b in zip(names, ("0.5%", "1%", "2%", "3%", "5%", "10%")):
    ax.annotate(b, (IDX[n]["n_hedge_trades"], IDX[n]["hedge_cost_usd"]), textcoords="offset points", xytext=(5, 5), fontsize=8)
ax.set_xscale("log")
ax.set_xlabel("number of hedge trades in 31 days (log)")
ax.set_ylabel("USD")
ax.set_title("Hedge band: trade count against execution cost")
ax.grid(alpha=0.3, which="both")
ax.legend(fontsize=8)
save(fig, "sweep_band_trades_cost", "Band sweep: trades vs cost")
bars_by_term([f"grid_{g}" for g in (10, 30, 60, 120, 300)], ["10 s", "30 s", "60 s", "120 s", "300 s"], "hedge decision grid", "sweep_grid", "Hedge grid sweep")
bars_by_term([f"fee_{f:.4f}" for f in (0.0001, 0.0005, 0.003, 0.01)], ["1 bp", "5 bp", "30 bp", "100 bp"], "LP fee tier applied to the 0.05% pool's flow", "sweep_fee", "Fee tier sweep (not economically coherent, see text)")
names = [f"capital_{c}k" for c in (100, 300, 1000, 3000, 10000)]
fig, ax = plt.subplots(**FIG)
ax.plot([100e3, 300e3, 1e6, 3e6, 1e7], [IDX[n]["fee_apr_pct"] for n in names], "o-", color=C["fees"], label="fee APR (%)")
ax.plot([100e3, 300e3, 1e6, 3e6, 1e7], [IDX[n]["net_apr_pct"] for n in names], "o-", color=C["net"], label="net APR (%)")
ax.plot([100e3, 300e3, 1e6, 3e6, 1e7], [IDX[n]["hedge_cost_apr_pct"] for n in names], "o-", color=C["hedge"], label="hedge cost APR (%)")
ax2 = ax.twinx()
ax2.plot([100e3, 300e3, 1e6, 3e6, 1e7], [IDX[n]["mean_liquidity_share_pct"] for n in names], "s--", color=C["k"], label="mean in-range liquidity share (%), right")
ax.set_xscale("log")
ax.set_xlabel("capital deployed in the LP (USD, log)")
ax.set_ylabel("% APR on capital base")
ax2.set_ylabel("share of pool liquidity (%)")
ax.set_title("Capital sweep: does it scale")
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=7)
ax.grid(alpha=0.3, which="both")
save(fig, "sweep_capital", "Capital sweep")
names = [f"leverage_{l}" for l in (1, 2, 3, 5, 10)]
fig, ax = plt.subplots(**FIG)
ax.bar(np.arange(5) - 0.2, [IDX[n]["capital_base_usd"] / 1e6 for n in names], 0.4, color=C["k"], label="capital base (USD m)")
ax2 = ax.twinx()
ax2.plot(np.arange(5), [IDX[n]["net_apr_pct"] for n in names], "o-", color=C["net"], label="net APR (%), right")
ax.set_xticks(np.arange(5))
ax.set_xticklabels(["1x", "2x", "3x", "5x", "10x"])
ax.set_xlabel("perp leverage")
ax.set_ylabel("USD millions")
ax2.set_ylabel("% APR")
ax.set_title("Leverage sweep: same USD P&L, different denominator")
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=8)
ax.grid(axis="y", alpha=0.3)
save(fig, "sweep_leverage", "Leverage sweep")

# reposition on/off
on, off = ts("reposition_on"), ts("reposition_off")
fig, ax = plt.subplots(**FIG)
ax.plot(on.index, on["cum_net"], color=C["net"], label="reposition on (default): cumulative net")
ax.plot(off.index, off["cum_net"], color=C["lvr"], label="reposition off: cumulative net")
ax.plot(on.index, on["fee_usd"].cumsum(), color=C["fees"], ls="--", lw=0.9, label="reposition on: cumulative fees")
ax.plot(off.index, off["fee_usd"].cumsum(), color="#98df8a", ls="--", lw=0.9, label="reposition off: cumulative fees")
ax2 = ax.twinx()
ax2.plot(on.index, on["eth_price"], color=C["price"], lw=0.7, alpha=0.6, label="ETH price (right)")
ax2.axhline(2143.12, color=C["price"], lw=0.6, ls=":")
ax.set_ylabel("USD")
ax2.set_ylabel("ETHUSDT")
ax.set_title("Reposition on vs off (upper bound 2,143 dotted)")
h1, l1 = ax.get_legend_handles_labels()
h2, l2 = ax2.get_legend_handles_labels()
ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper left")
ax.grid(alpha=0.3)
save(fig, "reposition_on_off", "Reposition on vs off")

# weekly windows
bars_by_term([f"week_{i}" for i in (1, 2, 3, 4)], ["Aug 1-7", "Aug 8-14", "Aug 15-21", "Aug 22-28"], "7-day sub-window", "weekly_usd", "Weekly sub-windows, USD terms")
bars_by_term([f"week_{i}" for i in (1, 2, 3, 4)], ["Aug 1-7", "Aug 8-14", "Aug 15-21", "Aug 22-28"], "7-day sub-window", "weekly_apr", "Weekly sub-windows, annualised", apr=True)

# hourly engine and stress
names = ["hourly_localcsv", "hourly_1hklines_default", "hourly_1hklines_measured", "hourly_1hklines_measured_ratio", "hourly_stress_default", "hourly_stress_30d1", "hourly_stress_30d1_step"]
labels = ["Local CSV\n(app path)", "1h klines\nshare 0.1%", "1h klines\nshare 2.65%", "1h klines\nshare 2.65%\nratio 1.02%", "stress\n-20% 7d", "stress\n-30% 1d\nlinear", "stress\n-30% 1d\nstep"]
fig, ax = plt.subplots(**FIG)
x = np.arange(len(names))
ax.bar(x - 0.3, [HIDX[n]["fees_usd"] for n in names], 0.2, color=C["fees"], label="fees")
ax.bar(x - 0.1, [HIDX[n]["funding_usd"] for n in names], 0.2, color=C["fund"], label="funding")
ax.bar(x + 0.1, [-HIDX[n]["hedge_cost_usd"] for n in names], 0.2, color=C["hedge"], label="-hedge cost")
ax.bar(x + 0.3, [HIDX[n]["net_usd"] for n in names], 0.2, color=C["net"], label="net (as reported by the hourly engine)")
ax.set_xticks(x)
ax.set_xticklabels(labels, fontsize=7)
ax.axhline(0, color="k", lw=0.5)
ax.set_ylabel("USD over 30 days")
ax.set_title("hourly engine runs (net includes the sign error in realised hedge P&L, see text)")
ax.legend(fontsize=7)
ax.grid(axis="y", alpha=0.3)
save(fig, "hourly_runs", "Hourly engine runs")

h = pd.read_csv(RAW / "hourly_localcsv" / "timeseries.csv", index_col=0, parse_dates=True)
s1 = pd.read_csv(RAW / "hourly_stress_30d1" / "timeseries.csv", index_col=0, parse_dates=True)
s2 = pd.read_csv(RAW / "hourly_stress_30d1_step" / "timeseries.csv", index_col=0, parse_dates=True)
fig, ax = plt.subplots(**FIG)
ax.plot(h.index, h["price"], color=C["price"], label="unshocked 1h close")
ax.plot(s1.index, s1["price"], color=C["lvr"], label="-30% linear over last 1 day")
ax.plot(s2.index, s2["price"], color=C["hedge"], ls="--", label="-30% step over last 1 day")
ax.set_ylabel("ETHUSDT")
ax.set_title("Stress shock: what _apply_stress_shock does to the tape")
ax.legend(fontsize=8)
ax.grid(alpha=0.3)
save(fig, "stress_tape", "Stress shock applied to the price tape")

json.dump(MADE, open(OUT / "INDEX.json", "w"), indent=1)
print(len(MADE), "charts")
