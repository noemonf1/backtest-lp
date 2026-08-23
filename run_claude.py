#!/usr/bin/env python3
"""
uni_v3_hedged_lp.py
===================

Do Uniswap v3 LP fees cover the cost of holding the position delta-hedged?

Pool   : Uniswap v3 ETH/USDC 0.05% (mainnet, 0x88e6A0c2dDD26FEEb64F039a2c41296FcB3f5640)
Hedge  : Binance USD-M ETHUSDT perpetual

The backtest is built around the exact identity for a delta-hedged LP:

    Net PnL = Fees - LVR - Funding - HedgeExecution - Gas - DiscreteHedgeError

Fees and LVR are both measured *directly from swap-level data* - no volatility
estimate, no closed-form assumption. The closed form (sigma^2 * L * sqrt(P) / 4)
is computed alongside purely as a sanity check.

Sign conventions
----------------
Pool frame (canonical Uniswap):  token0 = USDC (6dp), token1 = WETH (18dp).
  P_raw   = token1_raw / token0_raw = (sqrtPriceX96 / 2^96)^2
  P_eth   = 10^(dec1-dec0) / P_raw          # USDC per ETH, the human price
  => ETH price and P_raw move in OPPOSITE directions.
     tickLower  <->  UPPER ETH price bound.  This is the #1 footgun; all
     conversions go through raw_from_eth_price() so it is handled in one place.

Swap event amounts are signed from the POOL's perspective: positive = token
flowed into the pool (taker paid it), negative = token left the pool.

Data inputs
-----------
1. Swaps (parquet or csv). Canonical columns after normalisation:
       block_number, block_time (UTC), amount0, amount1 (raw ints, signed),
       sqrt_price_x96, liquidity, tick
   Accepted source schemas: cryo, Dune (uniswap_v3_ethereum.Pair_evt_Swap),
   Allium, or already-canonical. See normalise_swaps().
   Do NOT use the subgraph's hourly/daily aggregates.

2. CEX price. Binance Data Vision monthly zips:
       futures/um/monthly/aggTrades/ETHUSDT/      <- recommended, exact
       futures/um/monthly/klines/ETHUSDT/1m/      <- fast fallback, noisier LVR
   aggTrades is resampled to 1s on load.

3. Funding:
       futures/um/monthly/fundingRate/ETHUSDT/

Run `--demo` to generate a synthetic pool + CEX tape and exercise every code
path, including the accounting-identity self-check. Useful for validating the
attribution engine before you point it at 40M real swaps.

Usage
-----
  python uni_v3_hedged_lp.py --demo --sweep
  python uni_v3_hedged_lp.py --swaps swaps.parquet --cex aggtrades.parquet \
      --funding funding.csv --capital 1000000 --range-width 0.15 --hedge-band 0.03
  python uni_v3_hedged_lp.py --download 2024-01 2024-06   # fetch Binance data
"""

from __future__ import annotations

import argparse
import io
import math
import os
import sys
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Optional

import numpy as np
import pandas as pd

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

Q96 = 2**96
DEC0 = 6  # USDC
DEC1 = 18  # WETH
SCALE = 10 ** (DEC1 - DEC0)  # 1e12
FEE_TIER = 0.0005  # 0.05%
TICK_SPACING = 10
SECONDS_PER_YEAR = 365 * 24 * 3600

BINANCE_BASE = "https://data.binance.vision/data/futures/um/monthly"


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


@dataclass
class Config:
    # --- LP leg ---
    capital_usd: float = 1_000_000.0  # capital deployed into the LP position
    range_width: float = 0.15  # +/- fraction around spot for the range
    reposition: bool = True  # re-centre range when price exits
    reposition_buffer_hours: float = 6.0  # must be out of range this long
    gas_usd_per_reposition: float = 40.0
    lp_rebalance_swap_bps: float = 5.0  # cost to re-ratio tokens on reposition

    # --- Hedge leg ---
    hedge_band: float = 0.03  # rebalance when |resid| > band * max_delta
    taker_fee_bps: float = 4.5  # Binance USD-M taker (VIP0 = 5.0, 4.5 w/ BNB)
    half_spread_bps: float = 0.5  # used when only klines are available
    impact_coef_bps_per_100k: float = 0.8  # linear impact on rebalance clips
    leverage: float = 4.0  # margin = max_notional / leverage
    margin_buffer: float = 1.5  # extra collateral multiple for liq safety

    # --- Mechanics ---
    grid_seconds: int = 60  # hedge decision grid
    markout_seconds: int = 0  # 0 = mark at block time; e.g. 300 for 5min
    fee_tier: float = FEE_TIER
    tick_spacing: int = TICK_SPACING

    def __post_init__(self):
        if not 0 < self.range_width < 1:
            raise ValueError("range_width must be in (0,1)")


# --------------------------------------------------------------------------- #
# Uniswap v3 math  (all in the canonical token0/token1 frame)
# --------------------------------------------------------------------------- #


def raw_from_eth_price(p_eth: float | np.ndarray):
    """USDC-per-ETH  ->  pool raw price (token1/token0)."""
    return SCALE / p_eth


