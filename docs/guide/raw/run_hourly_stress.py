"""Phase 3 items 2 and 11: the `hourly` engine on the same window, and the
stress shock. Inputs are built exactly as app.py builds them in
`Local CSV files` mode (app.py:1118-1153): the 1s CEX series resampled to
1h OHLC with volume = quote_volume = 0, and the funding CSV as a DataFrame.
The stress shock reproduces app._apply_stress_shock (app.py:1876-1905).

Variants:
  hourly_localcsv            : app path verbatim (zero volume -> zero fees)
  hourly_1hklines_default    : the 1h klines CSV with its real quote_volume, pool_share = 0.001 (default)
  hourly_1hklines_measured   : same, pool_share = the $1M position's measured mean share (from default/diag.json)
  hourly_stress_default      : app path + shock defaults (-20 % linear over last 7 days)
  hourly_stress_30d1         : app path + shock -30 % linear over last 1 day
  hourly_stress_30d1_step    : app path + shock -30 % step over last 1 day
"""
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "/home/user/backtest-lp")
import backtest as bt  # noqa: E402
import run_claude as rc  # noqa: E402

RAW = Path("/home/user/backtest-lp/docs/guide/raw")
DATA = Path("/home/user/backtest-lp/data")
T0 = time.time()
INDEX = {}


def apply_stress_shock(klines: pd.DataFrame, stress: dict) -> pd.DataFrame:
    """Verbatim logic of app._apply_stress_shock (app.py:1876-1905)."""
    if klines.empty:
        return klines
    kl = klines.copy()
    end = kl.index[-1]
    window_start = end - pd.Timedelta(days=int(stress["days"]))
    mask = kl.index >= window_start
    if not mask.any():
        return kl
    n = int(mask.sum())
    if stress["shape"] == "step":
        mult = np.full(n, 1.0 + float(stress["pct"]))
    else:
        frac = np.linspace(0.0, 1.0, n)
        mult = 1.0 + float(stress["pct"]) * frac
    for col in ("open", "high", "low", "close"):
        if col in kl.columns:
            kl.loc[mask, col] = kl.loc[mask, col].to_numpy() * mult
    return kl


def app_local_csv_klines(cex_path: str) -> pd.DataFrame:
    """app.py:1131-1138 verbatim."""
    cex_series = bt.load_cex_prices(cex_path)
    klines = (
        cex_series.resample("1h")
        .agg(["first", "max", "min", "last"])
        .rename(columns={"first": "open", "max": "high", "min": "low", "last": "close"})
        .dropna()
    )
    klines["volume"] = 0.0
    klines["quote_volume"] = 0.0
    return klines


def real_1h_klines(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    idx = rc._epoch_to_datetime(df["open_time"])
    out = df[["open", "high", "low", "close", "volume", "quote_volume"]].copy()
    out.index = idx
    return out.sort_index()


def save(name, res: bt.BacktestResult, note, extra=None):
    out = RAW / name
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(res.summary, indent=1, default=str))
    res.timeseries.to_csv(out / "timeseries.csv")
    d = {"note": note, **(extra or {})}
    (out / "diag.json").write_text(json.dumps(d, indent=1, default=str))
    s = res.summary
    INDEX[name] = {"note": note, **{k: s.get(k) for k in ("days", "net_usd", "fees_usd", "funding_usd", "hedge_cost_usd",
                                                           "net_apr_pct", "fee_apr_pct", "funding_apr_pct", "hedge_cost_apr_pct",
                                                           "n_hedge_trades", "pct_time_in_range", "max_drawdown_usd", "capital_base_usd")}}
    (RAW / "scenarios_hourly_index.json").write_text(json.dumps(INDEX, indent=1, default=str))
    print(f"[{time.time()-T0:6.1f}s] {name:26s} net={s['net_usd']} fees={s['fees_usd']} fund={s['funding_usd']} "
          f"hedge={s['hedge_cost_usd']} netAPR={s['net_apr_pct']} trades={s['n_hedge_trades']} inr={s['pct_time_in_range']} days={s['days']}", flush=True)


