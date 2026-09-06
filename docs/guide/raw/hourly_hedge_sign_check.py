"""Verifies the sign of the hourly engine's realised hedge P&L on the real August 2026 tape.
Output recorded in hourly_hedge_sign_check.txt."""
import sys; sys.path.insert(0, "/home/user/backtest-lp"); sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
import numpy as np
import backtest as bt
from run_hourly_stress import app_local_csv_klines
from kimi.uniswap_delta_hedge_backtest import DeltaHedgeBacktest, BacktestConfig
kl = app_local_csv_klines("/home/user/backtest-lp/data/ETHUSDT-klines-1m-2026-08.csv")
fund = bt.load_funding_df("/home/user/backtest-lp/data/ETHUSDT-fundingRate-2026-08.csv")
a = bt.Assumptions(); cfg = bt._to_kimi_config(a); p0 = float(kl["close"].iloc[0])
cfg = BacktestConfig(**{**cfg.__dict__, "lower_price": p0 * (1 - a.range_width), "upper_price": p0 * (1 + a.range_width)})
eng = DeltaHedgeBacktest(cfg); r = eng.run(kl, fund); s = eng.summarize(r)
p = r["price"].to_numpy(); h = r["hedge_eth"].to_numpy()
correct = float(np.sum(h[:-1] * np.diff(p))); engine = float(r["hedge_realized_pnl"].iloc[-1] + r["hedge_unrealized_pnl"].iloc[-1])
print("bars", len(r), "price first/last", p[0], p[-1])
print("lp_value t0", r["lp_value"].iloc[0], "initial capital", cfg.initial_capital_usd, "unused at t0", cfg.initial_capital_usd - r["lp_value"].iloc[0])
print("lp_pnl end (lp_value - capital)", r["lp_pnl"].iloc[-1])
print("engine hedge pnl (realized+unrealized at end)", engine)
print("correct hedge pnl  sum(h[i-1]*(p[i]-p[i-1]))     ", correct)
print("engine total_pnl end", r["total_pnl"].iloc[-1])
print("total with correct hedge pnl", r["lp_pnl"].iloc[-1] + correct + r["cum_fees"].iloc[-1] + r["cum_funding"].iloc[-1] - r["cum_binance_fees"].iloc[-1] - r["cum_slippage"].iloc[-1])
print("summary", s)
for i, (ts, row) in enumerate(r.iterrows()):
    if i > 0 and row["rebalanced"] and abs(row["hedge_eth"]) < abs(r["hedge_eth"].iloc[i - 1]):
        print("first reducing trade at", ts, "hedge before", r["hedge_eth"].iloc[i - 1], "after", row["hedge_eth"], "price", row["price"], "avg before", r["hedge_avg_price"].iloc[i - 1], "realized jump", row["hedge_realized_pnl"] - r["hedge_realized_pnl"].iloc[i - 1])
        break
print("timeseries binance_fees column sum", r["binance_fees"].sum(), "vs cum_binance_fees end", r["cum_binance_fees"].iloc[-1])
