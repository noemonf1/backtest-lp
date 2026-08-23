#!/usr/bin/env python3
"""
Self-Contained Demo: Uniswap V3 Delta-Hedge Backtest
=====================================================
No external APIs needed. Generates realistic synthetic data.
"""

import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from dataclasses import dataclass
from typing import Tuple, Literal
from datetime import datetime, timedelta

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

def generate_realistic_data(days: int = 90, seed: int = 42):
    np.random.seed(seed)
    n_hours = days * 24
    idx = pd.date_range(end=datetime.now(), periods=n_hours, freq='h')

    p0 = 2000.0
    dt = 1 / (365 * 24)
    drift = 0.05
    vol = 0.60
    returns = np.random.normal(drift * dt, vol * np.sqrt(dt), n_hours)

    for i in range(1, n_hours):
        if abs(returns[i-1]) > 3 * vol * np.sqrt(dt):
            returns[i] *= 1.5

    prices = p0 * np.exp(np.cumsum(returns))

    funding_8h = np.zeros(n_hours)
    for i in range(0, n_hours, 8):
        funding_8h[i:min(i+8, n_hours)] = np.random.normal(0.00008, 0.0002)

    base_vol = 30_000_000
    vol_multiplier = 1 + 10 * np.abs(returns) / (vol * np.sqrt(dt))
    hourly_volume = (base_vol / 24) * vol_multiplier * np.random.lognormal(0, 0.3, n_hours)

    tvl = 50_000_000 + np.cumsum(np.random.normal(0, 500_000, n_hours))
    tvl = np.maximum(tvl, 20_000_000)

    pool_df = pd.DataFrame({
        'price': prices,
        'volumeUSD': hourly_volume,
        'feesUSD': hourly_volume * 0.0005,
        'tick': [UniswapV3Math.price_to_tick(p) for p in prices],
        'tvlUSD': tvl
    }, index=idx)

    funding_df = pd.DataFrame({'fundingRate': funding_8h}, index=idx)

    return pool_df, funding_df

@dataclass
class Config:
    pool_fee_rate: float = 0.0005
    binance_taker_fee: float = 0.0004
    rebalance_mode: Literal['threshold', 'periodic'] = 'threshold'
    rebalance_threshold: float = 0.05
    initial_capital_usd: float = 100_000
    lower_price: float = 1800.0
    upper_price: float = 2200.0
    slippage_bps: float = 1.0

class Backtest:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.tick_lower = UniswapV3Math.price_to_tick(cfg.lower_price)
        self.tick_upper = UniswapV3Math.price_to_tick(cfg.upper_price)

    def _get_funding(self, funding_data: pd.DataFrame, ts: pd.Timestamp) -> float:
        """Get nearest funding rate for timestamp."""
        if funding_data.empty:
            return 0.0001
        try:
            # Use asof to get nearest previous or exact match
            val = funding_data['fundingRate'].asof(ts)
            if pd.isna(val):
                return funding_data['fundingRate'].iloc[0]
            return val
        except:
            return 0.0001

    def run(self, pool_data: pd.DataFrame, funding_data: pd.DataFrame) -> pd.DataFrame:
        df = pool_data.copy()
        bar_hours = (df.index[1] - df.index[0]).total_seconds() / 3600

        p0 = df['price'].iloc[0]
        eth_alloc = (self.cfg.initial_capital_usd / 2) / p0
        usdc_alloc = self.cfg.initial_capital_usd / 2
        liquidity = UniswapV3Math.get_liquidity(
            UniswapV3Math.price_to_tick(p0), self.tick_lower, self.tick_upper,
            eth_alloc, usdc_alloc
        )

        avg_tvl = df['tvlUSD'].mean()
        our_share = min(self.cfg.initial_capital_usd / avg_tvl, 0.5)
        print(f"Estimated pool share: {our_share*100:.4f}%")

        hedge_eth = 0.0
        hedge_avg_price = 0.0
        hedge_realized = 0.0
        cum_fees = cum_binance = cum_funding = cum_slippage = 0.0
        last_rebal = 0
        results = []

        for i, (ts, row) in enumerate(df.iterrows()):
            price = row['price']
            tick = int(row['tick'])

            eth_amt, usdc_amt = UniswapV3Math.get_amounts(tick, self.tick_lower, self.tick_upper, liquidity)
            lp_value = eth_amt * price + usdc_amt
            delta_eth = eth_amt
            in_range = self.tick_lower <= tick < self.tick_upper

            fees = 0.0
            if in_range:
                fees = row['feesUSD'] * our_share
                cum_fees += fees

            rate_8h = self._get_funding(funding_data, ts)
            rate_period = rate_8h * (bar_hours / 8)
            if abs(hedge_eth) > 0:
                funding_pnl = -hedge_eth * price * rate_period
                cum_funding += funding_pnl
            else:
                funding_pnl = 0.0

            target = -delta_eth
            should = False
            if i == 0:
                should = True
            elif abs(hedge_eth) > 0 and abs(target - hedge_eth) / abs(hedge_eth) > self.cfg.rebalance_threshold:
                should = True

            if should:
                trade = target - hedge_eth
                if abs(trade) > 1e-12:
                    notional = abs(trade) * price
                    cum_binance += notional * self.cfg.binance_taker_fee
                    cum_slippage += notional * (self.cfg.slippage_bps / 10000)

                    new_hedge = hedge_eth + trade
                    if abs(new_hedge) < 1e-12:
                        hedge_avg_price = 0.0
                    else:
                        hedge_avg_price = price if abs(hedge_eth) == 0 else (hedge_eth * hedge_avg_price + trade * price) / new_hedge
                    hedge_eth = new_hedge
                    last_rebal = i

            hedge_unreal = hedge_eth * (price - hedge_avg_price) if abs(hedge_eth) > 0 else 0.0
            lp_pnl = lp_value - self.cfg.initial_capital_usd
            total = lp_pnl + hedge_realized + hedge_unreal + cum_fees + cum_funding - cum_binance - cum_slippage

            results.append({
                'timestamp': ts, 'price': price, 'in_range': in_range,
                'lp_value': lp_value, 'delta_eth': delta_eth, 'hedge_eth': hedge_eth,
                'cum_fees': cum_fees, 'cum_funding': cum_funding,
                'cum_binance': cum_binance, 'cum_slippage': cum_slippage,
                'lp_pnl': lp_pnl, 'total_pnl': total, 'rebalanced': should and i > 0
            })

        return pd.DataFrame(results).set_index('timestamp')

