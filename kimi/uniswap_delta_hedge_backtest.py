"""
Uniswap V3 LP Delta-Hedge Backtest
==================================
Backtests whether fees from an ETH/USDC 0.05% LP position
cover the cost of delta-hedging with Binance ETH-USDT perpetuals.

Author: Generated for backtesting analysis
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
# UNISWAP V3 MATH
# =============================================================================

class UniswapV3Math:
    """Pure functions for Uniswap V3 concentrated liquidity math."""

    @staticmethod
    def price_to_tick(p: float) -> int:
        """Convert price (USDC per ETH) to tick."""
        return int(np.floor(np.log(p) / np.log(1.0001)))

    @staticmethod
    def tick_to_price(tick: int) -> float:
        """Convert tick to price (USDC per ETH)."""
        return 1.0001 ** tick

    @staticmethod
    def get_sqrt_ratio(tick: int) -> float:
        """Get sqrt(price) at tick."""
        return np.sqrt(1.0001 ** tick)

    @classmethod
    def get_amounts(cls, tick: int, tick_a: int, tick_b: int, 
                    liquidity: float) -> Tuple[float, float]:
        """
        Get token amounts for a position given current tick.
        Returns (token0_amount, token1_amount) = (ETH, USDC).
        """
        sqrt_p = cls.get_sqrt_ratio(tick)
        sqrt_pa = cls.get_sqrt_ratio(tick_a)
        sqrt_pb = cls.get_sqrt_ratio(tick_b)

        if tick < tick_a:
            # Below range: 100% ETH
            x = liquidity * (sqrt_pb - sqrt_pa) / (sqrt_pa * sqrt_pb)
            y = 0.0
        elif tick >= tick_b:
            # Above range: 100% USDC
            x = 0.0
            y = liquidity * (sqrt_pb - sqrt_pa)
        else:
            # In range: mixed
            x = liquidity * (sqrt_pb - sqrt_p) / (sqrt_p * sqrt_pb)
            y = liquidity * (sqrt_p - sqrt_pa)

        return x, y

    @classmethod
    def get_liquidity(cls, tick: int, tick_a: int, tick_b: int,
                      amount0: float, amount1: float) -> float:
        """Calculate liquidity L given desired token amounts at current tick."""
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
    def get_delta_eth(cls, tick: int, tick_a: int, tick_b: int, 
                      liquidity: float) -> float:
        """
        Delta of LP position in ETH terms (dValue/dPrice).
        This equals the ETH amount held in the position.
        To hedge: short this amount on Binance perpetual.
        """
        x, _ = cls.get_amounts(tick, tick_a, tick_b, liquidity)
        return x

    @classmethod
    def get_value_usd(cls, tick: int, tick_a: int, tick_b: int,
                      liquidity: float, price: float) -> float:
        """Mark-to-market value of LP position in USD."""
        x, y = cls.get_amounts(tick, tick_a, tick_b, liquidity)
        return x * price + y


# =============================================================================
# DATA FETCHERS
# =============================================================================

class BinanceDataFetcher:
    """Fetch historical data from Binance Futures API (no API key needed)."""

    BASE_URL = "https://fapi.binance.com"

    @classmethod
    def get_perp_klines(cls, symbol: str = "ETHUSDT", interval: str = "1h",
                        start_ms: int = None, end_ms: int = None,
                        max_days: int = 90) -> pd.DataFrame:
        """
        Fetch perpetual futures OHLCV + volume.

        Columns: open_time, open, high, low, close, volume, quote_volume, trades
        """
        if end_ms is None:
            end_ms = int(datetime.now().timestamp() * 1000)
        if start_ms is None:
            start_ms = end_ms - max_days * 24 * 60 * 60 * 1000

        url = f"{cls.BASE_URL}/fapi/v1/klines"
        all_data = []

        while start_ms < end_ms:
            params = {
                'symbol': symbol,
                'interval': interval,
                'startTime': start_ms,
                'endTime': min(start_ms + 1000 * cls._interval_ms(interval), end_ms),
                'limit': 1000
            }
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                all_data.extend(data)
                start_ms = data[-1][0] + 1
                time.sleep(0.05)
            except Exception as e:
                print(f"Error fetching klines: {e}")
                break

        if not all_data:
            return pd.DataFrame()

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
    def get_funding_rates(cls, symbol: str = "ETHUSDT",
                          start_ms: int = None, end_ms: int = None,
                          max_days: int = 90) -> pd.DataFrame:
        """
        Fetch historical funding rates (paid every 8 hours on Binance).
        Returns DataFrame with fundingRate (8-hour rate) indexed by fundingTime.
        """
        if end_ms is None:
            end_ms = int(datetime.now().timestamp() * 1000)
        if start_ms is None:
            start_ms = end_ms - max_days * 24 * 60 * 60 * 1000

        url = f"{cls.BASE_URL}/fapi/v1/fundingRate"
        all_data = []

        while start_ms < end_ms:
            params = {
                'symbol': symbol,
                'startTime': start_ms,
                'endTime': end_ms,
                'limit': 1000
            }
            try:
                resp = requests.get(url, params=params, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                if not data:
                    break
                all_data.extend(data)
                start_ms = int(data[-1]['fundingTime']) + 1
                time.sleep(0.05)
            except Exception as e:
                print(f"Error fetching funding: {e}")
                break

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        df['fundingTime'] = pd.to_datetime(df['fundingTime'], unit='ms')
        df['fundingRate'] = df['fundingRate'].astype(float)
        return df.set_index('fundingTime')[['fundingRate']]

    @staticmethod
    def _interval_ms(interval: str) -> int:
        """Convert interval string to milliseconds."""
        mapping = {
            '1m': 60_000, '5m': 300_000, '15m': 900_000, '30m': 1_800_000,
            '1h': 3_600_000, '2h': 7_200_000, '4h': 14_400_000, '6h': 21_600_000,
            '8h': 28_800_000, '12h': 43_200_000, '1d': 86_400_000
        }
        return mapping.get(interval, 3_600_000)


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

@dataclass
class BacktestConfig:
    """Configuration for the backtest."""
    # Pool parameters
    pool_fee_rate: float = 0.0005        # 0.05% for ETH/USDC

    # Hedge parameters
    binance_taker_fee: float = 0.0004    # 0.04% taker on Binance perpetuals (Tier 0)
    rebalance_mode: Literal['threshold', 'periodic'] = 'threshold'
    rebalance_threshold: float = 0.05    # Rebalance when delta changes by 5%
    rebalance_period: int = 24           # Hours between rebalances if periodic

    # Capital & Range
    initial_capital_usd: float = 100_000
    lower_price: float = 1800.0
    upper_price: float = 2200.0

    # Pool share estimate (simplification)
    our_share_of_pool: float = 0.001     # We own 0.1% of active liquidity

    # Slippage/impact estimate for hedge trades
    slippage_bps: float = 1.0            # 1 bp slippage on hedge rebalances


class DeltaHedgeBacktest:
    """
    Backtest engine for Uniswap V3 LP + Binance perp delta hedge.

    Core P&L attribution:
    1. LP Fees Earned (positive)
    2. Binance Trading Fees (negative)
    3. Funding Rate Costs (usually negative, can be positive if funding negative)
    4. Slippage / Market Impact (negative)
    5. Hedge Error (residual P&L from discrete rebalancing vs continuous)
    """

    def __init__(self, config: BacktestConfig):
        self.cfg = config
        self.tick_lower = UniswapV3Math.price_to_tick(config.lower_price)
        self.tick_upper = UniswapV3Math.price_to_tick(config.upper_price)

    def run(self, 
            price_data: pd.DataFrame,
            funding_data: pd.DataFrame,
            pool_volume_data: Optional[pd.DataFrame] = None) -> pd.DataFrame:
        """
        Run the backtest.

        Parameters
        ----------
        price_data : DataFrame
            Index: datetime. Columns: 'close' (ETH price), optionally 'pool_volume_24h' (USD)
        funding_data : DataFrame
            Index: datetime. Column: 'fundingRate' (8-hour rate, e.g. 0.0001 = 0.01%)
        pool_volume_data : DataFrame, optional
            Index: datetime. Column: 'volume_usd' (pool volume in period)

        Returns
        -------
        results : DataFrame
            Detailed backtest results indexed by time.
        """
        df = price_data.copy()
        df['tick'] = df['close'].apply(UniswapV3Math.price_to_tick)

        # Determine bar size in hours for annualization
        if len(df) > 1:
            bar_hours = (df.index[1] - df.index[0]).total_seconds() / 3600
        else:
            bar_hours = 1.0

        # Initialize LP position: 50/50 allocation at starting price
        p0 = df['close'].iloc[0]
        eth_alloc = (self.cfg.initial_capital_usd / 2) / p0
        usdc_alloc = self.cfg.initial_capital_usd / 2

        liquidity = UniswapV3Math.get_liquidity(
            UniswapV3Math.price_to_tick(p0),
            self.tick_lower, self.tick_upper,
            eth_alloc, usdc_alloc
        )

        # Hedge state
        hedge_eth = 0.0           # ETH short position (negative = short)
        hedge_avg_price = 0.0     # Average entry price of current hedge
        hedge_realized_pnl = 0.0  # Cumulative realized P&L from hedge trades

        # Cash flows
        cum_fees = 0.0
        cum_binance_fees = 0.0
        cum_funding = 0.0
        cum_slippage = 0.0

        # For tracking
        last_rebalance_idx = 0

        results = []

        for i, (ts, row) in enumerate(df.iterrows()):
            price = row['close']
            tick = row['tick']

            # --- LP Position State ---
            eth_amt, usdc_amt = UniswapV3Math.get_amounts(
                tick, self.tick_lower, self.tick_upper, liquidity
            )
            lp_value = eth_amt * price + usdc_amt
            delta_eth = eth_amt  # dValue/dPrice = ETH held
            in_range = self.tick_lower <= tick < self.tick_upper

            # --- Fee Accrual ---
            # Simplified model: fees proportional to pool volume * our share
            # NOTE: For accuracy, you need historical tick-level liquidity from The Graph/Dune
            fees_earned = 0.0
            if in_range:
                if pool_volume_data is not None and ts in pool_volume_data.index:
                    vol = pool_volume_data.loc[ts, 'volume_usd']
                elif 'pool_volume_24h' in row:
                    # Assume volume is evenly distributed across bars
                    vol = row['pool_volume_24h'] * (bar_hours / 24)
                else:
                    vol = 0.0

                fees_earned = vol * self.cfg.pool_fee_rate * self.cfg.our_share_of_pool
                cum_fees += fees_earned

            # --- Funding Cost ---
            # Interpolate funding rate for this bar
            funding_rate_8h = self._get_funding_rate(funding_data, ts)
            # Convert 8h rate to period rate
            funding_rate_period = funding_rate_8h * (bar_hours / 8)

            if abs(hedge_eth) > 0:
                notional = abs(hedge_eth) * price
                # If funding rate > 0, longs pay shorts. We are short, so we RECEIVE funding.
                # Cost is negative (income) when funding > 0.
                funding_pnl = notional * funding_rate_period * (-1 if hedge_eth < 0 else 1)
                # Actually: if we are short (-eth), and funding > 0, we get paid.
                # funding_pnl = -notional * funding_rate_period * sign(hedge)
                funding_pnl = -hedge_eth * price * funding_rate_period
                cum_funding += funding_pnl
            else:
                funding_pnl = 0.0

            # --- Rebalancing Logic ---
            target_hedge = -delta_eth  # Short exact delta

            should_rebalance = False
            if i == 0:
                should_rebalance = True
            elif self.cfg.rebalance_mode == 'periodic':
                if i - last_rebalance_idx >= self.cfg.rebalance_period:
                    should_rebalance = True
            else:  # threshold
                if abs(hedge_eth) > 0:
                    delta_change = abs(target_hedge - hedge_eth)
                    if delta_change / abs(hedge_eth) > self.cfg.rebalance_threshold:
                        should_rebalance = True
                else:
                    should_rebalance = True

            trade_pnl = 0.0
            if should_rebalance:
                trade_size = target_hedge - hedge_eth  # ETH to trade

                if abs(trade_size) > 1e-12:
                    # Realize P&L on portion of hedge being closed
                    if abs(hedge_eth) > 0 and np.sign(trade_size) != np.sign(hedge_eth):
                        # Reducing position
                        close_size = min(abs(trade_size), abs(hedge_eth))
                        trade_pnl = -np.sign(hedge_eth) * close_size * (price - hedge_avg_price)
                        hedge_realized_pnl += trade_pnl

                    # Binance trading fee
                    trade_notional = abs(trade_size) * price
                    binance_fee = trade_notional * self.cfg.binance_taker_fee
                    cum_binance_fees += binance_fee

                    # Slippage / market impact
                    slippage_cost = trade_notional * (self.cfg.slippage_bps / 10000)
                    cum_slippage += slippage_cost

                    # Update hedge position and average price
                    new_hedge = hedge_eth + trade_size
                    if abs(new_hedge) < 1e-12:
                        hedge_avg_price = 0.0
                    else:
                        # Weighted average for remaining + new
                        if np.sign(new_hedge) == np.sign(hedge_eth) and abs(hedge_eth) > 0:
                            hedge_avg_price = (hedge_eth * hedge_avg_price + trade_size * price) / new_hedge
                        else:
                            hedge_avg_price = price

                    hedge_eth = new_hedge
                    last_rebalance_idx = i

            # --- Mark-to-Market ---
            # Unrealized P&L on open hedge
            hedge_unrealized = 0.0
            if abs(hedge_eth) > 0 and hedge_avg_price > 0:
                hedge_unrealized = hedge_eth * (price - hedge_avg_price)

            # Total P&L attribution
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
                'hedge_avg_price': hedge_avg_price,
                'fees_earned': fees_earned,
                'cum_fees': cum_fees,
                'funding_pnl': funding_pnl,
                'cum_funding': cum_funding,
                'binance_fees': (cum_binance_fees if should_rebalance and i > 0 else 0.0),
                'cum_binance_fees': cum_binance_fees,
                'slippage_cost': (cum_slippage if should_rebalance and i > 0 else 0.0),
                'cum_slippage': cum_slippage,
                'hedge_realized_pnl': hedge_realized_pnl,
                'hedge_unrealized_pnl': hedge_unrealized,
                'lp_pnl': lp_pnl,
                'total_pnl': total_pnl,
                'rebalanced': should_rebalance and i > 0
            })

        return pd.DataFrame(results).set_index('timestamp')

    def _get_funding_rate(self, funding_data: pd.DataFrame, ts: pd.Timestamp) -> float:
        """Get interpolated funding rate for timestamp."""
        if funding_data.empty:
            return 0.0001  # Default: 0.01% per 8h

        try:
            # Find nearest funding rate
            idx = funding_data.index.get_indexer([ts], method='nearest')[0]
            if idx >= 0:
                return funding_data.iloc[idx]['fundingRate']
        except:
            pass
        return 0.0001

    def summarize(self, results: pd.DataFrame) -> dict:
        """Generate summary statistics."""
        final = results.iloc[-1]
        duration_days = (results.index[-1] - results.index[0]).days

        # Annualize
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

        # Key metric: do fees cover hedge costs?
        hedge_costs = abs(final['cum_funding']) + final['cum_binance_fees'] + final['cum_slippage']
        summary['fees_vs_hedge_costs'] = final['cum_fees'] - hedge_costs
        summary['fees_cover_hedge'] = final['cum_fees'] > hedge_costs

        return summary


# =============================================================================
# EXAMPLE / MAIN
# =============================================================================

def main():
    """
    Example usage. Run this to fetch data from Binance and backtest.

    NOTE: For pool volume data, you need to source from:
    - Dune Analytics (uniswap_v3_ethereum.Pair_Swap_events)
    - The Graph (Uniswap V3 subgraph)
    - Allium / Flipside Crypto

    Below we use a placeholder assuming Uniswap volume is ~5-15% of Binance volume.
    """

    print("Fetching Binance data...")
    end = int(datetime.now().timestamp() * 1000)
    start = end - 90 * 24 * 60 * 60 * 1000  # 90 days

    # Fetch price and funding data
    klines = BinanceDataFetcher.get_perp_klines("ETHUSDT", "1h", start, end)
    funding = BinanceDataFetcher.get_funding_rates("ETHUSDT", start, end)

    if klines.empty:
        print("Failed to fetch price data. Check network connection.")
        return

    # Create placeholder pool volume: assume Uniswap ETH/USDC 0.05% does
    # roughly 5-10% of Binance perpetual volume (highly variable, replace with real data!)
    klines['pool_volume_24h'] = klines['quote_volume'] * 0.08

    # Configuration
    config = BacktestConfig(
        pool_fee_rate=0.0005,
        binance_taker_fee=0.0004,      # 0.04% for taker on Binance
        rebalance_mode='threshold',
        rebalance_threshold=0.05,      # Rebalance when delta drifts 5%
        initial_capital_usd=100_000,
        lower_price=1800,
        upper_price=2200,
        our_share_of_pool=0.0005,      # 0.05% of pool (adjust based on your size)
        slippage_bps=1.0               # 1 basis point slippage
    )

    print(f"\nRunning backtest: {config.lower_price} - {config.upper_price} USDC/ETH")
    print(f"Initial capital: ${config.initial_capital_usd:,.0f}")
    print(f"Pool share: {config.our_share_of_pool*100:.3f}%")
    print("-" * 60)

    engine = DeltaHedgeBacktest(config)
    results = engine.run(klines, funding)
    summary = engine.summarize(results)

    # Print summary
    print(f"\n{'='*60}")
    print("BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"Duration: {summary['backtest_days']} days")
    print(f"Time in range: {summary['time_in_range']:.1f}%")
    print(f"Rebalances: {summary['rebalance_count']}")
    print(f"\nLP FEES:")
    print(f"  Total fees earned:      ${summary['total_fees_earned']:>12,.2f}")
    print(f"\nHEDGE COSTS:")
    print(f"  Funding P&L:            ${summary['total_funding_pnl']:>12,.2f}")
    print(f"  Binance trading fees:   ${summary['total_binance_fees']:>12,.2f}")
    print(f"  Slippage / impact:      ${summary['total_slippage']:>12,.2f}")
    total_hedge_cost = (abs(summary['total_funding_pnl']) + 
                       summary['total_binance_fees'] + 
                       summary['total_slippage'])
    print(f"  Total hedge costs:      ${total_hedge_cost:>12,.2f}")
    print(f"\nNET:")
    print(f"  Fees - Hedge Costs:     ${summary['fees_vs_hedge_costs']:>12,.2f}")
    print(f"  Net P&L:                ${summary['net_pnl']:>12,.2f} ({summary['net_pnl_pct']:.2f}%)")
    print(f"  Net APR:                {summary['net_apr']:>12,.2f}%")
    print(f"\nDo fees cover hedge?     {'YES ✓' if summary['fees_cover_hedge'] else 'NO ✗'}")
    print(f"{'='*60}\n")

    # Save results
    results.to_csv('backtest_results.csv')
    print("Detailed results saved to backtest_results.csv")

    return results, summary


if __name__ == "__main__":
    main()
