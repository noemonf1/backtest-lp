# Recompute the default 5x6 sweep outside the UI to record the full table
# (the UI grid only exposes the rendered rows to the DOM). Same inputs as run 10.
import sys, time; sys.path.insert(0, "/home/user/backtest-lp")
import backtest as bt, pandas as pd
R = "/home/user/backtest-lp/"
t0 = time.time()
swaps = bt.load_swaps(R + "data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet")
cex = bt.load_cex_prices(R + "data/ETHUSDT-klines-1m-2026-08.csv")
funding = bt.load_funding_series(R + "data/ETHUSDT-fundingRate-2026-08.csv")
print("loaded in", round(time.time() - t0), "s", len(swaps), len(cex), len(funding), flush=True)
a = bt.Assumptions()
res = bt.run_swap_level(a, swaps, cex, funding)
print("baseline net_apr", res.summary.get("net_apr_pct"), "days", res.summary.get("days"), flush=True)
tbl = bt.sweep_swap_level_axes(a, swaps, cex, funding,
    x_field="hedge_band", x_values=bt.SWEEPABLE_FIELDS["hedge_band"]["default_values"],
    y_field="range_width", y_values=bt.SWEEPABLE_FIELDS["range_width"]["default_values"])
pd.set_option("display.width", 250); pd.set_option("display.max_columns", 30)
out = tbl.to_string()
with open(R + "docs/guide/raw/shots_local_sweep_table.txt", "a") as f:
    f.write("\n\n=== Full 30-cell sweep table recomputed with bt.sweep_swap_level_axes on the same three files and default Assumptions (same call the app makes) ===\n")
    f.write(f"baseline (defaults): net_apr_pct={res.summary.get('net_apr_pct')} days={res.summary.get('days')}\n")
    f.write(out + "\n")
    best = tbl.loc[tbl['net_apr'].idxmax()]
    f.write(f"best cell: {best.to_dict()}\n")
print(out, flush=True)