def plot_results(results: pd.DataFrame, cfg: Config):
    fig, axes = plt.subplots(3, 1, figsize=(14, 12), sharex=True,
                             gridspec_kw={'height_ratios': [1.5, 1, 1]})

    ax = axes[0]
    ax.plot(results.index, results['price'], color='#636EFA', lw=0.8, label='ETH Price')
    ax.axhline(y=cfg.lower_price, color='green', ls='--', alpha=0.7, label=f'Lower ${cfg.lower_price}')
    ax.axhline(y=cfg.upper_price, color='red', ls='--', alpha=0.7, label=f'Upper ${cfg.upper_price}')
    ax.fill_between(results.index, cfg.lower_price, cfg.upper_price, 
                    where=results['in_range'], alpha=0.1, color='green')
    ax.set_ylabel('Price (USDC/ETH)')
    ax.set_title('Uniswap V3 LP + Binance Perp Delta-Hedge Backtest', fontweight='bold')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[1]
    ax.plot(results.index, results['cum_fees'], color='#00CC96', lw=1.5, label='LP Fees')
    ax.plot(results.index, results['cum_funding'], color='#EF553B', lw=1.5, label='Funding P&L')
    ax.plot(results.index, -results['cum_binance'], color='#AB63FA', lw=1.5, label='Binance Fees')
    ax.plot(results.index, results['total_pnl'], color='#FFA15A', lw=2, label='Net P&L')
    ax.axhline(y=0, color='black', lw=0.5)
    ax.set_ylabel('Cumulative USD')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)

    ax = axes[2]
    ax.plot(results.index, results['delta_eth'], color='#636EFA', lw=1, label='LP Delta')
    ax.plot(results.index, -results['hedge_eth'], color='#EF553B', lw=1, label='Hedge Size')
    ax.fill_between(results.index, 0, results['delta_eth'], alpha=0.2, color='#636EFA')
    ax.set_ylabel('ETH Amount')
    ax.set_xlabel('Date')
    ax.legend(loc='upper left', fontsize=9)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('/mnt/agents/output/backtest_demo.png', dpi=150, bbox_inches='tight')
    plt.show()

if __name__ == "__main__":
    print("=" * 60)
    print("Uniswap V3 Delta-Hedge Backtest — Self-Contained Demo")
    print("=" * 60)

    print("\nGenerating 90 days of realistic synthetic data...")
    pool_data, funding_data = generate_realistic_data(days=90, seed=42)

    cfg = Config(
        lower_price=1800, upper_price=2200,
        initial_capital_usd=100_000,
        rebalance_threshold=0.05, slippage_bps=1.0
    )

    print(f"\nLP Range: ${cfg.lower_price} - ${cfg.upper_price}")
    print(f"Capital: ${cfg.initial_capital_usd:,.0f}")
    print(f"Rebalance: {cfg.rebalance_threshold*100:.0f}% delta drift threshold")

    print("\nRunning backtest...")
    engine = Backtest(cfg)
    results = engine.run(pool_data, funding_data)

    final = results.iloc[-1]
    days = (results.index[-1] - results.index[0]).days

    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Duration: {days} days")
    print(f"Time in range: {results['in_range'].mean()*100:.1f}%")
    print(f"Rebalances: {results['rebalanced'].sum()}")
    print(f"\nLP Fees Earned:      ${final['cum_fees']:>12,.2f}")
    print(f"Funding P&L:         ${final['cum_funding']:>12,.2f}")
    print(f"Binance Fees:        ${final['cum_binance']:>12,.2f}")
    print(f"Slippage:            ${final['cum_slippage']:>12,.2f}")
    hedge_cost = abs(final['cum_funding']) + final['cum_binance'] + final['cum_slippage']
    print(f"Total Hedge Costs:   ${hedge_cost:>12,.2f}")
    print(f"\nNet P&L:             ${final['total_pnl']:>12,.2f}")
    print(f"Net APR:             {(final['total_pnl']/cfg.initial_capital_usd)/(days/365)*100:>12,.2f}%")
    print(f"\nFees > Hedge Costs?  {'YES ✓' if final['cum_fees'] > hedge_cost else 'NO ✗'}")
    print(f"{'='*60}")

    plot_results(results, cfg)

    results.to_csv('/mnt/agents/output/demo_results.csv')
    print("\nSaved: demo_results.csv, backtest_demo.png")
