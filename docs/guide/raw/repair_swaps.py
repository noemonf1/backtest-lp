"""Completeness repair and liquidity fill (Phase 1 items 3 and 4).

Input : raw/swaps_raw_pages.parquet (stock pagination, no `liquidity` field)
        raw/fetch.log (one line per page; boundary=1 marks a full 1000-row page)
Output: data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet  (the file every run uses)
        raw/repair_result.json, raw/repair_added_rows.csv

Repair: for every page boundary the stock cursor rule (backtest.py:790-799)
advances with `timestamp_gt: <last timestamp of page>`, so any swap sharing that
second and not inside the 1000-row page is skipped. For each boundary we query
`swaps(first: 1000, where: {pool, timestamp: <second>})` and add rows whose
(block_number, log_index) key is absent. Because the stock rule can also bump
the cursor by one second when a whole page shares a timestamp, both
`cursor_ts` and `cursor_ts - 1` are probed.

Liquidity: `Swap.liquidity` does not exist on the live subgraph, so each swap's
`liquidity` is the `poolHourDatas.liquidity` of the hour containing it.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import backtest as bt  # noqa: E402

RAW = Path(__file__).resolve().parent
REPO = RAW.parents[2]
DATA = REPO / "data"
POOL = bt.ETH_USDC_POOL
FINAL = DATA / "swaps_0x88e6a0_2026-08-01_2026-08-31.parquet"

Q_SECOND = """
query($pool: String!, $ts: Int!) {
  swaps(first: 1000, orderBy: logIndex, orderDirection: asc,
        where: {pool: $pool, timestamp: $ts}) {
    transaction { blockNumber }
    timestamp
    logIndex
    amount0
    amount1
    sqrtPriceX96
    tick
  }
}
"""


def rows_from_batch(batch):
    out = []
    for s in batch:
        out.append(
            {
                "block_number": int(s["transaction"]["blockNumber"]),
                "block_time": int(s["timestamp"]),
                "log_index": int(s["logIndex"]),
                "amount0": float(s["amount0"]) * 10**6,
                "amount1": float(s["amount1"]) * 10**18,
                "sqrt_price_x96": float(int(s["sqrtPriceX96"])),
                "sqrt_price_x96_str": s["sqrtPriceX96"],
                "tick": int(s["tick"]),
            }
        )
    return out


def main():
    t0 = time.time()
    raw = pd.read_parquet(RAW / "swaps_raw_pages.parquet")
    n_raw = len(raw)
    raw_ts = (raw["block_time"].astype("int64") // 10**9).astype(int)
    have = set(zip(raw["block_number"].astype(int), raw["log_index"].astype(int)))

    boundaries = []
    for line in (RAW / "fetch.log").read_text().splitlines():
        m = re.match(r"slice=(\d+),rows=(\d+),cursor_ts=(\d+),wall_seconds=([\d.]+),page_len=(\d+),boundary=1", line)
        if m:
            boundaries.append(int(m.group(3)))
    boundaries = sorted(set(boundaries))
    probe_seconds = sorted(set(boundaries) | {b - 1 for b in boundaries})

    added, per_boundary = [], {}
    cache = RAW / "repair_probe_cache.json"
    if cache.exists():
        c = json.load(cache.open())
        added = c["added"]; per_boundary = {int(k): v for k, v in c["per_boundary"].items()}
        for r in added:
            have.add((r["block_number"], r["log_index"]))
        probe_seconds = []
    for sec in probe_seconds:
        for attempt in range(8):
            try:
                data = bt._query_uniswap_subgraph(Q_SECOND, {"pool": POOL.lower(), "ts": sec})
                break
            except Exception as e:  # noqa: BLE001  transient gateway 5xx
                print("retry", sec, attempt + 1, str(e)[:80])
                time.sleep(min(2 * 2**attempt, 60))
        else:
            raise RuntimeError(f"gateway failed 8 times at second {sec}")
        batch = data.get("swaps") or []
        n_at_sec = len(batch)
        new = [r for r in rows_from_batch(batch) if (r["block_number"], r["log_index"]) not in have]
        for r in new:
            have.add((r["block_number"], r["log_index"]))
        added.extend(new)
        n_have_at_sec = int((raw_ts == sec).sum())
        per_boundary[sec] = {"is_boundary": sec in boundaries, "subgraph_rows_at_second": n_at_sec,
                             "rows_already_held": n_have_at_sec, "rows_added": len(new)}

    if not cache.exists():
        json.dump({"added": added, "per_boundary": per_boundary}, cache.open("w"))
    add_df = pd.DataFrame(added, columns=list(raw.columns.drop("block_time")) + ["block_time"]) if added else pd.DataFrame(columns=raw.columns)
    if len(add_df):
        add_df["block_time"] = pd.to_datetime(add_df["block_time"], unit="s", utc=True)
        add_df = add_df[raw.columns]
        add_df.to_csv(RAW / "repair_added_rows.csv", index=False)
    full = pd.concat([raw, add_df], ignore_index=True) if len(add_df) else raw.copy()
    full = full.sort_values(["block_time", "block_number", "log_index"]).reset_index(drop=True)
    assert not full.duplicated(subset=["block_number", "log_index"]).any()

    # What the stock de-dup key would have discarded on this real data (backtest.py:813-815)
    n_stock_dedup_drop = int(full.duplicated(subset=["block_number", "block_time", "sqrt_price_x96"], keep="first").sum())

    # Liquidity fill from poolHourDatas (full 31-day window; the fetch is [gte start, lte end])
    hp = bt.ensure_pool_hourly("2026-08-01", "2026-09-01", POOL, out_dir=DATA)
    hourly = pd.read_parquet(hp["path"])
    full["block_time"] = pd.to_datetime(full["block_time"], utc=True)
    hour_key = full["block_time"].dt.floor("h")
    hk = pd.DatetimeIndex(hour_key)
    liq = hourly["liquidity"].reindex(hk)
    n_missing_hours = int(liq.isna().sum())
    if n_missing_hours:
        # hours with no subgraph row (no swap in that hour): carry the previous hour's liquidity
        liq = hourly["liquidity"].reindex(hourly.index.union(hour_key.unique())).sort_index().ffill().reindex(hk)
    full["liquidity"] = liq.to_numpy()
    assert full["liquidity"].notna().all()

    cols = ["block_number", "block_time", "amount0", "amount1", "sqrt_price_x96", "liquidity", "tick",
            "log_index", "sqrt_price_x96_str"]
    full = full[cols]
    tmp = FINAL.with_suffix(".parquet.tmp")
    full.to_parquet(tmp, index=False)
    tmp.replace(FINAL)

    res = {
        "raw_rows": n_raw,
        "page_boundaries": len(boundaries),
        "seconds_probed": len(probe_seconds),
        "rows_added": len(add_df),
        "fraction_added": (len(add_df) / n_raw) if n_raw else None,
        "final_rows": int(len(full)),
        "first": str(full["block_time"].iloc[0]),
        "last": str(full["block_time"].iloc[-1]),
        "stock_dedup_key_would_drop": n_stock_dedup_drop,
        "hourly_file": hp["path"],
        "hourly_rows": int(len(hourly)),
        "hourly_first": str(hourly.index[0]),
        "hourly_last": str(hourly.index[-1]),
        "swap_hours_missing_in_hourly": n_missing_hours,
        "final_path": str(FINAL),
        "wall_seconds": round(time.time() - t0, 1),
    }
    json.dump({"summary": res, "per_second": per_boundary}, (RAW / "repair_result.json").open("w"), indent=1)
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
