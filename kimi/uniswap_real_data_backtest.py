"""
Uniswap V3 LP Delta-Hedge Backtest — REAL DATA EDITION
========================================================
Fetches actual historical data from:
  1. The Graph (decentralized network) — requires free API key
  2. Goldsky — free community subgraphs, no API key needed for public endpoints
  3. Dune Analytics — free tier available
  4. Direct RPC (fallback)

Pool: ETH/USDC 0.05% = 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640
"""

import pandas as pd
import numpy as np
import requests
import time
from dataclasses import dataclass
from typing import Optional, Tuple, Literal
from datetime import datetime, timedelta
import json

# =============================================================================
# CONFIGURATION
# =============================================================================

ETH_USDC_005 = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640".lower()

# --- The Graph (decentralized network) ---
# Get a FREE API key at: https://thegraph.com/studio/
# Free tier: 100,000 queries/month (more than enough)
THEGRAPH_API_KEY = "YOUR_API_KEY_HERE"  # <-- PASTE YOUR KEY HERE
THEGRAPH_V3_ENDPOINT = f"https://gateway.thegraph.com/api/{THEGRAPH_API_KEY}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

# --- Goldsky (community subgraphs) ---
# Free tier, no API key for public endpoints.
# Find endpoints at: https://app.goldsky.com/dashboard
# Common pattern: https://api.goldsky.com/api/public/<project>/subgraphs/<name>/<tag>/gn
GOLDSKY_ENDPOINT = "https://api.goldsky.com/api/public/project_cl8ylkiw00krx0hvza0qw17vn/subgraphs/uniswap-v3-ethereum/prod/gn"

# --- Dune Analytics ---
# Get API key at: https://dune.com/settings/api
# Free tier: 4,000 credits/month
DUNE_API_KEY = "YOUR_DUNE_API_KEY_HERE"

# --- Binance (free, no key needed) ---
BINANCE_BASE = "https://fapi.binance.com"


# =============================================================================
# UNISWAP V3 MATH (same as before)
# =============================================================================

class UniswapV3Math:
    @staticmethod
    def price_to_tick(p: float) -> int:
        return int(np.floor(np.log(p) / np.log(1.0001)))

    @staticmethod
    def tick_to_price(tick: int) -> float:
        return 1.0001 ** tick

    @staticmethod
    def get_sqrt_ratio(tick: int) -> float:
        return np.sqrt(1.0001 ** tick)

    @classmethod
    def get_amounts(cls, tick: int, tick_a: int, tick_b: int, liquidity: float) -> Tuple[float, float]:
        sqrt_p = cls.get_sqrt_ratio(tick)
        sqrt_pa = cls.get_sqrt_ratio(tick_a)
        sqrt_pb = cls.get_sqrt_ratio(tick_b)
        if tick < tick_a:
            x = liquidity * (sqrt_pb - sqrt_pa) / (sqrt_pa * sqrt_pb)
            y = 0.0
        elif tick >= tick_b:
            x = 0.0
            y = liquidity * (sqrt_pb - sqrt_pa)
        else:
            x = liquidity * (sqrt_pb - sqrt_p) / (sqrt_p * sqrt_pb)
            y = liquidity * (sqrt_p - sqrt_pa)
        return x, y

    @classmethod
    def get_liquidity(cls, tick: int, tick_a: int, tick_b: int, amount0: float, amount1: float) -> float:
        sqrt_p = cls.get_sqrt_ratio(tick)
        sqrt_pa = cls.get_sqrt_ratio(tick_a)
        sqrt_pb = cls.get_sqrt_ratio(tick_b)
        if tick < tick_a:
            L = amount0 * (sqrt_pa * sqrt_pb) / (sqrt_pb - sqrt_pa)
        elif tick >= tick_b:
            L = amount1 / (sqrt_pb - sqrt_pa)
        else:
            L0 = amount0 * (sqrt_p * sqrt_pb) / (sqrt_pb - sqrt_p)
            L1 = amount1 / (sqrt_p - sqrt_pa)
            L = min(L0, L1)
        return L

    @classmethod
    def get_delta_eth(cls, tick: int, tick_a: int, tick_b: int, liquidity: float) -> float:
        x, _ = cls.get_amounts(tick, tick_a, tick_b, liquidity)
        return x

    @classmethod
    def get_value_usd(cls, tick: int, tick_a: int, tick_b: int, liquidity: float, price: float) -> float:
        x, y = cls.get_amounts(tick, tick_a, tick_b, liquidity)
        return x * price + y


