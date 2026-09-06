"""Derivations quoted in GUIDE.md sections 1.3, 3, 4 and 5. Output: guide_derivations.txt."""
import json, math, gzip, pandas as pd
d = json.load(open("/home/user/backtest-lp/docs/guide/raw/default/diag.json")); p = d["position_t0"]
P, Pa, Pb = p["entry_eth_price"], p["eth_price_lower"], p["eth_price_upper"]
sP, sa, sb = math.sqrt(P), math.sqrt(Pa), math.sqrt(Pb)
Lh = p["liquidity_L"] * math.sqrt(1e12) / 1e18
y = Lh * (sP - sa); x = Lh * (1 / sP - 1 / sb)
print(f"sqrtP={sP:.4f} sqrtPa={sa:.4f} sqrtPb={sb:.4f} L_h={Lh:.2f}")
print(f"USDC leg y=L_h*(sqrtP-sqrtPa)={y:,.2f}  ETH leg x=L_h*(1/sqrtP-1/sqrtPb)={x:.4f} ETH = {x*P:,.2f} USD  total {y+x*P:,.2f}")
print(f"sqrtP-sqrtPa={sP-sa:.4f}  sqrtPb-sqrtP={sb-sP:.4f}  USDC share {y/(y+x*P)*100:.2f}%  ETH share {x*P/(y+x*P)*100:.2f}%")
print(f"max delta at lower bound = L_h*(1/sqrtPa-1/sqrtPb) = {Lh*(1/sa-1/sb):.4f} ETH; delta at entry {x:.4f} = {x/(Lh*(1/sa-1/sb))*100:.2f}% of max")
f = pd.read_csv("/home/user/backtest-lp/data/ETHUSDT-fundingRate-2026-08.csv"); r0 = f.iloc[0]
with gzip.open("/home/user/backtest-lp/docs/guide/raw/default/timeseries.csv.gz", "rt") as fh:
    ts = pd.read_csv(fh, index_col=0, parse_dates=True)
row = ts.iloc[0]
print(f"first funding print {pd.to_datetime(r0.calc_time, unit='ms', utc=True)} rate {r0.last_funding_rate}; grid row0 hedge {row.hedge_delta:.4f} ETH price {row.eth_price} notional {abs(row.hedge_delta)*row.eth_price:,.2f} -> cash = -(hedge*p)*rate = {-(row.hedge_delta*row.eth_price)*r0.last_funding_rate:.2f} USD; funding_usd on row0 = {row.funding_usd:.2f}")
print("first 3 funding rows:", f.head(3).to_dict("records"))
fu = ts[ts.funding_usd != 0].funding_usd
print("funding stamps", len(fu), "max", fu.max(), fu.idxmax(), "min", fu.min(), fu.idxmin())
print(f"impact = N^2/1e5*0.8bps; equals spread+fee (5 bps * N) at N = {5/0.8*1e5:,.0f} USD; at mean clip 42,368.55: impact {42368.55**2/1e5*0.8/1e4:.2f} USD vs spread+fee {42368.55*5/1e4:.2f} USD; at max clip 441,856: impact {441856**2/1e5*0.8/1e4:,.2f} vs {441856*5/1e4:,.2f}")
tr = ts[ts.hedge_trade_eth != 0]
print("trades", len(tr), "first trade row", tr.index[0], tr.hedge_trade_eth.iloc[0], "second", tr.index[1], tr.hedge_trade_eth.iloc[1])
print("fees per day mean", ts.fee_usd.sum() / 31, "turnover/day ETH", ts.hedge_trade_eth.abs().sum() / 31)
print("sigma annualised from var/step", math.sqrt(d["reconciliation"]["sigma_var_per_step"] * 525600))
print("grid last price", ts.eth_price.iloc[-1], "lp_value first/last", ts.lp_value_usd.iloc[0], ts.lp_value_usd.iloc[-1], "max short", ts.hedge_delta.min(), ts.hedge_delta.idxmin())
rp = ts.loc["2026-08-20 02:50:00+00:00"]; print("reposition row", rp[["eth_price", "lp_value_usd", "lp_delta", "hedge_delta", "hedge_trade_eth", "hedge_cost_usd", "gas_usd", "lp_pnl_usd"]].to_dict())
prev = ts.loc["2026-08-20 02:49:00+00:00"]; print("row before", prev[["eth_price", "lp_value_usd", "lp_delta", "hedge_delta"]].to_dict())
sd_min = 0.5177 / math.sqrt(525600); sd_day = 0.5177 / math.sqrt(365)
print(f"per-minute sd {sd_min*100:.4f}% -> 3.4753% move = {3.4753/(sd_min*100):.1f} minute-sds; per-day sd {sd_day*100:.2f}% -> {3.4753/(sd_day*100):.2f} daily sds")
print("fees/gamma", 28956 / 37962.54, "hedge cost bps of turnover", 3539.66 / 6355283 * 1e4, "path/closed-form", 37962.54 / 37263.44)