def eth_price_from_raw(p_raw: float | np.ndarray):
    return SCALE / p_raw


def sqrt_from_eth_price(p_eth: float | np.ndarray):
    return np.sqrt(raw_from_eth_price(p_eth))


def eth_price_from_sqrtx96(sqrt_x96: float | np.ndarray):
    s = np.asarray(sqrt_x96, dtype=float) / Q96
    return SCALE / (s * s)


def tick_from_raw(p_raw: float) -> int:
    return int(math.floor(math.log(p_raw) / math.log(1.0001)))


def raw_from_tick(tick: int | np.ndarray):
    return np.power(1.0001, tick)


def align_tick(tick: int, spacing: int, up: bool) -> int:
    return (math.ceil(tick / spacing) if up else math.floor(tick / spacing)) * spacing


@dataclass
class Position:
    """A v3 position, parameterised in the pool frame but constructed from an
    ETH-price band."""

    tick_lower: int  # pool frame: corresponds to the UPPER eth price
    tick_upper: int  # pool frame: corresponds to the LOWER eth price
    liquidity: float  # raw L
    opened_at: pd.Timestamp
    entry_eth_price: float

    @property
    def sqrt_a(self) -> float:
        return float(math.sqrt(raw_from_tick(self.tick_lower)))

    @property
    def sqrt_b(self) -> float:
        return float(math.sqrt(raw_from_tick(self.tick_upper)))

    @property
    def eth_price_upper(self) -> float:
        return eth_price_from_raw(raw_from_tick(self.tick_lower))

    @property
    def eth_price_lower(self) -> float:
        return eth_price_from_raw(raw_from_tick(self.tick_upper))

    @property
    def max_delta_eth(self) -> float:
        """ETH held when fully converted (price at/below the lower ETH bound)."""
        return self.liquidity * (self.sqrt_b - self.sqrt_a) / 10**DEC1

    # -- geometry ----------------------------------------------------------- #
    def amounts_raw(self, sqrt_p: float | np.ndarray):
        s = np.clip(sqrt_p, self.sqrt_a, self.sqrt_b)
        amt0 = self.liquidity * (1.0 / s - 1.0 / self.sqrt_b)  # USDC raw
        amt1 = self.liquidity * (s - self.sqrt_a)  # WETH raw
        return amt0, amt1

    def delta_eth(self, eth_price: float | np.ndarray):
        """Position delta = ETH units held. This is exactly dV/dP_eth."""
        _, amt1 = self.amounts_raw(sqrt_from_eth_price(eth_price))
        return amt1 / 10**DEC1

    def value_usd(self, eth_price: float | np.ndarray):
        amt0, amt1 = self.amounts_raw(sqrt_from_eth_price(eth_price))
        return amt0 / 10**DEC0 + (amt1 / 10**DEC1) * eth_price

    def in_range(self, tick: int | np.ndarray):
        return (tick >= self.tick_lower) & (tick < self.tick_upper)


def build_position(
    eth_price: float,
    cfg: Config,
    when: pd.Timestamp,
    capital_usd: Optional[float] = None,
) -> Position:
    """Symmetric +/- range_width band around `eth_price`, sized to `capital_usd`."""
    capital = cfg.capital_usd if capital_usd is None else capital_usd
    p_hi_eth = eth_price * (1 + cfg.range_width)
    p_lo_eth = eth_price * (1 - cfg.range_width)

    # NOTE the inversion: high ETH price -> low raw price -> low tick
    t_lo = align_tick(
        tick_from_raw(raw_from_eth_price(p_hi_eth)), cfg.tick_spacing, up=False
    )
    t_hi = align_tick(
        tick_from_raw(raw_from_eth_price(p_lo_eth)), cfg.tick_spacing, up=True
    )
    if t_hi <= t_lo:
        t_hi = t_lo + cfg.tick_spacing

    probe = Position(t_lo, t_hi, 1.0, when, eth_price)
    value_per_unit_l = probe.value_usd(eth_price)
    L = capital / value_per_unit_l
    return Position(t_lo, t_hi, L, when, eth_price)


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #

_SWAP_ALIASES = {
    "block_number": ["block_number", "blockNumber", "block_num", "evt_block_number"],
    "block_time": ["block_time", "block_timestamp", "timestamp", "evt_block_time"],
    "amount0": ["amount0", "amount_0"],
    "amount1": ["amount1", "amount_1"],
    "sqrt_price_x96": ["sqrt_price_x96", "sqrtPriceX96", "sqrtpricex96"],
    "liquidity": ["liquidity"],
    "tick": ["tick"],
}