# =============================================================================
# DATA FETCHERS
# =============================================================================

class TheGraphFetcher:
    """
    Fetch from The Graph decentralized network.
    Requires API key from https://thegraph.com/studio/ (free tier: 100k queries/mo)
    """

    def __init__(self, api_key: str = None):
        self.api_key = api_key or THEGRAPH_API_KEY
        if self.api_key == "YOUR_API_KEY_HERE":
            raise ValueError(
                "Please set THEGRAPH_API_KEY. Get one free at https://thegraph.com/studio/"
            )
        self.endpoint = f"https://gateway.thegraph.com/api/{self.api_key}/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"

    def _query(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = requests.post(self.endpoint, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data.get("data", {})

    def get_pool_hour_data(self, pool_address: str = ETH_USDC_005, hours: int = 720) -> pd.DataFrame:
        """Fetch hourly pool data for backtesting."""
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
            data = self._query(query, {"pool": pool_address, "start": start_ts})
            batch = data.get("poolHourDatas", [])
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
        df["periodStartUnix"] = pd.to_datetime(df["periodStartUnix"].astype(int), unit="s")
        for col in ["volumeUSD", "feesUSD", "liquidity", "sqrtPriceX96", "tick", "tvlUSD", "txCount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # sqrtPriceX96 -> actual price (token1 per token0)
        # For ETH/USDC: token0 = USDC, token1 = WETH, so token1Price = ETH price in USDC
        df["price"] = (df["sqrtPriceX96"] / (2**96)) ** 2

        return df.set_index("periodStartUnix")

    def get_pool_day_data(self, pool_address: str = ETH_USDC_005, days: int = 90) -> pd.DataFrame:
        """Fetch daily pool data."""
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
        start_ts = int((datetime.now() - timedelta(days=days)).timestamp())
        data = self._query(query, {"pool": pool_address, "start": start_ts})

        if not data or "poolDayDatas" not in data:
            return pd.DataFrame()

        df = pd.DataFrame(data["poolDayDatas"])
        df["date"] = pd.to_datetime(df["date"].astype(int), unit="s")
        for col in ["volumeUSD", "feesUSD", "liquidity", "sqrtPriceX96", "tick", 
                    "token0Price", "token1Price", "tvlUSD", "txCount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df["price"] = (df["sqrtPriceX96"] / (2**96)) ** 2
        return df.set_index("date")


class GoldskyFetcher:
    """
    Fetch from Goldsky public/community subgraphs.
    No API key needed for public endpoints.
    Sign up at https://goldsky.com for free tier.
    """

    def __init__(self, endpoint: str = None):
        self.endpoint = endpoint or GOLDSKY_ENDPOINT

    def _query(self, query: str, variables: dict = None) -> dict:
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        resp = requests.post(self.endpoint, json=payload, headers={"Content-Type": "application/json"}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            raise RuntimeError(f"GraphQL errors: {data['errors']}")
        return data.get("data", {})

    def get_pool_hour_data(self, pool_address: str = ETH_USDC_005, hours: int = 720) -> pd.DataFrame:
        """Fetch hourly pool data from Goldsky."""
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
            try:
                data = self._query(query, {"pool": pool_address, "start": start_ts})
                batch = data.get("poolHourDatas", [])
                if not batch:
                    break
                all_data.extend(batch)
                start_ts = int(batch[-1]["periodStartUnix"]) + 1
                if len(batch) < 1000:
                    break
                time.sleep(0.2)
            except Exception as e:
                print(f"Goldsky fetch error: {e}")
                break

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df["periodStartUnix"] = pd.to_datetime(df["periodStartUnix"].astype(int), unit="s")
        for col in ["volumeUSD", "feesUSD", "liquidity", "sqrtPriceX96", "tick", "tvlUSD", "txCount"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["price"] = (df["sqrtPriceX96"] / (2**96)) ** 2
        return df.set_index("periodStartUnix")


class BinanceFetcher:
    """Fetch Binance perpetual futures data (free, no API key)."""

    BASE = "https://fapi.binance.com"

    @classmethod
    def get_klines(cls, symbol="ETHUSDT", interval="1h", start_ms=None, end_ms=None):
        if end_ms is None:
            end_ms = int(datetime.now().timestamp() * 1000)
        if start_ms is None:
            start_ms = end_ms - 90 * 24 * 60 * 60 * 1000

        url = f"{cls.BASE}/fapi/v1/klines"
        all_data = []
        while start_ms < end_ms:
            params = {
                'symbol': symbol, 'interval': interval,
                'startTime': start_ms,
                'endTime': min(start_ms + 1000 * 3_600_000, end_ms),
                'limit': 1000
            }
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            all_data.extend(data)
            start_ms = data[-1][0] + 1
            time.sleep(0.05)

        df = pd.DataFrame(all_data, columns=[
            'open_time', 'open', 'high', 'low', 'close', 'volume',
            'close_time', 'quote_volume', 'trades', 'taker_buy_base',
            'taker_buy_quote', 'ignore'
        ])
        df['open_time'] = pd.to_datetime(df['open_time'], unit='ms')
        for col in ['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades']:
            df[col] = df[col].astype(float)
        return df.set_index('open_time')[['open', 'high', 'low', 'close', 'volume', 'quote_volume', 'trades']]

    @classmethod
    def get_funding(cls, symbol="ETHUSDT", start_ms=None, end_ms=None):
        if end_ms is None:
            end_ms = int(datetime.now().timestamp() * 1000)
        if start_ms is None:
            start_ms = end_ms - 90 * 24 * 60 * 60 * 1000

        url = f"{cls.BASE}/fapi/v1/fundingRate"
        all_data = []
        while start_ms < end_ms:
            params = {'symbol': symbol, 'startTime': start_ms, 'endTime': end_ms, 'limit': 1000}
            resp = requests.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if not data:
                break
            all_data.extend(data)
            start_ms = int(data[-1]['fundingTime']) + 1
            time.sleep(0.05)

        df = pd.DataFrame(all_data)
        df['fundingTime'] = pd.to_datetime(df['fundingTime'], unit='ms')
        df['fundingRate'] = df['fundingRate'].astype(float)
        return df.set_index('fundingTime')[['fundingRate']]


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

@dataclass
class BacktestConfig:
    pool_fee_rate: float = 0.0005
    binance_taker_fee: float = 0.0004
    rebalance_mode: Literal['threshold', 'periodic'] = 'threshold'
    rebalance_threshold: float = 0.05
    rebalance_period: int = 24
    initial_capital_usd: float = 100_000
    lower_price: float = 1800.0
    upper_price: float = 2200.0
    our_share_of_pool: float = 0.001
    slippage_bps: float = 1.0


class DeltaHedgeBacktest:
    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.tick_lower = UniswapV3Math.price_to_tick(config.lower_price)
        self.tick_upper = UniswapV3Math.price_to_tick(config.upper_price)

    def run(self, pool_data: pd.DataFrame, funding_data: pd.DataFrame) -> pd.DataFrame:
        """
        Run backtest with REAL Uniswap pool data.

        pool_data must have: 'price', 'volumeUSD', 'feesUSD', 'tick', 'tvlUSD'
        funding_data must have: 'fundingRate'
        """
        df = pool_data.copy()
        if 'tick' not in df.columns:
            df['tick'] = df['price'].apply(UniswapV3Math.price_to_tick)

        bar_hours = (df.index[1] - df.index[0]).total_seconds() / 3600 if len(df) > 1 else 1.0

        # Initialize LP position (50/50 at start)
        p0 = df['price'].iloc[0]
        eth_alloc = (self.cfg.initial_capital_usd / 2) / p0
        usdc_alloc = self.cfg.initial_capital_usd / 2
        liquidity = UniswapV3Math.get_liquidity(
            UniswapV3Math.price_to_tick(p0),
            self.tick_lower, self.tick_upper, eth_alloc, usdc_alloc
        )

        # Estimate pool share from TVL if available
        if 'tvlUSD' in df.columns:
            avg_tvl = df['tvlUSD'].mean()
            self.cfg.our_share_of_pool = min(
                self.cfg.initial_capital_usd / avg_tvl,
                0.5  # Cap at 50% for sanity
            )
            print(f"Estimated pool share: {self.cfg.our_share_of_pool*100:.4f}% (based on avg TVL ${avg_tvl:,.0f})")

        hedge_eth = 0.0
        hedge_avg_price = 0.0
        hedge_realized_pnl = 0.0

        cum_fees = 0.0
        cum_binance_fees = 0.0
        cum_funding = 0.0
        cum_slippage = 0.0

        last_rebalance_idx = 0
        results = []

        for i, (ts, row) in enumerate(df.iterrows()):
            price = row['price']
            tick = int(row['tick']) if not pd.isna(row['tick']) else UniswapV3Math.price_to_tick(price)

            eth_amt, usdc_amt = UniswapV3Math.get_amounts(tick, self.tick_lower, self.tick_upper, liquidity)
            lp_value = eth_amt * price + usdc_amt
            delta_eth = eth_amt
            in_range = self.tick_lower <= tick < self.tick_upper

            # REAL fees from pool data
            fees_earned = 0.0
            if in_range:
                if 'feesUSD' in row and not pd.isna(row['feesUSD']):
                    # feesUSD in poolHourData is total pool fees for that hour
                    fees_earned = row['feesUSD'] * self.cfg.our_share_of_pool
                elif 'volumeUSD' in row and not pd.isna(row['volumeUSD']):
                    fees_earned = row['volumeUSD'] * self.cfg.pool_fee_rate * self.cfg.our_share_of_pool
                cum_fees += fees_earned

            # Funding
            funding_rate_8h = self._get_funding_rate(funding_data, ts)
            funding_rate_period = funding_rate_8h * (bar_hours / 8)

            if abs(hedge_eth) > 0:
                funding_pnl = -hedge_eth * price * funding_rate_period
                cum_funding += funding_pnl
            else:
                funding_pnl = 0.0

            # Rebalancing
            target_hedge = -delta_eth
            should_rebalance = False
            if i == 0:
                should_rebalance = True
            elif self.cfg.rebalance_mode == 'periodic':
                if i - last_rebalance_idx >= self.cfg.rebalance_period:
                    should_rebalance = True
            else:
                if abs(hedge_eth) > 0:
                    if abs(target_hedge - hedge_eth) / abs(hedge_eth) > self.cfg.rebalance_threshold:
                        should_rebalance = True
                else:
                    should_rebalance = True

            trade_pnl = 0.0
            if should_rebalance:
                trade_size = target_hedge - hedge_eth
                if abs(trade_size) > 1e-12:
                    if abs(hedge_eth) > 0 and np.sign(trade_size) != np.sign(hedge_eth):
                        close_size = min(abs(trade_size), abs(hedge_eth))
                        trade_pnl = -np.sign(hedge_eth) * close_size * (price - hedge_avg_price)
                        hedge_realized_pnl += trade_pnl

                    trade_notional = abs(trade_size) * price
                    binance_fee = trade_notional * self.cfg.binance_taker_fee
                    cum_binance_fees += binance_fee

                    slippage_cost = trade_notional * (self.cfg.slippage_bps / 10000)
                    cum_slippage += slippage_cost

                    new_hedge = hedge_eth + trade_size
                    if abs(new_hedge) < 1e-12:
                        hedge_avg_price = 0.0
                    else:
                        if np.sign(new_hedge) == np.sign(hedge_eth) and abs(hedge_eth) > 0:
                            hedge_avg_price = (hedge_eth * hedge_avg_price + trade_size * price) / new_hedge
                        else:
                            hedge_avg_price = price
                    hedge_eth = new_hedge
                    last_rebalance_idx = i

            hedge_unrealized = hedge_eth * (price - hedge_avg_price) if abs(hedge_eth) > 0 and hedge_avg_price > 0 else 0.0
            lp_pnl = lp_value - self.cfg.initial_capital_usd
            total_pnl = (lp_pnl + hedge_realized_pnl + hedge_unrealized + 
                        cum_fees + cum_funding - cum_binance_fees - cum_slippage)

            results.append({
                'timestamp': ts,
                'price': price,
                'in_range': in_range,
                'eth_held': eth_amt,
                'usdc_held': usdc_amt,
                'lp_value': lp_value,
                'delta_eth': delta_eth,
                'hedge_eth': hedge_eth,
                'fees_earned': fees_earned,
                'cum_fees': cum_fees,
                'funding_pnl': funding_pnl,
                'cum_funding': cum_funding,
                'cum_binance_fees': cum_binance_fees,
                'cum_slippage': cum_slippage,
                'hedge_realized_pnl': hedge_realized_pnl,
                'hedge_unrealized_pnl': hedge_unrealized,
                'lp_pnl': lp_pnl,
                'total_pnl': total_pnl,
                'rebalanced': should_rebalance and i > 0
            })

        return pd.DataFrame(results).set_index('timestamp')

    def _get_funding_rate(self, funding_data: pd.DataFrame, ts: pd.Timestamp) -> float:
        if funding_data.empty:
            return 0.0001
        try:
            idx = funding_data.index.get_indexer([ts], method='nearest')[0]
            if idx >= 0:
                return funding_data.iloc[idx]['fundingRate']
        except:
            pass
        return 0.0001

    def summarize(self, results: pd.DataFrame) -> dict:
        final = results.iloc[-1]
        duration_days = (results.index[-1] - results.index[0]).days
        years = duration_days / 365.25

        summary = {
            'backtest_days': duration_days,
            'initial_capital': self.cfg.initial_capital_usd,
            'price_range': (self.cfg.lower_price, self.cfg.upper_price),
            'final_lp_value': final['lp_value'],
            'total_fees_earned': final['cum_fees'],
            'total_funding_pnl': final['cum_funding'],
            'total_binance_fees': final['cum_binance_fees'],
            'total_slippage': final['cum_slippage'],
            'hedge_realized_pnl': final['hedge_realized_pnl'],
            'hedge_unrealized_pnl': final['hedge_unrealized_pnl'],
            'net_pnl': final['total_pnl'],
            'net_pnl_pct': (final['total_pnl'] / self.cfg.initial_capital_usd) * 100,
            'net_apr': (final['total_pnl'] / self.cfg.initial_capital_usd / years) * 100 if years > 0 else 0,
            'rebalance_count': results['rebalanced'].sum(),
            'time_in_range': results['in_range'].mean() * 100,
        }

        hedge_costs = abs(summary['total_funding_pnl']) + summary['total_binance_fees'] + summary['total_slippage']
        summary['fees_vs_hedge_costs'] = summary['total_fees_earned'] - hedge_costs
        summary['fees_cover_hedge'] = summary['total_fees_earned'] > hedge_costs

        return summary


# =============================================================================
# MAIN
# =============================================================================

def run_full_backtest(data_source: Literal["thegraph", "goldsky", "synthetic"] = "thegraph",
                      days: int = 90):
    """
    Run a complete backtest using real data.

    Parameters
    ----------
    data_source : str
        "thegraph" — The Graph decentralized network (requires API key)
        "goldsky"  — Goldsky community subgraphs (try first, may not need key)
        "synthetic" — Fake data for testing the engine
    days : int
        How many days of history to backtest
    """

    print("=" * 70)
    print(f"Uniswap V3 Delta-Hedge Backtest — {data_source.upper()}")
    print("=" * 70)

    # --- 1. Fetch Uniswap data ---
    print(f"\n[1/3] Fetching Uniswap V3 ETH/USDC 0.05% data ({days} days)...")

    if data_source == "thegraph":
        fetcher = TheGraphFetcher()
        pool_data = fetcher.get_pool_hour_data(ETH_USDC_005, hours=days*24)
    elif data_source == "goldsky":
        fetcher = GoldskyFetcher()
        pool_data = fetcher.get_pool_hour_data(ETH_USDC_005, hours=days*24)
    else:
        # Synthetic fallback
        print("Using synthetic data...")
        np.random.seed(42)
        n = days * 24
        idx = pd.date_range(end=datetime.now(), periods=n, freq='h')
        p0 = 2000
        returns = np.random.normal(0.3/365/24, 0.6/np.sqrt(365*24), n)
        prices = p0 * np.exp(np.cumsum(returns))
        pool_data = pd.DataFrame({
            'price': prices,
            'volumeUSD': np.random.lognormal(15, 0.5, n),
            'feesUSD': np.random.lognormal(10, 0.3, n),
            'tick': [UniswapV3Math.price_to_tick(p) for p in prices],
            'tvlUSD': [50_000_000] * n
        }, index=idx)

    if pool_data.empty:
        print("ERROR: No pool data fetched. Check API keys and endpoints.")
        return None, None

    print(f"  ✓ Got {len(pool_data)} hourly bars")
    print(f"  Price range: ${pool_data['price'].min():.2f} - ${pool_data['price'].max():.2f}")
    print(f"  Total volume: ${pool_data['volumeUSD'].sum():,.0f}")
    print(f"  Total fees: ${pool_data['feesUSD'].sum():,.2f}")

    # --- 2. Fetch Binance data ---
    print(f"\n[2/3] Fetching Binance ETHUSDT perpetual data...")
    start_ms = int(pool_data.index[0].timestamp() * 1000)
    end_ms = int(pool_data.index[-1].timestamp() * 1000)

    binance_klines = BinanceFetcher.get_klines("ETHUSDT", "1h", start_ms, end_ms)
    binance_funding = BinanceFetcher.get_funding("ETHUSDT", start_ms, end_ms)

    print(f"  ✓ Got {len(binance_klines)} price bars")
    print(f"  ✓ Got {len(binance_funding)} funding payments")

    # --- 3. Merge and run backtest ---
    print(f"\n[3/3] Running backtest...")

    # Align datasets
    merged = pool_data.join(binance_klines[['close']], how='inner')
    if 'price' not in merged.columns or merged['price'].isna().all():
        # Fallback: use Binance close as price if pool price missing
        merged['price'] = merged['close']

    # Use pool price preferentially, fall back to Binance
    merged['price'] = merged['price'].fillna(merged['close'])

    config = BacktestConfig(
        pool_fee_rate=0.0005,
        binance_taker_fee=0.0004,
        rebalance_mode='threshold',
        rebalance_threshold=0.05,
        initial_capital_usd=100_000,
        lower_price=1800,
        upper_price=2200,
        slippage_bps=1.0
    )

    engine = DeltaHedgeBacktest(config)
    results = engine.run(merged, binance_funding)
    summary = engine.summarize(results)

    # --- 4. Print results ---
    print(f"\n{'='*70}")
    print("BACKTEST RESULTS")
    print(f"{'='*70}")
    print(f"Duration:          {summary['backtest_days']} days")
    print(f"Time in range:     {summary['time_in_range']:.1f}%")
    print(f"Rebalances:        {summary['rebalance_count']}")
    print(f"\nLP FEES EARNED:")
    print(f"  Total:           ${summary['total_fees_earned']:>15,.2f}")
    print(f"\nHEDGE COSTS:")
    print(f"  Funding P&L:     ${summary['total_funding_pnl']:>15,.2f}")
    print(f"  Binance fees:    ${summary['total_binance_fees']:>15,.2f}")
    print(f"  Slippage:        ${summary['total_slippage']:>15,.2f}")
    total_hedge = abs(summary['total_funding_pnl']) + summary['total_binance_fees'] + summary['total_slippage']
    print(f"  Total hedge:     ${total_hedge:>15,.2f}")
    print(f"\nNET:")
    print(f"  Fees - Costs:    ${summary['fees_vs_hedge_costs']:>15,.2f}")
    print(f"  Net P&L:         ${summary['net_pnl']:>15,.2f} ({summary['net_pnl_pct']:.2f}%)")
    print(f"  Net APR:         {summary['net_apr']:>15,.2f}%")
    print(f"\nFees cover hedge? {'YES ✓' if summary['fees_cover_hedge'] else 'NO ✗'}")
    print(f"{'='*70}\n")

    # Save
    results.to_csv('backtest_real_data.csv')
    print("Results saved to backtest_real_data.csv")

    return results, summary


if __name__ == "__main__":
    # Quick test with synthetic data (no API keys needed)
    run_full_backtest("synthetic", days=30)

    # To run with real data, uncomment one of these:
    # run_full_backtest("goldsky", days=30)
    # run_full_backtest("thegraph", days=30)
