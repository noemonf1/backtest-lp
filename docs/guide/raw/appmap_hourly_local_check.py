"""Check two hourly-engine behaviours in Local-CSV mode without any network:
1. app._load_hourly_inputs sets quote_volume=0 (app.py:1139) so fees are always 0.
2. An empty funding frame makes kimi._get_funding_rate return 0.0001 per 8h, not 0."""
import pandas as pd, numpy as np, backtest as bt
from kimi.uniswap_delta_hedge_backtest import DeltaHedgeBacktest
# synthetic 1h klines exactly as app.py:1131-1139 builds them from a 1s CEX series
idx = pd.date_range("2024-01-01", periods=24*20, freq="1h", tz="UTC")
close = 3000 * np.exp(np.cumsum(np.random.default_rng(0).normal(0, 0.01, len(idx))))
kl = pd.DataFrame({"open": close, "high": close, "low": close, "close": close}, index=idx)
kl["volume"] = 0.0; kl["quote_volume"] = 0.0          # app.py:1138-1139
a = bt.Assumptions(pool_share=0.01, pool_volume_multiplier=0.5)
res0 = bt.run_hourly(a, kl, pd.DataFrame({"fundingRate": []}))   # app.py:1143 empty funding
print("Local-CSV-shaped klines, empty funding:")
print("  fees_usd =", res0.summary["fees_usd"], "(pool_share=0.01, multiplier=0.5)")
print("  funding_usd =", res0.summary["funding_usd"], " funding_apr_pct =", res0.summary["funding_apr_pct"])
# same but with an explicit zero funding series
fz = pd.DataFrame({"fundingRate": np.zeros(60)}, index=pd.date_range("2024-01-01", periods=60, freq="8h", tz="UTC"))
res1 = bt.run_hourly(a, kl, fz)
print("  with explicit zero funding: funding_usd =", res1.summary["funding_usd"])
# and with real quote volume, to show fees become non-zero
kl2 = kl.copy(); kl2["quote_volume"] = 5e7
res2 = bt.run_hourly(a, kl2, fz)
print("  with quote_volume=5e7 per bar: fees_usd =", res2.summary["fees_usd"])
# bar-interval scaling: 4h bars with the same per-bar quote volume
idx4 = pd.date_range("2024-01-01", periods=6*20, freq="4h", tz="UTC")
kl4 = pd.DataFrame({"open": close[::4], "high": close[::4], "low": close[::4], "close": close[::4]}, index=idx4)
kl4["volume"] = 0.0; kl4["quote_volume"] = 5e7 * 4     # 4h bar carries 4x the 1h quote volume
res4 = bt.run_hourly(a, kl4, fz)
print("  4h bars, per-bar quote_volume = 4x: fees_usd =", res4.summary["fees_usd"], " (1h gave", res2.summary["fees_usd"], ")")
print("  ratio 4h/1h fees =", round(res4.summary["fees_usd"] / max(res2.summary["fees_usd"], 1), 2), " (same total volume, so a correct model gives ~1.0)")
