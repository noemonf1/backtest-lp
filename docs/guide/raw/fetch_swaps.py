"""Swap fetch for the guide. Mirrors backtest.fetch_pool_swaps (backtest.py:708-825)
with two changes, both forced by the live subgraph schema:

  1. `liquidity` is removed from the Swap selection set. The gateway answers
     "Type `Swap` has no field `liquidity`" (docs/guide/raw/schema_probe.json).
  2. `logIndex` is kept as a column so the completeness repair can key on
     (block_number, log_index).

Pagination is byte-for-byte the stock algorithm: timestamp_gt cursor, 1000 rows
per page, so the page-boundary drop the repair step quantifies is the real one.
The month is split into day-slices fetched in parallel threads; slice edges use
timestamp_gte / timestamp_lt so they cannot drop rows.

Every page appends `slice,rows,cursor_ts,wall_seconds` to raw/fetch.log.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import backtest as bt  # noqa: E402

RAW = Path(__file__).resolve().parent
LOG = RAW / "fetch.log"
OUT_RAW = RAW / "swaps_raw_pages.parquet"     # before repair
START, END = "2026-08-01", "2026-09-01"        # [gte, lt)  = 31 full days
POOL = bt.ETH_USDC_POOL
DEC0, DEC1 = 6, 18
N_SLICES = 8

QUERY = """
query($pool: String!, $ts_gte: Int!, $ts_lt: Int!, $last_ts: Int!) {
  swaps(
    first: 1000
    orderBy: timestamp
    orderDirection: asc
    where: {
      pool: $pool,
      timestamp_gte: $ts_gte,
      timestamp_lt: $ts_lt,
      timestamp_gt: $last_ts
    }
  ) {
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

_lock = threading.Lock()
T0 = time.time()


def log(line: str) -> None:
    with _lock:
        with LOG.open("a") as f:
            f.write(line + "\n")


def query_with_retry(variables: dict, tries: int = 8) -> dict:
    delay = 2.0
    for i in range(tries):
        try:
            return bt._query_uniswap_subgraph(QUERY, variables)
        except Exception as e:  # noqa: BLE001
            log(f"RETRY slice={variables['ts_gte']} try={i+1} err={str(e)[:120]}")
            time.sleep(delay)
            delay = min(delay * 2, 60)
    raise RuntimeError("subgraph query failed after retries")


def fetch_slice(idx: int, ts_gte: int, ts_lt: int) -> pd.DataFrame:
    rows: list[dict] = []
    last_ts = ts_gte - 1
    scale0, scale1 = 10 ** DEC0, 10 ** DEC1
    pages = 0
    while True:
        data = query_with_retry(
            {"pool": POOL.lower(), "ts_gte": ts_gte, "ts_lt": ts_lt, "last_ts": last_ts}
        )
        batch = data.get("swaps") or []
        if not batch:
            break
        for s in batch:
            rows.append(
                {
                    "block_number": int(s["transaction"]["blockNumber"]),
                    "block_time": int(s["timestamp"]),
                    "log_index": int(s["logIndex"]),
                    "amount0": int(round(float(s["amount0"]) * scale0)),
                    "amount1": int(round(float(s["amount1"]) * scale1)),
                    "sqrt_price_x96": int(s["sqrtPriceX96"]),
                    "tick": int(s["tick"]),
                }
            )
        new_last_ts = int(batch[-1]["timestamp"])
        # stock cursor rule (backtest.py:790-799)
        last_ts = new_last_ts + 1 if new_last_ts == last_ts else new_last_ts
        pages += 1
        full = len(batch) == 1000
        log(f"slice={idx},rows={len(rows)},cursor_ts={last_ts},wall_seconds={time.time()-T0:.1f},page_len={len(batch)},boundary={int(full)}")
        if not full:
            break
        if last_ts >= ts_lt:
            break
    df = pd.DataFrame(rows)
    df.to_parquet(RAW / f"swaps_slice_{idx}.parquet", index=False)
    log(f"DONE slice={idx} rows={len(df)} pages={pages} wall_seconds={time.time()-T0:.1f}")
    return df


def main() -> None:
    if LOG.exists():
        LOG.unlink()
    start_ts = int(pd.Timestamp(START, tz="UTC").timestamp())
    end_ts = int(pd.Timestamp(END, tz="UTC").timestamp())
    edges = [start_ts + (end_ts - start_ts) * i // N_SLICES for i in range(N_SLICES + 1)]
    log(f"START window=[{START},{END}) start_ts={start_ts} end_ts={end_ts} slices={edges}")
    with ThreadPoolExecutor(max_workers=N_SLICES) as ex:
        futs = [ex.submit(fetch_slice, i, edges[i], edges[i + 1]) for i in range(N_SLICES)]
        parts = [f.result() for f in futs]
    df = pd.concat(parts, ignore_index=True)
    df["block_time"] = pd.to_datetime(df["block_time"], unit="s", utc=True)
    df = df.sort_values(["block_time", "block_number", "log_index"]).reset_index(drop=True)
    df.to_parquet(OUT_RAW, index=False)
    log(f"ALL rows={len(df)} wall_seconds={time.time()-T0:.1f}")
    json.dump(
        {"rows": int(len(df)), "first": str(df["block_time"].iloc[0]), "last": str(df["block_time"].iloc[-1]),
         "wall_seconds": round(time.time() - T0, 1), "slices": edges},
        (RAW / "fetch_result.json").open("w"), indent=1,
    )


if __name__ == "__main__":
    main()
