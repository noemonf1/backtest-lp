"""
Real Uniswap V3 Data Fetchers
==============================
Fetch historical pool data from:
1. The Graph (Uniswap V3 Subgraph) — free, programmatic
2. Dune Analytics — free tier, SQL-based
3. Direct RPC (infura/alchemy) — for position-level data

Pool: ETH/USDC 0.05% = 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640
"""

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time
from typing import Optional, List

# Pool addresses
ETH_USDC_005 = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640".lower()
ETH_USDC_03  = "0x8ad599c3a0ff1de082011efddc58f1908eb6e6d8".lower()
ETH_USDT_005 = "0x11b815efb8f581194ae79006d24e0d814b7697f6".lower()


class TheGraphFetcher:
    """
    Fetch data from Uniswap V3 Subgraph via The Graph.
    Free tier: 100k queries/month. No API key needed for public endpoint.
    """

    UNISWAP_V3_URL = "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3"

    @classmethod
    def _query(cls, query: str, variables: dict = None) -> dict:
        """Execute a GraphQL query."""
        payload = {"query": query}
        if variables:
            payload["variables"] = variables

        resp = requests.post(
            cls.UNISWAP_V3_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=60
        )
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data["data"]

    @classmethod
    def get_pool_day_data(cls, pool_address: str = ETH_USDC_005,
                          days: int = 90) -> pd.DataFrame:
        """
        Fetch daily pool stats: volume, fees, liquidity, tick.

        Returns DataFrame with columns:
        date, volumeUSD, feesUSD, liquidity, sqrtPriceX96, tick,
        token0Price, token1Price, tvlUSD, txCount
        """
        query = """
        query ($pool: String!, $start: Int!) {
            poolDayDatas(
                where: { pool: $pool, date_gt: $start }
                orderBy: date
                orderDirection: asc
                first: 1000
            ) {
                date
                volumeUSD
                feesUSD
                liquidity
                sqrtPriceX96
                tick
                token0Price
                token1Price
                tvlUSD
                txCount
            }
        }
        """
        start_timestamp = int((datetime.now() - timedelta(days=days)).timestamp())

        data = cls._query(query, {"pool": pool_address, "start": start_timestamp})

        if not data or "poolDayDatas" not in data:
            return pd.DataFrame()

        df = pd.DataFrame(data["poolDayDatas"])
        df["date"] = pd.to_datetime(df["date"], unit="s")

        numeric_cols = ["volumeUSD", "feesUSD", "liquidity", "sqrtPriceX96", 
                       "tick", "token0Price", "token1Price", "tvlUSD", "txCount"]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

        # sqrtPriceX96 to actual price: (sqrtPriceX96 / 2^96)^2
        # For ETH/USDC, token0 = USDC, token1 = ETH, so token1Price = ETH price in USDC
        df["price_from_sqrt"] = (df["sqrtPriceX96"] / (2**96)) ** 2

        return df.set_index("date")

    @classmethod
    def get_tick_data(cls, pool_address: str = ETH_USDC_005,
                      days: int = 30) -> pd.DataFrame:
        """
        Fetch tick-level liquidity distribution at end of each day.
        This is CRITICAL for accurate fee share calculation.

        Returns: DataFrame of ticks with liquidityNet, liquidityGross.
        """
        # First get pool to find current tick
        pool_query = """
        query ($pool: String!) {
            pool(id: $pool) {
                tick
                liquidity
            }
        }
        """
        pool_data = cls._query(pool_query, {"pool": pool_address})
        current_tick = int(pool_data["pool"]["tick"])

        # Fetch ticks around current price (± 50 ticks = ±0.5%)
        # For full distribution, you need multiple paginated queries
        tick_query = """
        query ($pool: String!, $tickLower: Int!, $tickUpper: Int!) {
            ticks(
                where: { pool: $pool, tickIdx_gte: $tickLower, tickIdx_lte: $tickUpper }
                orderBy: tickIdx
                orderDirection: asc
                first: 1000
            ) {
                tickIdx
                liquidityNet
                liquidityGross
                price0
                price1
            }
        }
        """

        tick_lower = current_tick - 500  # ~5% range
        tick_upper = current_tick + 500

        data = cls._query(tick_query, {
            "pool": pool_address,
            "tickLower": tick_lower,
            "tickUpper": tick_upper
        })

        if not data or "ticks" not in data:
            return pd.DataFrame()

        df = pd.DataFrame(data["ticks"])
        for col in ["tickIdx", "liquidityNet", "liquidityGross"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["price1"] = pd.to_numeric(df["price1"], errors="coerce")  # ETH price in USDC

        return df

    @classmethod
    def get_position_history(cls, position_id: str) -> pd.DataFrame:
        """
        Fetch historical snapshots of a specific NFT position.
        Best way to get ACTUAL fees earned by a real position.

        You can find position IDs on OpenSea or via Etherscan.
        """
        query = """
        query ($position: String!) {
            position(id: $position) {
                id
                owner
                liquidity
                depositedToken0
                depositedToken1
                withdrawnToken0
                withdrawnToken1
                collectedFeesToken0
                collectedFeesToken1
                tickLower { tickIdx }
                tickUpper { tickIdx }
                pool { id token0 { symbol } token1 { symbol } feeTier }
            }
        }
        """
        data = cls._query(query, {"position": position_id})

        if not data or not data.get("position"):
            return pd.DataFrame()

        pos = data["position"]
        print(f"Position {position_id}:")
        print(f"  Range: {pos['tickLower']['tickIdx']} to {pos['tickUpper']['tickIdx']}")
        print(f"  Liquidity: {pos['liquidity']}")
        print(f"  Fees collected: {pos['collectedFeesToken0']} USDC, {pos['collectedFeesToken1']} ETH")

        return pd.DataFrame([pos])

    @classmethod
    def get_hourly_data(cls, pool_address: str = ETH_USDC_005,
                        hours: int = 720) -> pd.DataFrame:
        """
        Fetch hourly pool data for higher-resolution backtests.
        """
        query = """
        query ($pool: String!, $start: Int!) {
            poolHourDatas(
                where: { pool: $pool, periodStartUnix_gt: $start }
                orderBy: periodStartUnix
                orderDirection: asc
                first: 1000
            ) {
                periodStartUnix
                volumeUSD
                feesUSD
                liquidity
                sqrtPriceX96
                tick
                tvlUSD
                txCount
            }
        }
        """
        start_ts = int((datetime.now() - timedelta(hours=hours)).timestamp())

        all_data = []
        while True:
            data = cls._query(query, {"pool": pool_address, "start": start_ts})
            if not data or not data.get("poolHourDatas"):
                break

            batch = data["poolHourDatas"]
            if not batch:
                break

            all_data.extend(batch)
            start_ts = int(batch[-1]["periodStartUnix"]) + 1

            if len(batch) < 1000:
                break
            time.sleep(0.2)

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["periodStartUnix"] = pd.to_datetime(df["periodStartUnix"], unit="s")

        numeric_cols = ["volumeUSD", "feesUSD", "liquidity", "sqrtPriceX96", 
                       "tick", "tvlUSD", "txCount"]
        for col in numeric_cols:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["price"] = (df["sqrtPriceX96"] / (2**96)) ** 2

        return df.set_index("periodStartUnix")


class DuneFetcher:
    """
    Fetch data from Dune Analytics.
    Free tier: 4,000 credits/month. Requires API key from dune.com.

    Best for: custom queries, position-level analysis, exact fee attribution.
    """

    DUNE_API_URL = "https://api.dune.com/api/v1"

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"X-Dune-API-Key": api_key}

    def execute_query(self, query_id: int) -> pd.DataFrame:
        """
        Execute a Dune query by ID and return results.

        For Uniswap V3 ETH/USDC data, useful query IDs:
        - You can create your own queries at dune.com/queries
        """
        # Trigger execution
        exec_url = f"{self.DUNE_API_URL}/query/{query_id}/execute"
        resp = requests.post(exec_url, headers=self.headers, timeout=30)
        resp.raise_for_status()
        execution_id = resp.json()["execution_id"]

        # Poll for completion
        status_url = f"{self.DUNE_API_URL}/execution/{execution_id}/status"
        for _ in range(60):
            status = requests.get(status_url, headers=self.headers, timeout=30).json()
            if status["state"] == "QUERY_STATE_COMPLETED":
                break
            elif status["state"] in ["QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"]:
                raise RuntimeError(f"Query failed: {status}")
            time.sleep(2)

        # Fetch results
        results_url = f"{self.DUNE_API_URL}/execution/{execution_id}/results"
        results = requests.get(results_url, headers=self.headers, timeout=30).json()

        return pd.DataFrame(results["result"]["rows"])

    @staticmethod
    def get_pool_volume_query() -> str:
        """
        SQL query for Dune to get daily Uniswap V3 ETH/USDC 0.05% volume.
        Run this at dune.com/queries and note the query ID.
        """
        return """
        -- Dune Query: Daily Uniswap V3 ETH/USDC 0.05% Volume & Fees
        -- Blockchain: ethereum
        -- Table: dex.trades

        SELECT 
            DATE_TRUNC('day', block_time) AS day,
            SUM(amount_usd) AS volume_usd,
            COUNT(*) AS trade_count,
            SUM(amount_usd * 0.0005) AS estimated_fees_usd
        FROM dex.trades
        WHERE project = 'uniswap'
          AND version = '3'
          AND token_bought_address = 0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2  -- WETH
          AND token_sold_address = 0xA0b86a33E6441E6C7D3D4B4E5E6F7A8B9C0D1E2F  -- USDC
          AND fee_tier = 500  -- 0.05%
          AND block_time >= DATE('2024-01-01')
        GROUP BY 1
        ORDER BY 1
        """


