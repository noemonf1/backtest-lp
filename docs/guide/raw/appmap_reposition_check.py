"""Check whether run_claude.run_backtest charges reposition cost twice:
once via the capital write-down in build_position_schedule (run_claude.py:569-572,
which lowers lp_value_usd and therefore lp_pnl_usd/net_usd) and again via the
gas_usd column (run_claude.py:690-695, subtracted in net_usd at line 706)."""
import numpy as np, pandas as pd, backtest as bt, run_claude as rc
swaps, cex, funding = bt.make_demo_tape(days=10, s0=3000.0, vol_annual=0.65, seed=7)
a = bt.Assumptions(range_width=0.02, reposition=True, reposition_buffer_hours=1.0,
                   gas_usd_per_reposition=40.0, lp_rebalance_swap_bps=5.0)
cfg = bt._to_claude_config(a)
res = rc.run_backtest(swaps, cex, funding, cfg)
df, pf, positions = res["df"], res["pos_frame"], res["positions"]
print("n_repositions:", res["summary"]["n_repositions"], "gas_reposition_usd:", res["summary"]["gas_reposition_usd"])
tot_writedown = 0.0
for k, ts in enumerate(pf.index[1:], start=1):
    old, new = positions[k-1], positions[k]
    p = float(df.loc[ts, "eth_price"])
    v_old, v_new = old.value_usd(p), new.value_usd(p)
    writedown = v_old - v_new            # loss embedded in lp_value_usd at the reposition step
    gas_col = float(df.loc[ts, "gas_usd"])  # explicit charge in the gas_usd column
    tot_writedown += writedown
    if k <= 5:
        print(f"repo {k} @ {ts}: price={p:.2f} old_value={v_old:,.2f} new_value={v_new:,.2f} "
              f"writedown={writedown:,.2f} gas_usd_col={gas_col:,.2f}")
print(f"sum writedown over all repositions = {tot_writedown:,.2f}; sum gas_usd column = {df['gas_usd'].sum():,.2f}")
print("Both are subtracted from net_usd: writedown via lp_pnl_usd (line 699-701), gas_usd via line 706.")
