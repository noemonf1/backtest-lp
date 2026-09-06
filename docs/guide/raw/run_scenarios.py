"""Phase 3: every engine run on the real August 2026 files.

Each scenario writes docs/guide/raw/<scenario>/summary.json, timeseries.csv.gz
(the full hedge-grid frame from run_claude.run_backtest, all columns) and
diag.json (position at t0, hedge statistics, attribution sums, reconciliation
terms). docs/guide/raw/scenarios_index.json lists every scenario with its
overrides and headline numbers.

Inputs are loaded exactly as app.py loads them in `Real data (files)` mode
(app.py:1105-1111): bt.load_swaps, bt.load_cex_prices, bt.load_funding_series,
with an empty Backtest window (no clipping).
"""
from __future__ import annotations

import gzip
import json
import math
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/user/backtest-lp")
import backtest as bt  # noqa: E402
import run_claude as rc  # noqa: E402

RAW = Path("/home/user/backtest-lp/docs/guide/raw")
DATA = Path("/home/user/backtest-lp/data")
SWAPS = DATA / "swaps_0x88e6a0_2026-08-01_2026-08-31.parquet"
CEX_1M = DATA / "ETHUSDT-klines-1m-2026-08.csv"
CEX_1H = DATA / "ETHUSDT-klines-1h-2026-08.csv"
CEX_AGG = DATA / "ETHUSDT-aggTrades-2026-08.csv"
FUND = DATA / "ETHUSDT-fundingRate-2026-08.csv"
HOURLY_POOL = DATA / "pool_hourly_0x88e6a0_2026-08-01_2026-09-01.parquet"

INDEX: dict = {}
T0 = time.time()


def jdump(obj, path):
    def conv(o):
        if isinstance(o, (np.integer,)):
            return int(o)
        if isinstance(o, (np.floating,)):
            return None if math.isnan(float(o)) else float(o)
        if isinstance(o, (pd.Timestamp,)):
            return str(o)
        if isinstance(o, float) and math.isnan(o):
            return None
        return str(o)
    Path(path).write_text(json.dumps(obj, indent=1, default=conv))