class AlliumFetcher:
    """
    Allium provides high-quality Uniswap V3 data with tick-level granularity.
    Paid service but has the best historical tick data.
    https://allium.so
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = "https://api.allium.so/api/v1"

    def query(self, sql: str) -> pd.DataFrame:
        """Execute SQL against Allium's data warehouse."""
        resp = requests.post(
            f"{self.base_url}/queries/run",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"query": sql},
            timeout=120
        )
        resp.raise_for_status()
        return pd.DataFrame(resp.json()["results"])


# =============================================================================
# INTEGRATION: Modified backtest using real data
# =============================================================================

def run_backtest_with_real_data(days: int = 90):
    """
    Example: Run the backtest using real Uniswap data from The Graph.
    """
    print("Fetching real Uniswap V3 data from The Graph...")

    # 1. Get hourly pool data (price, volume, fees)
    hourly = TheGraphFetcher.get_hourly_data(ETH_USDC_005, hours=days*24)

    if hourly.empty:
        print("Failed to fetch data from The Graph. Check network/subgraph status.")
        return None, None

    print(f"Fetched {len(hourly)} hours of data from {hourly.index[0]} to {hourly.index[-1]}")
    print(f"Avg daily volume: ${hourly['volumeUSD'].sum() / days:,.0f}")
    print(f"Total fees (reported by pool): ${hourly['feesUSD'].sum():,.2f}")

    # 2. Get Binance data for same period
    from datetime import timezone
    start_ms = int(hourly.index[0].replace(tzinfo=timezone.utc).timestamp() * 1000)
    end_ms = int(hourly.index[-1].replace(tzinfo=timezone.utc).timestamp() * 1000)

    print("\nFetching Binance perpetual data...")
    # Import from the backtest module
    # (In practice, import from uniswap_delta_hedge_backtest)

    # For standalone use, here's a minimal fetch:
    binance_url = "https://fapi.binance.com/fapi/v1/klines"
    params = {
        'symbol': 'ETHUSDT',
        'interval': '1h',
        'startTime': start_ms,
        'endTime': end_ms,
        'limit': 1000
    }

    all_klines = []
    while True:
        resp = requests.get(binance_url, params=params, timeout=30)
        data = resp.json()
        if not data:
            break
        all_klines.extend(data)
        params['startTime'] = data[-1][0] + 1
        if len(data) < 1000:
            break
        time.sleep(0.1)

    binance_df = pd.DataFrame(all_klines, columns=[
        'open_time', 'open', 'high', 'low', 'close', 'volume',
        'close_time', 'quote_volume', 'trades', 'taker_buy_base',
        'taker_buy_quote', 'ignore'
    ])
    binance_df['open_time'] = pd.to_datetime(binance_df['open_time'], unit='ms')
    binance_df['close'] = binance_df['close'].astype(float)
    binance_df = binance_df.set_index('open_time')[['close']]

    # 3. Merge datasets
    merged = hourly.join(binance_df, how='inner')

    if merged.empty:
        print("No overlapping timestamps between Uniswap and Binance data.")
        return None, None

    print(f"\nMerged dataset: {len(merged)} hours")
    print(f"Price correlation: {merged['price'].corr(merged['close']):.6f}")

    # 4. Run backtest
    # Use real volume and fees from the pool
    # For 'our_share_of_pool', estimate based on your capital vs pool TVL

    avg_tvl = hourly['tvlUSD'].mean()
    our_capital = 100_000
    estimated_share = our_capital / avg_tvl

    print(f"\nPool average TVL: ${avg_tvl:,.0f}")
    print(f"Your estimated share: {estimated_share*100:.4f}%")

    # The real fees are already in hourly['feesUSD'] — that's the TOTAL pool fees
    # Your share = your liquidity / total active liquidity at each tick
    # For a rough estimate, use TVL-based share
    merged['our_fees'] = merged['feesUSD'] * estimated_share

    return merged, estimated_share


if __name__ == "__main__":
    # Example: Fetch and display recent data
    print("=" * 60)
    print("Uniswap V3 Real Data Fetcher Demo")
    print("=" * 60)

    # Fetch last 30 days of daily data
    daily = TheGraphFetcher.get_pool_day_data(ETH_USDC_005, days=30)
    if not daily.empty:
        print(f"\nLast 30 days ETH/USDC 0.05% stats:")
        print(daily[['volumeUSD', 'feesUSD', 'tvlUSD', 'token1Price']].tail())
        print(f"\nTotal 30-day volume: ${daily['volumeUSD'].sum():,.0f}")
        print(f"Total 30-day fees:   ${daily['feesUSD'].sum():,.2f}")
        print(f"Average daily APR (fees/TVL): {(daily['feesUSD'].sum() / daily['tvlUSD'].mean()) * (365/30) * 100:.2f}%")

    # Fetch tick data for current distribution
    print("\n" + "=" * 60)
    print("Fetching tick liquidity distribution...")
    ticks = TheGraphFetcher.get_tick_data(ETH_USDC_005, days=1)
    if not ticks.empty:
        print(f"Fetched {len(ticks)} ticks")
        print(ticks[['tickIdx', 'liquidityNet', 'price1']].head(10))
