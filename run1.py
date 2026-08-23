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

# Canonical Ethereum mainnet WETH/USDC 0.05% pool
POOL_ADDRESS = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640".lower()

# Hypothetical LP position
INITIAL_CAPITAL_USD = 10_000.0
TICK_LOWER = -240000
TICK_UPPER = 240000

# You can also override with a narrower range, e.g. +/- 5% around entry after bootstrapping
RANGE_MODE = "fixed_ticks"  # fixed_ticks | centered_pct
CENTERED_LOWER_PCT = 0.95
CENTERED_UPPER_PCT = 1.05

# Hedge
HEDGE_FRACTION = 1.0
REBALANCE_DELTA_THRESHOLD_ETH = 0.05
BINANCE_TAKER_FEE = 0.0004
BINANCE_MAKER_FEE = 0.0002
USE_TAKER = True

# If you do not have exact active-liquidity share, this models your share of pool fees.
# Example: 0.00005 = 5 bps of active liquidity.
ACTIVE_LIQUIDITY_SHARE = 0.00005

# Protocol/source endpoints
UNISWAP_SUBGRAPH_URL = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"
BINANCE_SPOT = "https://api.binance.com"
BINANCE_FUTURES = "https://fapi.binance.com"

OUT_DIR = "output"
os.makedirs(OUT_DIR, exist_ok=True)


# =========================
# BINANCE DATA
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
        last_open = int(data[-1][0])
        start_ts = last_open + 1
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
        last_time = int(data[-1]["fundingTime"])
        start_ts = last_time + 1
        if len(data) < 1000:
            break
        time.sleep(0.12)

    if not rows:
        return pd.DataFrame(columns=["ts", "fundingRate"])

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["fundingTime"], unit="ms", utc=True)
    df["fundingRate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    return df[["ts", "fundingRate"]].sort_values("ts")


# =========================
# UNISWAP SUBGRAPH
# =========================
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
        raise RuntimeError("Pool not found in subgraph.")
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
        amt0 = L * (sb - sa) / (sa * sb)
        amt1 = 0.0
    elif P >= Pb:
        amt0 = 0.0
        amt1 = L * (sb - sa)
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


def estimate_fee_income(row, L, pool_liquidity, fee_tier, active_liq_share):
    # First-order approximation using actual pool fees and your active-liquidity share.
    # For a hypothetical position, this is a reasonable proxy, though not exact feeGrowthInside accounting.
    if pd.isna(row["feesUSD"]) or pd.isna(row["liquidity"]) or row["liquidity"] <= 0:
        return 0.0
    share = min(max(L / row["liquidity"], 0.0), 1.0)
    share = max(share, active_liq_share)
    return float(row["feesUSD"]) * share


# =========================
# MAIN BACKTEST
# =========================
def build_data():
    spot = fetch_binance_klines("ETHUSDT", BAR, START, END)
    funding = fetch_binance_funding("ETHUSDT", START, END)
    if spot.empty:
        raise RuntimeError("No Binance spot data returned.")
    if funding.empty:
        funding = pd.DataFrame(columns=["ts", "fundingRate"])

    pool = fetch_pool_meta(POOL_ADDRESS)
    pool_hours = fetch_pool_hour_data(
        POOL_ADDRESS,
        int(pd.Timestamp(START, tz="UTC").timestamp()),
        int(pd.Timestamp(END, tz="UTC").timestamp()),
    )

    if pool_hours.empty:
        raise RuntimeError("No Uniswap poolHourData returned.")

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
    return df, pool


def backtest():
    df, pool = build_data()
    fee_tier = float(pool["feeTier"]) / 1e6
    df = df.dropna(subset=["price", "liquidity", "feesUSD"]).copy()

    entry_price = float(df["price"].iloc[0])

    if RANGE_MODE == "centered_pct":
        Pa = entry_price * CENTERED_LOWER_PCT
        Pb = entry_price * CENTERED_UPPER_PCT
    else:
        Pa = tick_to_price(TICK_LOWER)
        Pb = tick_to_price(TICK_UPPER)

    L = liquidity_from_value(INITIAL_CAPITAL_USD, entry_price, Pa, Pb)

    amt0_0, amt1_0 = amounts_from_liquidity(L, entry_price, Pa, Pb)
    delta0 = lp_delta_eth(L, entry_price, Pa, Pb)

    hedge_eth = -HEDGE_FRACTION * delta0
    cash = INITIAL_CAPITAL_USD

    if abs(hedge_eth) > 0:
        trade_fee = (
            abs(hedge_eth)
            * entry_price
            * (BINANCE_TAKER_FEE if USE_TAKER else BINANCE_MAKER_FEE)
        )
        cash -= trade_fee

    rows = []
    prev_price = entry_price

    for _, row in df.iterrows():
        P = float(row["price"])
        tick = int(row["tick"]) if not pd.isna(row["tick"]) else np.nan

        amt0, amt1 = amounts_from_liquidity(L, P, Pa, Pb)
        lp_value = amt0 * P + amt1
        delta = lp_delta_eth(L, P, Pa, Pb)

        # Actual pool volume / fees
        fee_income = estimate_fee_income(
            row, L, float(row["liquidity"]), fee_tier, ACTIVE_LIQUIDITY_SHARE
        )

        # Hedge mark-to-market
        hedge_pnl = hedge_eth * (P - prev_price)

        # Funding cost/benefit
        funding_payment = -hedge_eth * P * float(row["fundingRate"])

        cash += fee_income + hedge_pnl + funding_payment

        # Rebalance hedge if delta drift too much
        hedge_target = -HEDGE_FRACTION * delta
        if abs(hedge_target - hedge_eth) >= REBALANCE_DELTA_THRESHOLD_ETH:
            trade_qty = hedge_target - hedge_eth
            trade_fee = (
                abs(trade_qty)
                * P
                * (BINANCE_TAKER_FEE if USE_TAKER else BINANCE_MAKER_FEE)
            )
            cash -= trade_fee
            hedge_eth = hedge_target

        total_equity = cash + lp_value + hedge_eth * P

        rows.append(
            {
                "ts": row["ts"],
                "binance_price": P,
                "uniswap_price": float(row["price"]),
                "tick": tick,
                "pool_liquidity": float(row["liquidity"]),
                "pool_volumeUSD": float(row["volumeUSD"]),
                "pool_feesUSD": float(row["feesUSD"]),
                "lp_amount0": amt0,
                "lp_amount1": amt1,
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
    out["cum_strategy_pnl"] = out["strategy_pnl"]

    out_path = os.path.join(
        OUT_DIR, "uniswap_v3_delta_hedged_hypothetical_backtest.csv"
    )
    out.to_csv(out_path, index=False)

    print("Backtest complete")
    print(
        f"Pool: {pool['token0']['symbol']}/{pool['token1']['symbol']} feeTier={fee_tier}"
    )
    print(f"Entry price: {entry_price:.4f}")
    print(f"Range: [{Pa:.6f}, {Pb:.6f}]")
    print(
        out[
            ["ts", "binance_price", "total_equity", "strategy_pnl", "unhedged_pnl"]
        ].tail()
    )
    print(f"Saved results to {out_path}")


if __name__ == "__main__":
    backtest()