def diag_swap_level(res: dict, cfg: rc.Config, cex: pd.Series, swaps: pd.DataFrame, funding: pd.Series) -> dict:
    df, att, positions, pf = res["df"], res["attribution"], res["positions"], res["pos_frame"]
    p0 = positions[0]
    e0 = p0.entry_eth_price
    a0, a1 = p0.amounts_raw(rc.sqrt_from_eth_price(e0))
    usdc0, eth0 = a0 / 10**rc.DEC0, a1 / 10**rc.DEC1
    trades = df["hedge_trade_eth"]
    nz = trades != 0
    notional = (trades.abs() * df["eth_price"])
    spread_fee = notional * (cfg.half_spread_bps + cfg.taker_fee_bps) / 1e4
    impact = notional * (notional / 1e5) * cfg.impact_coef_bps_per_100k / 1e4
    r = np.diff(np.log(df["eth_price"].to_numpy()))
    var_step = float(np.var(r))
    steps_per_year = rc.SECONDS_PER_YEAR / cfg.grid_seconds
    inr = att["in_range"]
    f = funding[(funding.index >= df.index[0]) & (funding.index <= df.index[-1])] if len(funding) else funding
    d = {
        "window": {"grid_start": str(df.index[0]), "grid_end": str(df.index[-1]), "grid_steps": int(len(df)),
                   "days": res["summary"]["days"], "n_swaps_input": int(len(swaps)), "n_swaps_attributed": int(len(att))},
        "position_t0": {
            "entry_eth_price": e0, "opened_at": str(p0.opened_at),
            "tick_lower": p0.tick_lower, "tick_upper": p0.tick_upper,
            "eth_price_lower": p0.eth_price_lower, "eth_price_upper": p0.eth_price_upper,
            "liquidity_L": p0.liquidity, "usdc_at_entry": usdc0, "eth_at_entry": eth0,
            "eth_value_usd_at_entry": eth0 * e0, "value_usd_at_entry": p0.value_usd(e0),
            "delta_eth_at_entry": float(p0.delta_eth(e0)), "max_delta_eth": p0.max_delta_eth,
            "hedge_notional_usd_at_entry": float(p0.delta_eth(e0)) * e0,
            "delta_fraction_of_max": float(p0.delta_eth(e0)) / p0.max_delta_eth,
            "band_abs_eth": cfg.hedge_band * p0.max_delta_eth,
        },
        "positions": [{"opened_at": str(p.opened_at), "entry": p.entry_eth_price, "lower": p.eth_price_lower,
                       "upper": p.eth_price_upper, "L": p.liquidity, "capital_value_at_open": p.value_usd(p.entry_eth_price)}
                      for p in positions],
        "hedge": {
            "n_trades": int(nz.sum()), "turnover_eth": float(trades.abs().sum()),
            "turnover_usd": float(notional.sum()), "mean_clip_eth": float(trades[nz].abs().mean()) if nz.any() else 0.0,
            "mean_clip_usd": float(notional[nz].mean()) if nz.any() else 0.0, "max_clip_usd": float(notional.max()),
            "first_trade_eth": float(trades.iloc[0]), "first_trade_usd": float(notional.iloc[0]),
            "cost_spread_and_fee_usd": float(spread_fee.sum()), "cost_impact_usd": float(impact.sum()),
            "cost_total_usd": float(df["hedge_cost_usd"].sum()),
            "hedge_pnl_usd": float(df["hedge_pnl_usd"].sum()), "lp_pnl_usd": float(df["lp_pnl_usd"].sum()),
            "max_abs_hedge_eth": float(df["hedge_delta"].abs().max()),
            "max_notional_usd": float((df["hedge_delta"].abs() * df["eth_price"]).max()),
        },
        "attribution": {
            "fee_usd": float(att["fee_usd"].sum()), "lvr_usd": float(att["lvr_usd"].sum()),
            "markout_usd": float(att["markout_usd"].sum()),
            "n_in_range": int(inr.sum()), "pct_swaps_in_range": float(100 * inr.mean()),
            "share_in_range_mean": float(att.loc[inr, "share"].mean()) if inr.any() else 0.0,
            "share_in_range_min": float(att.loc[inr, "share"].min()) if inr.any() else 0.0,
            "share_in_range_max": float(att.loc[inr, "share"].max()) if inr.any() else 0.0,
            "gross_volume_usd_in_range": float((att.loc[inr, "amount0"].abs() / 1e6).sum()),
            "gross_volume_usd_all": float((att["amount0"].abs() / 1e6).sum()),
            "pool_fees_total_usd_in_range": float(((att.loc[inr, "amount0"].clip(lower=0) * cfg.fee_tier / 1e6)
                                                   + (att.loc[inr, "amount1"].clip(lower=0) * cfg.fee_tier / 1e18 * att.loc[inr, "p_cex"])).sum()),
            "n_taker_bought_eth": int((att["amount1"] < 0).sum()),
        },
        "reconciliation": {
            "net_usd_sum": float(df["net_usd"].sum()), "decomp_usd_sum": float(df["decomp_usd"].sum()),
            "discrete_hedge_error_usd": float(df["net_usd"].sum() - df["decomp_usd"].sum()),
            "path_lvr_usd": float(df["lvr_usd"].sum()), "closed_form_lvr_usd": float(rc._closed_form_lvr(df, cex, cfg)),
            "sigma_var_per_step": var_step, "sigma_annualised": math.sqrt(var_step * steps_per_year),
            "grid_seconds": cfg.grid_seconds,
        },
        "funding": {"n_stamps_in_window": int(len(f)), "sum_rate": float(f.sum()) if len(f) else 0.0,
                    "funding_usd": float(df["funding_usd"].sum())},
        "gas": {"gas_usd_sum": float(df["gas_usd"].sum()), "n_repositions": len(pf) - 1,
                "reposition_times": [str(t) for t in pf.index[1:]]},
        "capital": {"capital_usd": cfg.capital_usd, "margin_usd": res["summary"]["margin_usd"],
                    "capital_base_usd": res["summary"]["capital_base_usd"], "leverage": cfg.leverage,
                    "margin_buffer": cfg.margin_buffer},
    }
    return d


