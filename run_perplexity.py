#!/usr/bin/env python3
import math
import os
import time

import numpy as np
import pandas as pd
import requests

# =========================
# CONFIG
# =========================
START = "2024-01-01"
END = "2024-03-01"
BAR = "1h"

POOL_ADDRESS = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640".lower()

INITIAL_CAPITAL_USD = 10_000.0

# Hypothetical position
RANGE_MODE = "centered_pct"  # centered_pct | fixed_ticks
CENTERED_LOWER_PCT = 0.95
CENTERED_UPPER_PCT = 1.05
TICK_LOWER = -240000
TICK_UPPER = 240000

# Hedge
HEDGE_FRACTION = 1.0
REBALANCE_DELTA_THRESHOLD_ETH = 0.05
BINANCE_TAKER_FEE = 0.0004
BINANCE_MAKER_FEE = 0.0002
USE_TAKER = True

# Fee accounting / simulation
ACTIVE_LIQUIDITY_SHARE = 0.00005
REBALANCE_ON_OUT_OF_RANGE = True

UNISWAP_SUBGRAPH_URL = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUTURES = "https://fapi.binance.com"

OUT_DIR = "output"
os.makedirs(OUT_DIR, exist_ok=True)

Q128 = 2**128


# =========================
# DATA FETCH
# =========================
def fetch_binance_klines(symbol, interval, start_str, end_str):
    start_ts = int(pd.Timestamp(start_str, tz="UTC").timestamp() * 1000)
    end_ts = int(pd.Timestamp(end_str, tz="UTC").timestamp() * 1000)
    rows = []
    while start_ts < end_ts:
        params = {
            "symbol": symbol,
            "interval": interval,
            "startTime": start_ts,
            "endTime": end_ts,
            "limit": 1000,
        }
        r = requests.get(f"{BINANCE_SPOT}/api/v3/klines", params=params, timeout=30)
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows.extend(data)
        start_ts = int(data[-1][0]) + 1
        if len(data) < 1000:
            break
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        rows,
        columns=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trades",
            "taker_base_vol",
            "taker_quote_vol",
            "ignore",
        ],
    )
    df["ts"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    for c in [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "taker_base_vol",
        "taker_quote_vol",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[["ts", "open", "high", "low", "close", "volume", "quote_volume"]]


def fetch_binance_funding(symbol, start_str, end_str):
    start_ts = int(pd.Timestamp(start_str, tz="UTC").timestamp() * 1000)
    end_ts = int(pd.Timestamp(end_str, tz="UTC").timestamp() * 1000)
    rows = []
    while start_ts < end_ts:
        params = {
            "symbol": symbol,
            "startTime": start_ts,
            "endTime": end_ts,
            "limit": 1000,
        }
        r = requests.get(
            f"{BINANCE_FUTURES}/fapi/v1/fundingRate", params=params, timeout=30
        )
        r.raise_for_status()
        data = r.json()
        if not data:
            break
        rows.extend(data)
        start_ts = int(data[-1]["fundingTime"]) + 1
        if len(data) < 1000:
            break
        time.sleep(0.12)
    if not rows:
        return pd.DataFrame(columns=["ts", "fundingRate"])
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["ts", "fundingRate"]].sort_values("ts")


def gql(query, variables=None):
    r = requests.post(
        UNISWAP_SUBGRAPH_URL,
        json={"query": query, "variables": variables or {}},
        timeout=45,
    )
    r.raise_for_status()
    out = r.json()
    if "errors" in out:
        raise RuntimeError(out["errors"])
    return out["data"]


def fetch_pool_meta(pool_address):
    q = """
    query($id: ID!) {
      pool(id: $id) {
        id
        feeTier
        tick
        liquidity
        sqrtPrice
        token0 { id symbol decimals }
        token1 { id symbol decimals }
      }
    }
    """
    d = gql(q, {"id": pool_address})
    if not d["pool"]:
        raise RuntimeError("Pool not found.")
    return d["pool"]


def fetch_pool_hour_data(pool_address, start_ts, end_ts):
    q = """
    query($id: String!, $start: Int!, $end: Int!) {
      poolHourDatas(
        first: 1000
        orderBy: periodStartUnix
        orderDirection: asc
        where: { pool: $id, periodStartUnix_gte: $start, periodStartUnix_lte: $end }
      ) {
        periodStartUnix
        liquidity
        sqrtPrice
        tick
        volumeUSD
        feesUSD
        tvlUSD
      }
    }
    """
    d = gql(q, {"id": pool_address, "start": int(start_ts), "end": int(end_ts)})
    df = pd.DataFrame(d["poolHourDatas"])
    if df.empty:
        return df
    df["ts"] = pd.to_datetime(df["periodStartUnix"].astype(int), unit="s", utc=True)
    for c in ["liquidity", "sqrtPrice", "tick", "volumeUSD", "feesUSD", "tvlUSD"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("ts")


def fetch_ticks(pool_address):
    all_ticks = []
    skip = 0
    while True:
        q = """
        query($pool: String!, $skip: Int!) {
          ticks(
            first: 1000
            skip: $skip
            orderBy: tickIdx
            orderDirection: asc
            where: { poolAddress: $pool }
          ) {
            tickIdx
            liquidityGross
            liquidityNet
            feeGrowthOutside0X128
            feeGrowthOutside1X128
          }
        }
        """
        d = gql(q, {"pool": pool_address, "skip": skip})
        batch = d["ticks"]
        if not batch:
            break
        all_ticks.extend(batch)
        skip += len(batch)
        if len(batch) < 1000:
            break
    df = pd.DataFrame(all_ticks)
    if df.empty:
        return df
    for c in [
        "tickIdx",
        "liquidityGross",
        "liquidityNet",
        "feeGrowthOutside0X128",
        "feeGrowthOutside1X128",
    ]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# =========================
# UNISWAP V3 MATH
# =========================
def tick_to_price(tick):
    return 1.0001**tick


def amounts_from_liquidity(L, P, Pa, Pb):
    sa = math.sqrt(Pa)
    sb = math.sqrt(Pb)
    sp = math.sqrt(P)
    if P <= Pa:
        return L * (sb - sa) / (sa * sb), 0.0
    elif P >= Pb:
        return 0.0, L * (sb - sa)
    else:
        amt0 = L * (sb - sp) / (sp * sb)
        amt1 = L * (sp - sa)
        return amt0, amt1


def liquidity_from_value(value_usd, P, Pa, Pb):
    sa = math.sqrt(Pa)
    sb = math.sqrt(Pb)
    sp = math.sqrt(P)
    if P <= Pa:
        amt0 = value_usd / P
        return amt0 * sa * sb / (sb - sa)
    elif P >= Pb:
        return value_usd / (sb - sa)
    else:
        L0 = (value_usd / P) * sp * sb / (sb - sp)
        L1 = value_usd / (sp - sa)
        return min(L0, L1)


def lp_delta_eth(L, P, Pa, Pb):
    amt0, amt1 = amounts_from_liquidity(L, P, Pa, Pb)
    return amt0 + amt1 / P


def in_range_tick(tick_current, tick_lower, tick_upper):
    return tick_current >= tick_lower and tick_current < tick_upper


def fee_growth_inside_from_ticks(
    tick_current, tick_lower, tick_upper, fg0_global, fg1_global, ticks_df
):
    lower = ticks_df.loc[ticks_df["tickIdx"] == tick_lower]
    upper = ticks_df.loc[ticks_df["tickIdx"] == tick_upper]
    if lower.empty or upper.empty:
        return None, None
    lower = lower.iloc[0]
    upper = upper.iloc[0]

    if tick_current >= tick_lower:
        fg0_below = float(lower["feeGrowthOutside0X128"])
        fg1_below = float(lower["feeGrowthOutside1X128"])
    else:
        fg0_below = fg0_global - float(lower["feeGrowthOutside0X128"])
        fg1_below = fg1_global - float(lower["feeGrowthOutside1X128"])

    if tick_current < tick_upper:
        fg0_above = float(upper["feeGrowthOutside0X128"])
        fg1_above = float(upper["feeGrowthOutside1X128"])
    else:
        fg0_above = fg0_global - float(upper["feeGrowthOutside0X128"])
        fg1_above = fg1_global - float(upper["feeGrowthOutside1X128"])

    inside0 = fg0_global - fg0_below - fg0_above
    inside1 = fg1_global - fg1_below - fg1_above
    return inside0, inside1


def estimate_fee_income(row, L, in_range, active_share):
    if (
        not in_range
        or pd.isna(row["feesUSD"])
        or pd.isna(row["liquidity"])
        or row["liquidity"] <= 0
    ):
        return 0.0
    share = max(min(L / float(row["liquidity"]), 1.0), active_share)
    return float(row["feesUSD"]) * share


# =========================
# POSITION ROLLING
# =========================
def new_range_from_price(price):
    if RANGE_MODE == "centered_pct":
        return price * CENTERED_LOWER_PCT, price * CENTERED_UPPER_PCT
    return tick_to_price(TICK_LOWER), tick_to_price(TICK_UPPER)


# =========================
# BACKTEST
# =========================
def build_data():
    spot = fetch_binance_klines("ETHUSDT", BAR, START, END)
    funding = fetch_binance_funding("ETHUSDT", START, END)
    pool = fetch_pool_meta(POOL_ADDRESS)
    pool_hours = fetch_pool_hour_data(
        POOL_ADDRESS,
        int(pd.Timestamp(START, tz="UTC").timestamp()),
        int(pd.Timestamp(END, tz="UTC").timestamp()),
    )
    ticks_df = fetch_ticks(POOL_ADDRESS)

    if spot.empty or pool_hours.empty or ticks_df.empty:
        raise RuntimeError("Missing required data.")

    pool_hours["price"] = pool_hours["sqrtPrice"].astype(float) ** 2

    df = pd.merge_asof(
        spot.sort_values("ts"),
        pool_hours.sort_values("ts"),
        on="ts",
        direction="backward",
    )
    df = pd.merge_asof(
        df.sort_values("ts"),
        funding.sort_values("ts"),
        on="ts",
        direction="backward",
        tolerance=pd.Timedelta(BAR),
    )
    df["fundingRate"] = df["fundingRate"].fillna(0.0)
    df = df.dropna(subset=["price", "liquidity", "feesUSD"]).copy()
    return df, pool, ticks_df


def backtest():
    df, pool, ticks_df = build_data()
    fee_tier = float(pool["feeTier"]) / 1e6

    entry_price = float(df["price"].iloc[0])
    Pa, Pb = new_range_from_price(entry_price)
    L = liquidity_from_value(INITIAL_CAPITAL_USD, entry_price, Pa, Pb)

    delta0 = lp_delta_eth(L, entry_price, Pa, Pb)
    hedge_eth = -HEDGE_FRACTION * delta0
    cash = INITIAL_CAPITAL_USD
    if abs(hedge_eth) > 0:
        cash -= (
            abs(hedge_eth)
            * entry_price
            * (BINANCE_TAKER_FEE if USE_TAKER else BINANCE_MAKER_FEE)
        )

    fg0_last = None
    fg1_last = None
    rows = []
    prev_price = entry_price

    for _, row in df.iterrows():
        P = float(row["price"])
        tick_current = int(row["tick"]) if not pd.isna(row["tick"]) else 0
        active = in_range_tick(
            tick_current,
            TICK_LOWER if RANGE_MODE == "fixed_ticks" else int(math.log(Pa, 1.0001)),
            TICK_UPPER if RANGE_MODE == "fixed_ticks" else int(math.log(Pb, 1.0001)),
        )

        amt0, amt1 = amounts_from_liquidity(L, P, Pa, Pb)
        lp_value = amt0 * P + amt1
        delta = lp_delta_eth(L, P, Pa, Pb)

        # Fee growth accounting path placeholder:
        # If you later add historical feeGrowthGlobal snapshots, plug them in here.
        fee_income = estimate_fee_income(row, L, active, ACTIVE_LIQUIDITY_SHARE)

        hedge_pnl = hedge_eth * (P - prev_price)
        funding_payment = -hedge_eth * P * float(row["fundingRate"])
        cash += fee_income + hedge_pnl + funding_payment

        # If price exits range, optionally roll the position to a new centered range.
        if REBALANCE_ON_OUT_OF_RANGE and not active:
            realized_lp_value = lp_value
            cash += realized_lp_value
            entry_price = P
            Pa, Pb = new_range_from_price(entry_price)
            L = liquidity_from_value(cash, entry_price, Pa, Pb)
            delta = lp_delta_eth(L, P, Pa, Pb)
            hedge_target = -HEDGE_FRACTION * delta
            trade_qty = hedge_target - hedge_eth
            cash -= (
                abs(trade_qty)
                * P
                * (BINANCE_TAKER_FEE if USE_TAKER else BINANCE_MAKER_FEE)
            )
            hedge_eth = hedge_target
        else:
            hedge_target = -HEDGE_FRACTION * delta
            if abs(hedge_target - hedge_eth) >= REBALANCE_DELTA_THRESHOLD_ETH:
                trade_qty = hedge_target - hedge_eth
                cash -= (
                    abs(trade_qty)
                    * P
                    * (BINANCE_TAKER_FEE if USE_TAKER else BINANCE_MAKER_FEE)
                )
                hedge_eth = hedge_target

        total_equity = cash + lp_value + hedge_eth * P

        rows.append(
            {
                "ts": row["ts"],
                "price": P,
                "tick": tick_current,
                "in_range": active,
                "range_lower_price": Pa,
                "range_upper_price": Pb,
                "pool_liquidity": float(row["liquidity"]),
                "pool_volumeUSD": float(row["volumeUSD"]),
                "pool_feesUSD": float(row["feesUSD"]),
                "lp_liquidity": L,
                "lp_value": lp_value,
                "lp_delta_eth": delta,
                "hedge_eth": hedge_eth,
                "fee_income": fee_income,
                "hedge_pnl": hedge_pnl,
                "funding_payment": funding_payment,
                "cash": cash,
                "total_equity": total_equity,
                "strategy_pnl": total_equity - INITIAL_CAPITAL_USD,
                "unhedged_pnl": lp_value - INITIAL_CAPITAL_USD,
            }
        )

        prev_price = P

    out = pd.DataFrame(rows)
    out["cum_fee_income"] = out["fee_income"].cumsum()
    out["cum_hedge_pnl"] = out["hedge_pnl"].cumsum()
    out["cum_funding"] = out["funding_payment"].cumsum()

    out_path = os.path.join(
        OUT_DIR, "uniswap_v3_more_complete_hypothetical_backtest.csv"
    )
    out.to_csv(out_path, index=False)

    print("Backtest complete")
    print(
        f"Pool: {pool['token0']['symbol']}/{pool['token1']['symbol']} feeTier={fee_tier}"
    )
    print(out[["ts", "price", "total_equity", "strategy_pnl", "unhedged_pnl"]].tail())
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    backtest()