def normalise_swaps(df: pd.DataFrame) -> pd.DataFrame:
    """Map cryo / Dune / Allium column names onto the canonical schema."""
    lower = {c.lower(): c for c in df.columns}
    out = {}
    for canon, aliases in _SWAP_ALIASES.items():
        for a in aliases:
            if a.lower() in lower:
                out[canon] = df[lower[a.lower()]]
                break
        else:
            raise KeyError(
                f"swap data is missing a column for '{canon}'; "
                f"saw {list(df.columns)}"
            )
    s = pd.DataFrame(out)
    s["block_time"] = pd.to_datetime(s["block_time"], utc=True)
    for c in ("amount0", "amount1", "sqrt_price_x96", "liquidity"):
        s[c] = pd.to_numeric(s[c], errors="raise").astype(float)
    s["tick"] = pd.to_numeric(s["tick"]).astype(int)
    s = s.sort_values("block_time").reset_index(drop=True)
    return s


def load_swaps(path: str) -> pd.DataFrame:
    if path.endswith(".parquet"):
        df = pd.read_parquet(path)
    else:
        df = pd.read_csv(path)
    return normalise_swaps(df)


def _epoch_to_datetime(ts: pd.Series) -> pd.DatetimeIndex:
    """Infer s / ms / us / ns from magnitude. Binance mixes ms and us across
    dataset versions, and pandas>=3 hands you microsecond int64 by default."""
    ts = pd.to_numeric(ts, errors="coerce")
    m = float(np.nanmedian(ts.to_numpy(dtype=float)))
    for unit, hi in (("s", 1e11), ("ms", 1e14), ("us", 1e17), ("ns", 1e20)):
        if abs(m) < hi:
            return pd.to_datetime(ts, unit=unit, utc=True)
    raise ValueError(f"cannot infer epoch unit from magnitude {m:g}")


def load_cex_prices(path: str) -> pd.Series:
    """Return a 1s-indexed (UTC) ETH price series.

    Accepts Binance aggTrades or klines dumps, or a 2-column csv/parquet of
    (timestamp, price).
    """
    df = pd.read_parquet(path) if path.endswith(".parquet") else pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}

    if "price" in cols and ("transact_time" in cols or "time" in cols or "ts" in cols):
        tcol = cols.get("transact_time") or cols.get("time") or cols.get("ts")
        ts, px = df[tcol], df[cols["price"]]
    elif "close" in cols:  # klines
        tcol = cols.get("close_time") or cols.get("open_time") or df.columns[0]
        ts, px = df[tcol], df[cols["close"]]
    else:  # headerless aggTrades
        ts, px = df.iloc[:, 5], df.iloc[:, 1]

    idx = _epoch_to_datetime(ts)
    s = pd.Series(pd.to_numeric(px).values, index=idx).sort_index()
    return s.resample("1s").last().ffill()


def load_funding(path: str) -> pd.Series:
    df = pd.read_csv(path)
    cols = {c.lower(): c for c in df.columns}
    tcol = cols.get("calc_time") or cols.get("fundingtime") or df.columns[0]
    rcol = cols.get("last_funding_rate") or cols.get("fundingrate") or df.columns[-1]
    idx = _epoch_to_datetime(df[tcol])
    return pd.Series(pd.to_numeric(df[rcol]).values, index=idx).sort_index()


def download_binance(
    kind: str, months: Iterable[str], symbol="ETHUSDT", interval="1m", out_dir="data"
) -> list[str]:
    """kind in {aggTrades, fundingRate, klines, bookTicker}. Needs `requests`."""
    import requests

    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for m in months:
        if kind == "klines":
            url = (
                f"{BINANCE_BASE}/klines/{symbol}/{interval}/{symbol}-{interval}-{m}.zip"
            )
            dest = os.path.join(out_dir, f"{symbol}-klines-{interval}-{m}.csv")
        else:
            url = f"{BINANCE_BASE}/{kind}/{symbol}/{symbol}-{kind}-{m}.zip"
            dest = os.path.join(out_dir, f"{symbol}-{kind}-{m}.csv")
        if os.path.exists(dest):
            paths.append(dest)
            continue
        print(f"  GET {url}", file=sys.stderr)
        r = requests.get(url, timeout=180)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            name = z.namelist()[0]
            with z.open(name) as fh, open(dest, "wb") as out:
                out.write(fh.read())
        paths.append(dest)
    return paths


# --------------------------------------------------------------------------- #
# Synthetic tape (for validation / smoke tests)
# --------------------------------------------------------------------------- #