def save_run(name: str, overrides: dict, res: dict, diag: dict, note: str = ""):
    out = RAW / name
    out.mkdir(parents=True, exist_ok=True)
    jdump(res["summary"], out / "summary.json")
    with gzip.open(out / "timeseries.csv.gz", "wt") as fh:
        res["df"].to_csv(fh)
    jdump(diag, out / "diag.json")
    s = res["summary"]
    INDEX[name] = {"overrides": overrides, "note": note, "net_usd": s["net_usd"], "fees_usd": s["fees_usd"],
                   "lvr_usd": s["lvr_usd"], "funding_usd": s["funding_usd"], "hedge_cost_usd": s["hedge_cost_usd"],
                   "gas_reposition_usd": s["gas_reposition_usd"], "net_apr_pct": s["net_apr_pct"],
                   "fee_apr_pct": s["fee_apr_pct"], "lvr_apr_pct": s["lvr_apr_pct"],
                   "funding_apr_pct": s["funding_apr_pct"], "hedge_cost_apr_pct": s["hedge_cost_apr_pct"],
                   "n_hedge_trades": s["n_hedge_trades"], "n_repositions": s["n_repositions"],
                   "pct_time_in_range": s["pct_time_in_range"], "mean_liquidity_share_pct": s["mean_liquidity_share_pct"],
                   "fee_over_lvr": s["fee_over_lvr"], "sharpe": s["sharpe"], "max_drawdown_usd": s["max_drawdown_usd"],
                   "capital_base_usd": s["capital_base_usd"], "margin_usd": s["margin_usd"],
                   "discrete_hedge_error_usd": s["discrete_hedge_error_usd"], "closed_form_lvr_usd": s["closed_form_lvr_usd"],
                   "days": s["days"]}
    jdump(INDEX, RAW / "scenarios_index.json")
    print(f"[{time.time()-T0:7.1f}s] {name:28s} net={s['net_usd']:>9} fees={s['fees_usd']:>8} lvr={s['lvr_usd']:>8} "
          f"fund={s['funding_usd']:>7} hedge={s['hedge_cost_usd']:>7} gas={s['gas_reposition_usd']:>6} "
          f"netAPR={s['net_apr_pct']:>7} trades={s['n_hedge_trades']:>5} inr={s['pct_time_in_range']}", flush=True)


def run_sl(name, swaps, cex, funding, note="", **overrides):
    a = bt.Assumptions(**overrides)
    cfg = bt._to_claude_config(a)
    res = rc.run_backtest(swaps, cex, funding, cfg)
    diag = diag_swap_level(res, cfg, cex, swaps, funding)
    save_run(name, overrides, res, diag, note)
    return res