def main():
    fund_df = bt.load_funding_df(str(DATA / "ETHUSDT-fundingRate-2026-08.csv"))
    kl_app = app_local_csv_klines(str(DATA / "ETHUSDT-klines-1m-2026-08.csv"))
    print("app-path klines:", len(kl_app), kl_app.index[0], kl_app.index[-1], "quote_volume sum", kl_app["quote_volume"].sum(), flush=True)
    a = bt.Assumptions()

    res = bt.run_hourly(a, kl_app, fund_df)
    save("hourly_localcsv", res, "app Local CSV path: 1s series -> 1h OHLC, volume=quote_volume=0 (app.py:1131-1138)",
         {"bars": len(kl_app), "quote_volume_sum": float(kl_app["quote_volume"].sum()),
          "range_lower": float(kl_app['close'].iloc[0]) * (1 - a.range_width), "range_upper": float(kl_app['close'].iloc[0]) * (1 + a.range_width),
          "first_close": float(kl_app["close"].iloc[0])})

    kl_real = real_1h_klines(str(DATA / "ETHUSDT-klines-1h-2026-08.csv"))
    qv = float(kl_real["quote_volume"].sum())
    # real pool volume in the month, from the swap tape (|amount0| in USDC)
    sw = pd.read_parquet(DATA / "swaps_0x88e6a0_2026-08-01_2026-08-31.parquet", columns=["amount0"])
    pool_vol = float(sw["amount0"].abs().sum() / 1e6)
    ratio = pool_vol / qv
    diag_default = json.load(open(RAW / "default" / "diag.json"))
    measured_share = diag_default["attribution"]["share_in_range_mean"]
    print(f"binance 1h quote_volume sum={qv:,.0f} pool volume={pool_vol:,.0f} ratio={ratio:.5f} measured share={measured_share:.5f}", flush=True)

    res = bt.run_hourly(a, kl_real, fund_df)
    save("hourly_1hklines_default", res, "real 1h klines with quote_volume; pool_volume_24h = quote_volume*0.08*24 (backtest.py:442-446); pool_share default 0.001",
         {"bars": len(kl_real), "binance_quote_volume_usd": qv, "pool_volume_usd_from_swaps": pool_vol,
          "pool_over_binance_volume_ratio": ratio, "pool_volume_multiplier_used": a.pool_volume_multiplier,
          "pool_share_used": a.pool_share, "implied_pool_volume_usd": qv * a.pool_volume_multiplier})

    a2 = bt.Assumptions(pool_share=measured_share)
    res = bt.run_hourly(a2, kl_real, fund_df)
    save("hourly_1hklines_measured", res, "same as hourly_1hklines_default but pool_share = measured mean in-range share of the $1M position",
         {"pool_share_used": measured_share, "pool_volume_multiplier_used": a2.pool_volume_multiplier})

    a3 = bt.Assumptions(pool_share=measured_share, pool_volume_multiplier=ratio)
    res = bt.run_hourly(a3, kl_real, fund_df)
    save("hourly_1hklines_measured_ratio", res, "pool_share = measured share AND pool_volume_multiplier = measured pool/Binance volume ratio",
         {"pool_share_used": measured_share, "pool_volume_multiplier_used": ratio})

    for name, stress in (("hourly_stress_default", {"days": 7, "pct": -0.20, "shape": "linear"}),
                         ("hourly_stress_30d1", {"days": 1, "pct": -0.30, "shape": "linear"}),
                         ("hourly_stress_30d1_step", {"days": 1, "pct": -0.30, "shape": "step"})):
        kl = apply_stress_shock(kl_app, stress)
        res = bt.run_hourly(a, kl, fund_df)
        n_mask = int((kl.index >= kl.index[-1] - pd.Timedelta(days=stress["days"])).sum())
        save(name, res, f"app Local CSV path + stress shock {stress}", {"stress": stress, "bars_shocked": n_mask,
             "last_close_unshocked": float(kl_app["close"].iloc[-1]), "last_close_shocked": float(kl["close"].iloc[-1]),
             "min_close_shocked": float(kl["close"].min())})
    print(f"ALL DONE {time.time()-T0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