def make_demo_data(
    days: int = 60,
    s0: float = 3000.0,
    vol_annual: float = 0.65,
    pool_tvl_usd: float = 2.0e8,
    seed: int = 7,
):
    """GBM CEX tape + a pool driven by arbitrageurs and Poisson noise flow.

    The pool is a single constant-liquidity level (fine for validating the
    attribution engine; real data supplies the real tick map).
    """
    rng = np.random.default_rng(seed)
    n_sec = days * 24 * 3600
    dt = 1.0 / SECONDS_PER_YEAR
    shocks = rng.normal(0, vol_annual * math.sqrt(dt), n_sec)
    px = s0 * np.exp(np.cumsum(shocks - 0.5 * vol_annual**2 * dt))
    t0 = pd.Timestamp("2024-01-01", tz="UTC")
    cex = pd.Series(px, index=pd.date_range(t0, periods=n_sec, freq="1s"))

    # pool liquidity: L such that a full-range-ish position is worth pool_tvl
    probe = build_position(
        s0,
        Config(capital_usd=pool_tvl_usd, range_width=0.30),
        t0,
        capital_usd=pool_tvl_usd,
    )
    L_pool = probe.liquidity

    rows = []
    sqrt_p = sqrt_from_eth_price(s0)
    block_idx = np.arange(0, n_sec, 12)
    noise_per_block = rng.poisson(0.55, len(block_idx))
    noise_notional = rng.lognormal(10.0, 1.4, noise_per_block.sum())
    k = 0

    def emit(sqrt_from, sqrt_to, ts, blk):
        """Move pool from sqrt_from -> sqrt_to at constant L; record the swap."""
        d1 = L_pool * (sqrt_to - sqrt_from)  # token1 delta (raw)
        d0 = L_pool * (1.0 / sqrt_to - 1.0 / sqrt_from)
        # input side is the positive one; gross it up for the fee
        if d1 > 0:
            a1, a0 = d1 / (1 - FEE_TIER), d0
        else:
            a0, a1 = d0 / (1 - FEE_TIER), d1
        rows.append((blk, ts, a0, a1, sqrt_to * Q96, L_pool, tick_from_raw(sqrt_to**2)))

    for j, i in enumerate(block_idx):
        ts = cex.index[i]
        p_cex = px[i]
        # --- noise flow ---
        for _ in range(noise_per_block[j]):
            notional = noise_notional[k]
            k += 1
            side = 1 if rng.random() < 0.5 else -1
            eth_qty = side * notional / eth_price_from_raw(sqrt_p**2)
            d1_raw = -eth_qty * 10**DEC1  # taker buys eth => leaves pool
            s_new = sqrt_p + d1_raw / L_pool
            if s_new <= 0:
                continue
            emit(sqrt_p, s_new, ts, i // 12)
            sqrt_p = s_new
        # --- arbitrage back to the no-arb band ---
        p_pool = eth_price_from_raw(sqrt_p**2)
        lo, hi = p_cex * (1 - FEE_TIER), p_cex * (1 + FEE_TIER)
        if p_pool < lo or p_pool > hi:
            target = lo if p_pool < lo else hi
            s_new = sqrt_from_eth_price(target)
            emit(sqrt_p, s_new, ts, i // 12)
            sqrt_p = s_new

    swaps = pd.DataFrame(
        rows,
        columns=[
            "block_number",
            "block_time",
            "amount0",
            "amount1",
            "sqrt_price_x96",
            "liquidity",
            "tick",
        ],
    )
    swaps["block_time"] = pd.to_datetime(swaps["block_time"], utc=True)

    stamps = pd.date_range(t0, cex.index[-1], freq="8h")
    funding = pd.Series(rng.normal(0.00008, 0.00012, len(stamps)), index=stamps)
    return swaps, cex, funding


# --------------------------------------------------------------------------- #
# Attribution engine: fees + LVR, per swap
# --------------------------------------------------------------------------- #


def attribute_swaps(
    swaps: pd.DataFrame, cex: pd.Series, pos_frame: pd.DataFrame, cfg: Config
) -> pd.DataFrame:
    """For every swap, compute the LP's fee income and adverse-selection cost.

    pos_frame: index = position epoch start, columns = tick_lower, tick_upper,
               liquidity. Lets a repositioning schedule be applied vectorised.

    Returns swaps enriched with:
      fee_usd        fees accruing to OUR liquidity, dilution-adjusted
      lvr_usd        our share of the taker's markout profit, fee-EXCLUSIVE
      markout_usd    our share of total LP markout  (== fee_usd - lvr_usd)
      share          L_you / (L_active + L_you)
    """
    s = swaps.copy()
    n = len(s)

    # -- align each swap to the position epoch in force ---------------------- #
    # searchsorted, not reindex: many swaps share a block timestamp
    j = (
        np.searchsorted(pos_frame.index.values, s["block_time"].values, side="right")
        - 1
    )
    valid = j >= 0
    s = s.loc[valid].reset_index(drop=True)
    j = j[valid]
    s["tick_lower"] = pos_frame["tick_lower"].to_numpy()[j]
    s["tick_upper"] = pos_frame["tick_upper"].to_numpy()[j]
    s["L_you"] = pos_frame["liquidity"].to_numpy()[j]
    if s.empty:
        return s

    # -- CEX mark (last quote at or before the mark time) -------------------- #
    mark_time = (s["block_time"] + pd.Timedelta(seconds=cfg.markout_seconds)).values
    k = np.searchsorted(cex.index.values, mark_time, side="right") - 1
    ok = k >= 0
    s = s.loc[ok].reset_index(drop=True)
    s["p_cex"] = cex.to_numpy()[k[ok]]
    s = s.dropna(subset=["p_cex"]).reset_index(drop=True)

    a0, a1 = s["amount0"].to_numpy(), s["amount1"].to_numpy()
    p_cex = s["p_cex"].to_numpy()

    # -- our share of the pool, after our own dilution ----------------------- #
    inr = (s["tick"].to_numpy() >= s["tick_lower"].to_numpy()) & (
        s["tick"].to_numpy() < s["tick_upper"].to_numpy()
    )
    L_act, L_you = s["liquidity"].to_numpy(), s["L_you"].to_numpy()
    share = np.where(inr, L_you / (L_act + L_you), 0.0)
    s["in_range"] = inr
    s["share"] = share

    # -- fees: charged on the gross input token ------------------------------ #
    fee0 = np.where(a0 > 0, a0 * cfg.fee_tier / 10**DEC0, 0.0)  # USDC
    fee1 = np.where(a1 > 0, a1 * cfg.fee_tier / 10**DEC1 * p_cex, 0.0)  # WETH->USD
    s["fee_usd"] = (fee0 + fee1) * share

    # -- LVR: taker profit at CEX marks, measured on the FEE-NET execution --- #
    # (fee-inclusive would double count against fee_usd)
    a0_net = np.where(a0 > 0, a0 * (1 - cfg.fee_tier), a0)
    a1_net = np.where(a1 > 0, a1 * (1 - cfg.fee_tier), a1)
    eth_qty = np.abs(a1_net) / 10**DEC1
    usd_qty = np.abs(a0_net) / 10**DEC0
    with np.errstate(divide="ignore", invalid="ignore"):
        p_exec = np.where(eth_qty > 0, usd_qty / np.maximum(eth_qty, 1e-30), np.nan)
    taker_bought_eth = a1_net < 0  # ETH left the pool
    taker_pnl = np.where(taker_bought_eth, 1.0, -1.0) * eth_qty * (p_cex - p_exec)
    s["lvr_usd"] = np.nan_to_num(taker_pnl) * share

    # -- gross markout (fee-inclusive) = the identity check ------------------ #
    eth_g = np.abs(a1) / 10**DEC1
    usd_g = np.abs(a0) / 10**DEC0
    with np.errstate(divide="ignore", invalid="ignore"):
        p_exec_g = np.where(eth_g > 0, usd_g / np.maximum(eth_g, 1e-30), np.nan)
    taker_pnl_g = np.where(a1 < 0, 1.0, -1.0) * eth_g * (p_cex - p_exec_g)
    s["markout_usd"] = -np.nan_to_num(taker_pnl_g) * share

    return s


# --------------------------------------------------------------------------- #
# Position schedule (range repositioning)
# --------------------------------------------------------------------------- #


def build_position_schedule(
    cex: pd.Series, cfg: Config
) -> tuple[pd.DataFrame, list[Position]]:
    """Walk the CEX tape, re-centring the range when price has been outside it
    for longer than reposition_buffer_hours. Returns (frame, positions)."""
    grid = cex.resample(f"{cfg.grid_seconds}s").last().ffill().dropna()
    positions: list[Position] = []
    rows = []
    capital = cfg.capital_usd

    pos = build_position(float(grid.iloc[0]), cfg, grid.index[0], capital)
    positions.append(pos)
    rows.append((grid.index[0], pos.tick_lower, pos.tick_upper, pos.liquidity))

    if cfg.reposition:
        buf = pd.Timedelta(hours=cfg.reposition_buffer_hours)
        out_since: Optional[pd.Timestamp] = None
        for t, p in grid.items():
            outside = not (pos.eth_price_lower <= p <= pos.eth_price_upper)
            if not outside:
                out_since = None
                continue
            if out_since is None:
                out_since = t
            elif t - out_since >= buf:
                # crystallise: value at exit, minus swap cost to re-ratio, minus gas
                capital = pos.value_usd(p)
                capital -= capital * cfg.lp_rebalance_swap_bps / 1e4
                capital -= cfg.gas_usd_per_reposition
                pos = build_position(float(p), cfg, t, capital)
                positions.append(pos)
                rows.append((t, pos.tick_lower, pos.tick_upper, pos.liquidity))
                out_since = None

    frame = pd.DataFrame(
        rows, columns=["t", "tick_lower", "tick_upper", "liquidity"]
    ).set_index("t")
    return frame, positions


# --------------------------------------------------------------------------- #
# Hedge engine
# --------------------------------------------------------------------------- #


def simulate_hedge(
    cex: pd.Series,
    pos_frame: pd.DataFrame,
    positions: list[Position],
    funding: pd.Series,
    cfg: Config,
) -> pd.DataFrame:
    """Band-based delta hedge on the perp. Returns a per-grid-step frame."""
    grid = cex.resample(f"{cfg.grid_seconds}s").last().ffill().dropna()
    t = grid.index
    p = grid.to_numpy()

    # position parameters on the grid
    pf = pos_frame.reindex(pos_frame.index.union(t)).ffill().reindex(t)
    sa = np.sqrt(raw_from_tick(pf["tick_lower"].to_numpy()))
    sb = np.sqrt(raw_from_tick(pf["tick_upper"].to_numpy()))
    L = pf["liquidity"].to_numpy()

    sp = np.clip(sqrt_from_eth_price(p), sa, sb)
    lp_delta = L * (sp - sa) / 10**DEC1  # ETH held by the LP
    max_delta = L * (sb - sa) / 10**DEC1

    band_abs = cfg.hedge_band * max_delta

    hedge = np.zeros(len(p))  # signed perp position in ETH (negative = short)
    trades = np.zeros(len(p))
    h = -lp_delta[0]
    for i in range(len(p)):
        resid = lp_delta[i] + h
        if abs(resid) > band_abs[i] or i == 0:
            trades[i] = -resid
            h += trades[i]
        hedge[i] = h

    # execution cost: half-spread + taker fee + linear impact
    notional = np.abs(trades) * p
    cost = notional * (cfg.half_spread_bps + cfg.taker_fee_bps) / 1e4
    cost += notional * (notional / 1e5) * cfg.impact_coef_bps_per_100k / 1e4

    # hedge mark-to-market
    dp = np.diff(p, prepend=p[0])
    hedge_pnl = np.concatenate([[0.0], hedge[:-1] * dp[1:]])

    out = pd.DataFrame(
        {
            "eth_price": p,
            "lp_delta": lp_delta,
            "hedge_delta": hedge,
            "hedge_trade_eth": trades,
            "hedge_cost_usd": cost,
            "hedge_pnl_usd": hedge_pnl,
            "lp_value_usd": _lp_value(L, sa, sb, p),
        },
        index=t,
    )

    # funding: settled on the perp notional at each 8h stamp.
    # Short (negative delta) RECEIVES when the rate is positive.
    fund = np.zeros(len(out))
    if funding is not None and len(funding):
        f = funding[(funding.index >= t[0]) & (funding.index <= t[-1])]
        if len(f):
            k = np.clip(
                np.searchsorted(t.values, f.index.values, side="right") - 1,
                0,
                len(out) - 1,
            )
            cash = -(hedge[k] * p[k]) * f.to_numpy()
            np.add.at(fund, k, np.nan_to_num(cash))
    out["funding_usd"] = fund

    return out


def _lp_value(L, sa, sb, p):
    s = np.clip(sqrt_from_eth_price(p), sa, sb)
    a0 = L * (1.0 / s - 1.0 / sb)
    a1 = L * (s - sa)
    return a0 / 10**DEC0 + (a1 / 10**DEC1) * p


# --------------------------------------------------------------------------- #
# Assembly + metrics
# --------------------------------------------------------------------------- #


def run_backtest(swaps, cex, funding, cfg: Config) -> dict:
    pos_frame, positions = build_position_schedule(cex, cfg)
    att = attribute_swaps(swaps, cex, pos_frame, cfg)
    hed = simulate_hedge(cex, pos_frame, positions, funding, cfg)

    freq = f"{cfg.grid_seconds}s"
    agg = (
        att.set_index("block_time")[["fee_usd", "lvr_usd", "markout_usd"]]
        .resample(freq)
        .sum()
        .reindex(hed.index)
        .fillna(0.0)
    )

    df = hed.join(agg)
    df["gas_usd"] = 0.0
    for ts in pos_frame.index[1:]:
        i = min(df.index.searchsorted(ts), len(df) - 1)
        df.iloc[i, df.columns.get_loc("gas_usd")] += (
            cfg.gas_usd_per_reposition
            + cfg.capital_usd * cfg.lp_rebalance_swap_bps / 1e4
        )

    # --- the identity ----------------------------------------------------- #
    # delta-hedged LP PnL, path-exact:
    df["lp_pnl_usd"] = df["lp_value_usd"].diff().fillna(0.0)
    df["net_usd"] = (
        df["lp_pnl_usd"]
        + df["hedge_pnl_usd"]
        + df["fee_usd"]
        + df["funding_usd"]
        - df["hedge_cost_usd"]
        - df["gas_usd"]
    )
    # decomposition view (should track net_usd closely):
    df["decomp_usd"] = (
        df["fee_usd"]
        - df["lvr_usd"]
        + df["funding_usd"]
        - df["hedge_cost_usd"]
        - df["gas_usd"]
    )
    df["cum_net"] = df["net_usd"].cumsum()

    # --- capital base ------------------------------------------------------ #
    max_notional = float((df["hedge_delta"].abs() * df["eth_price"]).max())
    margin = max_notional / cfg.leverage * cfg.margin_buffer
    capital_base = cfg.capital_usd + margin

    days = (df.index[-1] - df.index[0]).total_seconds() / 86400
    yrs = days / 365.0
    daily = df["net_usd"].resample("1D").sum()

    def apr(x):
        return float(x) / capital_base / yrs * 100 if yrs > 0 else float("nan")

    fees = df["fee_usd"].sum()
    lvr = df["lvr_usd"].sum()

    dd = df["cum_net"] - df["cum_net"].cummax()
    summary = {
        "days": round(days, 1),
        "capital_lp_usd": cfg.capital_usd,
        "margin_usd": round(margin),
        "capital_base_usd": round(capital_base),
        "range_width": cfg.range_width,
        "hedge_band": cfg.hedge_band,
        "n_repositions": len(pos_frame) - 1,
        "n_hedge_trades": int((df["hedge_trade_eth"] != 0).sum()),
        "pct_time_in_range": round(
            100 * float(att["in_range"].mean()) if len(att) else 0.0, 1
        ),
        "mean_liquidity_share_pct": round(
            100 * float(att.loc[att["in_range"], "share"].mean()) if len(att) else 0.0,
            3,
        ),
        "fees_usd": round(fees),
        "lvr_usd": round(lvr),
        "fee_over_lvr": round(fees / lvr, 3) if lvr > 0 else float("nan"),
        "funding_usd": round(df["funding_usd"].sum()),
        "hedge_cost_usd": round(df["hedge_cost_usd"].sum()),
        "gas_reposition_usd": round(df["gas_usd"].sum()),
        "net_usd": round(df["net_usd"].sum()),
        "fee_apr_pct": round(apr(fees), 2),
        "lvr_apr_pct": round(apr(-lvr), 2),
        "funding_apr_pct": round(apr(df["funding_usd"].sum()), 2),
        "hedge_cost_apr_pct": round(apr(-df["hedge_cost_usd"].sum()), 2),
        "net_apr_pct": round(apr(df["net_usd"].sum()), 2),
        "sharpe": round(
            (
                float(daily.mean() / daily.std() * math.sqrt(365))
                if daily.std() > 0
                else float("nan")
            ),
            2,
        ),
        "max_drawdown_usd": round(float(dd.min())),
        "discrete_hedge_error_usd": round(
            float(df["net_usd"].sum() - df["decomp_usd"].sum())
        ),
        "closed_form_lvr_usd": round(_closed_form_lvr(df, cex, cfg)),
    }
    return {
        "df": df,
        "attribution": att,
        "summary": summary,
        "positions": positions,
        "pos_frame": pos_frame,
    }


def _closed_form_lvr(df: pd.DataFrame, cex: pd.Series, cfg: Config) -> float:
    """Sanity check only: integral of sigma^2 * L * sqrt(P_raw) / 4.

    Expressed in the ETH-price frame this is (1/2) * sigma^2 * P^2 * |V''(P)|.
    Realised sigma is estimated on the hedge grid.
    """
    p = df["eth_price"].to_numpy()
    r = np.diff(np.log(p))
    if len(r) < 2:
        return float("nan")
    var_per_step = float(np.var(r))
    # |V''(P_eth)| for the concentrated position, by finite difference on delta
    with np.errstate(divide="ignore", invalid="ignore"):
        gamma = np.gradient(df["lp_delta"].to_numpy(), p)  # dDelta/dP
    inst = 0.5 * np.abs(gamma) * (p**2) * var_per_step
    return float(np.nansum(inst[1:]))


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #


def sweep(
    swaps,
    cex,
    funding,
    cfg: Config,
    widths=(0.05, 0.10, 0.15, 0.25, 0.50),
    bands=(0.01, 0.02, 0.05, 0.10, 0.25),
) -> pd.DataFrame:
    rows = []
    for w in widths:
        for b in bands:
            c = Config(**{**asdict(cfg), "range_width": w, "hedge_band": b})
            try:
                res = run_backtest(swaps, cex, funding, c)
            except Exception as e:  # keep the grid going
                print(f"  ! w={w} b={b}: {e}", file=sys.stderr)
                continue
            s = res["summary"]
            rows.append(
                {
                    "range_width": w,
                    "hedge_band": b,
                    "fee_apr": s["fee_apr_pct"],
                    "lvr_apr": s["lvr_apr_pct"],
                    "funding_apr": s["funding_apr_pct"],
                    "hedge_cost_apr": s["hedge_cost_apr_pct"],
                    "net_apr": s["net_apr_pct"],
                    "fee_over_lvr": s["fee_over_lvr"],
                    "in_range_pct": s["pct_time_in_range"],
                    "sharpe": s["sharpe"],
                    "n_hedges": s["n_hedge_trades"],
                }
            )
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #


def report(res: dict, out_prefix: Optional[str] = None):
    s = res["summary"]
    w = max(len(k) for k in s)
    print("\n" + "=" * 62)
    print(" DELTA-HEDGED UNISWAP v3 LP  -  ETH/USDC 0.05% vs ETHUSDT perp")
    print("=" * 62)
    for k, v in s.items():
        print(f"  {k:<{w}}  {v:>14}")
    print("-" * 62)
    verdict = (
        "FEES COVER THE COST" if s["net_apr_pct"] > 0 else "FEES DO NOT COVER THE COST"
    )
    print(
        f"  VERDICT: {verdict}   (net {s['net_apr_pct']}% APR on "
        f"${s['capital_base_usd']:,} deployed)"
    )
    print("=" * 62 + "\n")

    if out_prefix:
        res["df"].to_csv(f"{out_prefix}_timeseries.csv")
        pd.Series(s).to_csv(f"{out_prefix}_summary.csv")
        try:
            _plot(res, f"{out_prefix}_pnl.png")
        except Exception as e:
            print(f"(plot skipped: {e})", file=sys.stderr)


def _plot(res: dict, path: str):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    df = res["df"]
    d = df.resample("1D").sum(numeric_only=True)
    fig, ax = plt.subplots(3, 1, figsize=(11, 11), sharex=True)

    ax[0].plot(df.index, df["fee_usd"].cumsum(), label="cumulative fees")
    ax[0].plot(df.index, df["lvr_usd"].cumsum(), label="cumulative LVR")
    ax[0].plot(df.index, df["cum_net"], label="net (hedged)", lw=2, color="k")
    ax[0].axhline(0, color="grey", lw=0.6)
    ax[0].legend()
    ax[0].set_ylabel("USD")
    ax[0].set_title("Fees vs LVR vs net")

    comp = pd.DataFrame(
        {
            "fees": d["fee_usd"],
            "LVR": -d["lvr_usd"],
            "funding": d["funding_usd"],
            "hedge+gas": -(d["hedge_cost_usd"] + d["gas_usd"]),
        }
    )
    bottom_pos = np.zeros(len(comp))
    bottom_neg = np.zeros(len(comp))
    for col in comp.columns:
        v = comp[col].to_numpy()
        base = np.where(v >= 0, bottom_pos, bottom_neg)
        ax[1].bar(comp.index, v, bottom=base, width=0.9, label=col)
        bottom_pos = bottom_pos + np.clip(v, 0, None)
        bottom_neg = bottom_neg + np.clip(v, None, 0)
    ax[1].plot(d.index, d["net_usd"], color="k", lw=1.2, label="net")
    ax[1].axhline(0, color="grey", lw=0.6)
    ax[1].legend(ncol=5, fontsize=8)
    ax[1].set_ylabel("USD / day")
    ax[1].set_title("Daily PnL attribution")

    ax[2].plot(df.index, df["eth_price"], color="grey", lw=0.8)
    pf = res["pos_frame"]
    ends = list(pf.index[1:]) + [df.index[-1]]
    for (t, row), t_end in zip(pf.iterrows(), ends):
        lo = eth_price_from_raw(raw_from_tick(row["tick_upper"]))
        hi = eth_price_from_raw(raw_from_tick(row["tick_lower"]))
        ax[2].hlines([lo, hi], t, t_end, color="tab:red", lw=0.9, alpha=0.7)
    ax[2].set_ylabel("ETH/USDC")
    ax[2].set_title("Price vs active range")

    fig.tight_layout()
    fig.savefig(path, dpi=120)
    print(f"  wrote {path}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--demo",
        action="store_true",
        help="run on a synthetic tape (validates the engine)",
    )
    ap.add_argument("--demo-days", type=int, default=60)
    ap.add_argument("--swaps")
    ap.add_argument("--cex")
    ap.add_argument("--funding")
    ap.add_argument(
        "--download",
        nargs=2,
        metavar=("FROM", "TO"),
        help="download Binance months, e.g. --download 2024-01 2024-06",
    )
    ap.add_argument("--capital", type=float, default=1_000_000.0)
    ap.add_argument("--range-width", type=float, default=0.15)
    ap.add_argument("--hedge-band", type=float, default=0.03)
    ap.add_argument("--markout-seconds", type=int, default=0)
    ap.add_argument("--grid-seconds", type=int, default=60)
    ap.add_argument("--no-reposition", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--out", default=None, help="prefix for csv/png output")
    a = ap.parse_args(argv)

    if a.download:
        months = pd.period_range(a.download[0], a.download[1], freq="M").strftime(
            "%Y-%m"
        )
        for kind in ("aggTrades", "fundingRate"):
            print(f"downloading {kind} ...", file=sys.stderr)
            download_binance(kind, months)
        return 0

    cfg = Config(
        capital_usd=a.capital,
        range_width=a.range_width,
        hedge_band=a.hedge_band,
        markout_seconds=a.markout_seconds,
        grid_seconds=a.grid_seconds,
        reposition=not a.no_reposition,
    )

    if a.demo:
        print(f"generating {a.demo_days}d synthetic tape ...", file=sys.stderr)
        swaps, cex, funding = make_demo_data(days=a.demo_days)
        print(
            "!! DEMO MODE: synthetic tape. The numbers below validate the\n"
            "!! engine's plumbing and the fee/LVR identity - they say NOTHING\n"
            "!! about whether the real pool is profitable.",
            file=sys.stderr,
        )
    else:
        if not (a.swaps and a.cex):
            ap.error("need --swaps and --cex (or --demo)")
        swaps = load_swaps(a.swaps)
        cex = load_cex_prices(a.cex)
        funding = load_funding(a.funding) if a.funding else pd.Series(dtype=float)

    print(f"{len(swaps):,} swaps | {cex.index[0]} -> {cex.index[-1]}", file=sys.stderr)

    res = run_backtest(swaps, cex, funding, cfg)
    report(res, a.out)

    if a.sweep:
        print("sweeping range width x hedge band ...", file=sys.stderr)
        tbl = sweep(swaps, cex, funding, cfg)
        print(tbl.to_string(index=False))
        if a.out:
            tbl.to_csv(f"{a.out}_sweep.csv", index=False)
            print(f"\n  wrote {a.out}_sweep.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