def main():
    t = time.time()
    swaps = bt.load_swaps(str(SWAPS))
    cex = bt.load_cex_prices(str(CEX_1M))
    funding = bt.load_funding_series(str(FUND))
    print(f"loaded swaps={len(swaps)} cex_1s={len(cex)} [{cex.index[0]} .. {cex.index[-1]}] funding={len(funding)} in {time.time()-t:.1f}s", flush=True)
    jdump({"n_swaps": len(swaps), "cex_len": len(cex), "cex_first": str(cex.index[0]), "cex_last": str(cex.index[-1]),
           "cex_first_price": float(cex.iloc[0]), "cex_last_price": float(cex.iloc[-1]),
           "funding_n": len(funding), "funding_first": str(funding.index[0]), "funding_last": str(funding.index[-1]),
           "swaps_first": str(swaps["block_time"].iloc[0]), "swaps_last": str(swaps["block_time"].iloc[-1])},
          RAW / "inputs_loaded.json")

    # 1. default, also through bt.run_swap_level to prove the adapter returns the same summary
    res_def = run_sl("default", swaps, cex, funding, note="all Assumptions defaults, 1m klines, funding file")
    res_adapter = bt.run_swap_level(bt.Assumptions(), swaps, cex, funding)
    same = res_adapter.summary == res_def["summary"]
    jdump({"adapter_summary": res_adapter.summary, "direct_summary": res_def["summary"], "identical": same},
          RAW / "default" / "adapter_vs_direct.json")
    print("adapter == direct:", same, flush=True)

    # 3-9. one-axis sweeps
    for w in (0.02, 0.05, 0.10, 0.15, 0.25, 0.50):
        run_sl(f"range_{w:.2f}", swaps, cex, funding, range_width=w)
    for b in (0.005, 0.01, 0.02, 0.03, 0.05, 0.10):
        run_sl(f"band_{b:.3f}", swaps, cex, funding, hedge_band=b)
    for g in (10, 30, 60, 120, 300):
        run_sl(f"grid_{g}", swaps, cex, funding, grid_seconds=g)
    for ft in (0.0001, 0.0005, 0.003, 0.01):
        run_sl(f"fee_{ft:.4f}", swaps, cex, funding, fee_tier=ft)
    for c in (100_000, 300_000, 1_000_000, 3_000_000, 10_000_000):
        run_sl(f"capital_{c//1000}k", swaps, cex, funding, capital_usd=float(c))
    for lev in (1, 2, 3, 5, 10):
        run_sl(f"leverage_{lev}", swaps, cex, funding, leverage=float(lev))
    run_sl("reposition_off", swaps, cex, funding, reposition=False)
    run_sl("reposition_on", swaps, cex, funding, reposition=True, note="identical to default")

    # 10. weekly sub-windows (clipped exactly as app._clip_swaps / _clip_series would with a window)
    weeks = [("2026-08-01", "2026-08-08"), ("2026-08-08", "2026-08-15"), ("2026-08-15", "2026-08-22"), ("2026-08-22", "2026-08-29")]
    for i, (s0, s1) in enumerate(weeks, 1):
        t0_, t1_ = pd.Timestamp(s0, tz="UTC"), pd.Timestamp(s1, tz="UTC") - pd.Timedelta(seconds=1)
        sw = swaps[(swaps["block_time"] >= t0_) & (swaps["block_time"] <= t1_)].reset_index(drop=True)
        cx = cex.loc[t0_:t1_]
        fd = funding.loc[t0_:t1_]
        run_sl(f"week_{i}", sw, cx, fd, note=f"window [{s0}, {s1}) : {len(sw)} swaps")

    # 2 (markout variants are cheap and useful for the LVR discussion)
    for m in (60, 300):
        run_sl(f"markout_{m}", swaps, cex, funding, markout_seconds=m)

    # aggTrades CEX tape (the app's default pick for `CEX price file`)
    try:
        t = time.time()
        cex_agg = bt.load_cex_prices(str(CEX_AGG))
        print(f"aggTrades loaded: {len(cex_agg)} 1s rows in {time.time()-t:.0f}s", flush=True)
        run_sl("default_aggtrades", swaps, cex_agg, funding, note="CEX tape = aggTrades resampled to 1s (app default file pick)")
        del cex_agg
    except Exception as e:  # noqa: BLE001
        print("aggTrades run failed:", repr(e)[:300], flush=True)
        jdump({"error": repr(e)}, RAW / "default_aggtrades_error.json")

    # funding = none (the app's default `Funding file` pick is "(none — assume 0)")
    run_sl("default_nofunding", swaps, cex, pd.Series(dtype=float), note="Funding file = (none — assume 0), app default pick")

    print(f"ALL DONE in {time.time()-T0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
