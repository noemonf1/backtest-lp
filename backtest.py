"""Unified backtest facade for the Streamlit app.

Two engines are exposed behind a common interface:

- **swap_level** (run_claude.py): the reference implementation.
  Consumes swap-level events (cryo / Dune / Allium) + Binance aggTrades + funding.
  Path-exact fees and LVR; explicit hedge execution model (spread + taker +
  linear impact); closed-loop accounting identity with a closed-form LVR
  sanity check. Also has a synthetic demo tape for validating the engine
  without external data.

- **hourly** (kimi/uniswap_delta_hedge_backtest.py): a lightweight fallback
  that only needs Binance hourly klines + funding + an assumed pool share.
  Faster to set up when you don't yet have a swap-level dump, but coarser:
  fees are estimated from Binance volume × pool share, hedge execution is a
  flat bp slippage, no LVR attribution, no gas modelling.

Both engines produce a `BacktestResult` with the same shape so the UI doesn't
care which one ran.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import run_claude
from kimi.uniswap_delta_hedge_backtest import (
    BacktestConfig as KimiConfig,
    BinanceDataFetcher,
    DeltaHedgeBacktest,
)

DATA_DIR = Path(__file__).parent / "data"
ETH_USDC_POOL = "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"


# --------------------------------------------------------------------------- #
# Unified result envelope
# --------------------------------------------------------------------------- #


@dataclass
class BacktestResult:
    """What every engine returns. Keeps the UI engine-agnostic."""

    engine: str  # "swap_level" | "hourly"
    summary: dict  # scalar KPIs (see _normalise_* below for the canonical keys)
    timeseries: pd.DataFrame  # time-indexed; canonical columns:
    #   price, lp_value, lp_delta, hedge_delta,
    #   fee_usd, lvr_usd, funding_usd, hedge_cost_usd, gas_usd, net_usd, cum_net
    attribution: Optional[pd.DataFrame] = None  # per-swap (swap_level only)
    extras: dict = field(default_factory=dict)  # engine-specific goodies


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #


@dataclass
class Assumptions:
    """Everything the UI can twiddle. Superset of what either engine needs;
    each engine picks the fields it cares about."""

    # LP leg
    capital_usd: float = 1_000_000.0
    range_width: float = 0.15  # ± fraction around spot (swap_level, and used to
    # derive lower/upper for hourly if user doesn't override)
    lower_price: Optional[float] = None  # hourly engine only; overrides range_width
    upper_price: Optional[float] = None
    reposition: bool = True
    reposition_buffer_hours: float = 6.0
    gas_usd_per_reposition: float = 40.0
    lp_rebalance_swap_bps: float = 5.0

    # Hedge leg
    hedge_band: float = 0.03  # swap_level: fraction of max delta
    rebalance_threshold_pct: float = 0.05  # hourly: fraction of |delta|
    rebalance_mode: str = "threshold"  # hourly: "threshold" | "periodic"
    rebalance_period_h: int = 24  # hourly: periodic mode
    taker_fee_bps: float = 4.5  # swap_level
    binance_taker_fee: float = 0.0004  # hourly (fraction, not bps)
    half_spread_bps: float = 0.5
    impact_bps_per_100k: float = 0.8
    slippage_bps: float = 1.0  # hourly
    leverage: float = 4.0
    margin_buffer: float = 1.5

    # Mechanics
    grid_seconds: int = 60
    markout_seconds: int = 0
    pool_share: float = 0.001  # hourly only: our share of pool active liquidity
    pool_volume_multiplier: float = 0.08  # hourly only: pool vol as fraction of
    # Binance quote volume (rough proxy; ~8% is a reasonable default for ETH/USDC)

    # Fee tier
    fee_tier: float = 0.0005

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Assumptions":
        allowed = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in allowed})


# --------------------------------------------------------------------------- #
# swap_level engine (run_claude.py)
# --------------------------------------------------------------------------- #


def _to_claude_config(a: Assumptions) -> run_claude.Config:
    return run_claude.Config(
        capital_usd=a.capital_usd,
        range_width=a.range_width,
        reposition=a.reposition,
        reposition_buffer_hours=a.reposition_buffer_hours,
        gas_usd_per_reposition=a.gas_usd_per_reposition,
        lp_rebalance_swap_bps=a.lp_rebalance_swap_bps,
        hedge_band=a.hedge_band,
        taker_fee_bps=a.taker_fee_bps,
        half_spread_bps=a.half_spread_bps,
        impact_coef_bps_per_100k=a.impact_bps_per_100k,
        leverage=a.leverage,
        margin_buffer=a.margin_buffer,
        grid_seconds=a.grid_seconds,
        markout_seconds=a.markout_seconds,
        fee_tier=a.fee_tier,
    )


def _normalise_swap_level(res: dict) -> BacktestResult:
    df = res["df"]
    s = res["summary"]

    ts = pd.DataFrame(
        {
            "price": df["eth_price"],
            "lp_value": df["lp_value_usd"],
            "lp_delta": df["lp_delta"],
            "hedge_delta": df["hedge_delta"],
            "fee_usd": df["fee_usd"],
            "lvr_usd": df["lvr_usd"],
            "funding_usd": df["funding_usd"],
            "hedge_cost_usd": df["hedge_cost_usd"],
            "gas_usd": df["gas_usd"],
            "net_usd": df["net_usd"],
            "cum_net": df["cum_net"],
        },
        index=df.index,
    )

    return BacktestResult(
        engine="swap_level",
        summary=s,
        timeseries=ts,
        attribution=res["attribution"],
        extras={"positions": res["positions"], "pos_frame": res["pos_frame"]},
    )


def run_swap_level(
    assumptions: Assumptions,
    swaps: pd.DataFrame,
    cex: pd.Series,
    funding: pd.Series,
) -> BacktestResult:
    cfg = _to_claude_config(assumptions)
    return _normalise_swap_level(run_claude.run_backtest(swaps, cex, funding, cfg))


def sweep_swap_level(
    assumptions: Assumptions,
    swaps: pd.DataFrame,
    cex: pd.Series,
    funding: pd.Series,
    widths=(0.05, 0.10, 0.15, 0.25, 0.50),
    bands=(0.01, 0.02, 0.05, 0.10, 0.25),
) -> pd.DataFrame:
    return run_claude.sweep(
        swaps, cex, funding, _to_claude_config(assumptions), widths, bands
    )


# --------------------------------------------------------------------------- #
# hourly engine (kimi)
# --------------------------------------------------------------------------- #


def _to_kimi_config(a: Assumptions) -> KimiConfig:
    # If the user didn't set explicit lower/upper we derive them from a
    # nominal centre. The kimi engine treats these as fixed range bounds --
    # it does not know about "spot at t=0". Use the first CEX price at
    # call-time (that's why the derivation happens in run_hourly()).
    return KimiConfig(
        pool_fee_rate=a.fee_tier,
        binance_taker_fee=a.binance_taker_fee,
        rebalance_mode=a.rebalance_mode,  # type: ignore[arg-type]
        rebalance_threshold=a.rebalance_threshold_pct,
        rebalance_period=a.rebalance_period_h,
        initial_capital_usd=a.capital_usd,
        lower_price=a.lower_price if a.lower_price is not None else 0.0,
        upper_price=a.upper_price if a.upper_price is not None else 0.0,
        our_share_of_pool=a.pool_share,
        slippage_bps=a.slippage_bps,
    )


def _normalise_hourly(
    results: pd.DataFrame, summary: dict, cfg: KimiConfig
) -> BacktestResult:
    days = summary.get("backtest_days") or 1
    yrs = days / 365.0 if days else 1.0
    cap = cfg.initial_capital_usd

    def apr(x):
        return float(x) / cap / yrs * 100 if yrs > 0 else float("nan")

    ts = pd.DataFrame(
        {
            "price": results["price"],
            "lp_value": results["lp_value"],
            "lp_delta": results["delta_eth"],
            "hedge_delta": results["hedge_eth"],
            "fee_usd": results["fees_earned"],
            "lvr_usd": 0.0,  # not modelled in the hourly engine
            "funding_usd": results["funding_pnl"],
            "hedge_cost_usd": results["binance_fees"] + results["slippage_cost"],
            "gas_usd": 0.0,
            "net_usd": results["total_pnl"].diff().fillna(results["total_pnl"].iloc[0]),
            "cum_net": results["total_pnl"],
        },
        index=results.index,
    )

    unified_summary = {
        "days": summary.get("backtest_days"),
        "capital_lp_usd": cap,
        "capital_base_usd": round(cap),
        "range_width": None,
        "hedge_band": cfg.rebalance_threshold,
        "n_repositions": 0,
        "n_hedge_trades": int(summary.get("rebalance_count", 0)),
        "pct_time_in_range": round(float(summary.get("time_in_range", 0.0)), 1),
        "fees_usd": round(summary.get("total_fees_earned", 0.0)),
        "lvr_usd": 0,
        "fee_over_lvr": float("nan"),
        "funding_usd": round(summary.get("total_funding_pnl", 0.0)),
        "hedge_cost_usd": round(
            summary.get("total_binance_fees", 0.0)
            + summary.get("total_slippage", 0.0)
        ),
        "gas_reposition_usd": 0,
        "net_usd": round(summary.get("net_pnl", 0.0)),
        "fee_apr_pct": round(apr(summary.get("total_fees_earned", 0.0)), 2),
        "lvr_apr_pct": 0.0,
        "funding_apr_pct": round(apr(summary.get("total_funding_pnl", 0.0)), 2),
        "hedge_cost_apr_pct": round(
            apr(
                -(
                    summary.get("total_binance_fees", 0.0)
                    + summary.get("total_slippage", 0.0)
                )
            ),
            2,
        ),
        "net_apr_pct": round(summary.get("net_apr", 0.0), 2),
        "sharpe": None,
        "max_drawdown_usd": int((ts["cum_net"] - ts["cum_net"].cummax()).min()),
    }

    return BacktestResult(
        engine="hourly",
        summary=unified_summary,
        timeseries=ts,
        attribution=None,
        extras={},
    )


def run_hourly(
    assumptions: Assumptions,
    hourly_klines: pd.DataFrame,
    funding: pd.DataFrame,
) -> BacktestResult:
    """`hourly_klines` must be indexed by datetime and include OHLCV columns.
    `funding` must be indexed by datetime with a `fundingRate` column
    (Binance native schema, as produced by fetch_binance_funding /
    load_funding_df)."""
    cfg = _to_kimi_config(assumptions)

    # Default range = ± range_width around the first close
    if cfg.lower_price <= 0 or cfg.upper_price <= 0:
        p0 = float(hourly_klines["close"].iloc[0])
        cfg = KimiConfig(
            **{
                **cfg.__dict__,
                "lower_price": p0 * (1 - assumptions.range_width),
                "upper_price": p0 * (1 + assumptions.range_width),
            }
        )

    # The hourly engine reads pool volume from a `pool_volume_24h` column on
    # the klines frame (see kimi engine line 342-344). Without it, fees stay
    # 0. Estimate pool volume as a fraction of Binance quote volume.
    if "pool_volume_24h" not in hourly_klines.columns and "quote_volume" in hourly_klines.columns:
        hourly_klines = hourly_klines.copy()
        hourly_klines["pool_volume_24h"] = (
            hourly_klines["quote_volume"] * assumptions.pool_volume_multiplier * 24
        )

    bt = DeltaHedgeBacktest(cfg)
    results = bt.run(hourly_klines, funding)
    summary = bt.summarize(results)
    return _normalise_hourly(results, summary, cfg)


# --------------------------------------------------------------------------- #
# Data loaders (thin passthroughs to run_claude + kimi fetchers)
# --------------------------------------------------------------------------- #


def load_swaps(path: str) -> pd.DataFrame:
    return run_claude.load_swaps(path)


def load_cex_prices(path: str) -> pd.Series:
    return run_claude.load_cex_prices(path)


def load_funding_series(path: str) -> pd.Series:
    """Funding as a UTC-indexed Series -- what the swap_level engine wants."""
    return run_claude.load_funding(path)


def load_funding_df(path: str) -> pd.DataFrame:
    """Funding as a DataFrame indexed by fundingTime with a `fundingRate`
    column -- what the hourly engine wants."""
    s = run_claude.load_funding(path)
    df = pd.DataFrame({"fundingRate": s.values}, index=s.index)
    df.index.name = "fundingTime"
    return df


def make_demo_tape(
    days: int = 25,
    s0: float = 3000.0,
    vol_annual: float = 0.65,
    seed: int = 7,
):
    return run_claude.make_demo_data(
        days=days, s0=s0, vol_annual=vol_annual, seed=seed
    )


def fetch_binance_klines(
    symbol: str = "ETHUSDT",
    interval: str = "1h",
    days: int = 30,
) -> pd.DataFrame:
    """Live-fetch Binance klines for the hourly engine (no local file needed)."""
    end_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    return BinanceDataFetcher.get_perp_klines(
        symbol=symbol, interval=interval, start_ms=start_ms, end_ms=end_ms
    )


def fetch_binance_funding(
    symbol: str = "ETHUSDT",
    days: int = 30,
) -> pd.DataFrame:
    end_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)
    start_ms = end_ms - days * 24 * 60 * 60 * 1000
    return BinanceDataFetcher.get_funding_rates(
        symbol=symbol, start_ms=start_ms, end_ms=end_ms
    )


# --------------------------------------------------------------------------- #
# Auto-download of Binance monthly CSVs + Uniswap hourly pool aggregates
# --------------------------------------------------------------------------- #


def _months_between(start: str, end: str) -> list[str]:
    """['2024-01', '2024-02', ...] inclusive of both endpoints."""
    return list(pd.period_range(start, end, freq="M").strftime("%Y-%m"))


def _binance_csv_path(
    kind: str, month: str, symbol: str, out_dir: Path | str,
    interval: str | None = None,
) -> Path:
    """Mirrors run_claude.download_binance's destination naming, including
    the interval for klines so 1m/1h dumps don't collide."""
    if kind == "klines" and interval:
        return Path(out_dir) / f"{symbol}-klines-{interval}-{month}.csv"
    return Path(out_dir) / f"{symbol}-{kind}-{month}.csv"


def ensure_binance_csvs(
    months: list[str],
    kinds: tuple[str, ...] = ("aggTrades", "fundingRate"),
    symbol: str = "ETHUSDT",
    out_dir: Path | str = DATA_DIR,
    klines_interval: str = "1m",
) -> dict[str, dict]:
    """Download any missing Binance monthly CSVs to `data/`. Idempotent --
    files already on disk are skipped without any network call.

    `kinds` may include: 'aggTrades' (~3.5 GB/month), 'klines' (~5 MB/month
    at 1m, ~200 KB/month at 1h), 'fundingRate' (~10 KB/month).
    `klines_interval` is applied when 'klines' is in `kinds`.

    Returns {kind: {'paths': [...], 'downloaded': [...], 'skipped': [...]}}.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, dict] = {}

    for kind in kinds:
        interval = klines_interval if kind == "klines" else None
        skipped: list[str] = []
        to_fetch: list[str] = []
        all_paths: list[str] = []

        for m in months:
            dest = _binance_csv_path(kind, m, symbol, out_dir, interval)
            all_paths.append(str(dest))
            (skipped if dest.exists() else to_fetch).append(m)

        downloaded: list[str] = []
        if to_fetch:
            got = run_claude.download_binance(
                kind, to_fetch, symbol=symbol,
                interval=interval or "1m", out_dir=str(out_dir),
            )
            downloaded = [str(p) for p in got]

        out[kind] = {
            "paths": all_paths,
            "downloaded": downloaded,
            "skipped": [
                str(_binance_csv_path(kind, m, symbol, out_dir, interval))
                for m in skipped
            ],
        }
    return out


def _query_uniswap_subgraph(query: str, variables: dict) -> dict:
    """Call the Uniswap v3 subgraph. Tries the legacy hosted endpoint first
    (still works for read-only, no key), falls back to the gateway which
    requires THEGRAPH_API_KEY in env."""
    import os

    import requests

    endpoints = [
        "https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3",
    ]
    key = os.environ.get("THEGRAPH_API_KEY")
    if key:
        # 5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV = official Uniswap v3
        endpoints.append(
            f"https://gateway.thegraph.com/api/{key}/subgraphs/id/"
            "5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV"
        )

    last_err: Exception | None = None
    for url in endpoints:
        try:
            r = requests.post(
                url, json={"query": query, "variables": variables}, timeout=45
            )
            r.raise_for_status()
            out = r.json()
            if "errors" in out:
                raise RuntimeError(out["errors"])
            return out["data"]
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(
        f"Uniswap subgraph unreachable ({last_err!r}). "
        "Set THEGRAPH_API_KEY in your env to use the gateway."
    )


def fetch_pool_hourly(
    start: str,
    end: str,
    pool_address: str = ETH_USDC_POOL,
) -> pd.DataFrame:
    """Fetch hourly pool aggregates (volumeUSD, feesUSD, liquidity, tvlUSD,
    sqrtPriceX96, tick) from the Uniswap v3 subgraph, paginated. Returns a
    time-indexed DataFrame in UTC."""
    query = """
    query ($pool: String!, $start: Int!, $end: Int!) {
      poolHourDatas(
        first: 1000
        orderBy: periodStartUnix
        orderDirection: asc
        where: {
          pool: $pool,
          periodStartUnix_gte: $start,
          periodStartUnix_lte: $end
        }
      ) {
        periodStartUnix
        volumeUSD
        feesUSD
        liquidity
        sqrtPrice
        tick
        tvlUSD
        txCount
      }
    }
    """
    start_ts = int(pd.Timestamp(start, tz="UTC").timestamp())
    end_ts = int(pd.Timestamp(end, tz="UTC").timestamp())

    rows = []
    cursor = start_ts
    while cursor < end_ts:
        data = _query_uniswap_subgraph(
            query, {"pool": pool_address.lower(), "start": cursor, "end": end_ts}
        )
        batch = data.get("poolHourDatas") or []
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1]["periodStartUnix"]) + 1
        if len(batch) < 1000:
            break

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["periodStartUnix"].astype(int), unit="s", utc=True)
    for c in ("volumeUSD", "feesUSD", "liquidity", "sqrtPrice", "tick", "tvlUSD", "txCount"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.set_index("ts").sort_index().drop(columns=["periodStartUnix"])


def ensure_pool_hourly(
    start: str,
    end: str,
    pool_address: str = ETH_USDC_POOL,
    out_dir: Path | str = DATA_DIR,
) -> dict:
    """Download hourly pool aggregates and cache as parquet. Idempotent --
    returns immediately if the parquet already exists.

    Returns {'path': str, 'cached': bool}.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"pool_hourly_{pool_address[:8]}_{start}_{end}.parquet"
    if dest.exists():
        return {"path": str(dest), "cached": True}
    df = fetch_pool_hourly(start, end, pool_address)
    if df.empty:
        raise RuntimeError("Uniswap subgraph returned no rows for that window.")
    df.to_parquet(dest)
    return {"path": str(dest), "cached": False}


def fetch_pool_swaps(
    start: str,
    end: str,
    pool_address: str = ETH_USDC_POOL,
    dec0: int = 6,   # USDC
    dec1: int = 18,  # WETH
    progress_cb=None,
    cancel_cb=None,
) -> pd.DataFrame:
    """Paginated download of individual pool swap events from the Uniswap v3
    subgraph. Returns a DataFrame in the canonical schema `run_claude.py`
    expects (block_number, block_time UTC, amount0, amount1 raw signed ints,
    sqrt_price_x96, liquidity, tick).

    The subgraph returns amounts as decimal strings already scaled by the
    token's decimals -- we multiply back to raw ints.

    `progress_cb(count, cursor_ts)` is called after every page so a
    Streamlit UI can update a progress bar. `cancel_cb()` returning True
    aborts the loop early and returns whatever's been fetched so far.
    """
    query = """
    query($pool: String!, $ts_gte: Int!, $ts_lt: Int!, $last_ts: Int!, $last_logIndex: Int!) {
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
        liquidity
        tick
      }
    }
    """
    start_ts = int(pd.Timestamp(start, tz="UTC").timestamp())
    end_ts = int(pd.Timestamp(end, tz="UTC").timestamp())

    rows: list[dict] = []
    last_ts = start_ts - 1
    scale0 = 10 ** dec0
    scale1 = 10 ** dec1

    while True:
        data = _query_uniswap_subgraph(
            query,
            {
                "pool": pool_address.lower(),
                "ts_gte": start_ts,
                "ts_lt": end_ts,
                "last_ts": last_ts,
                "last_logIndex": 0,  # unused placeholder for future keyset
            },
        )
        batch = data.get("swaps") or []
        if not batch:
            break

        for s in batch:
            rows.append(
                {
                    "block_number": int(s["transaction"]["blockNumber"]),
                    "block_time": int(s["timestamp"]),
                    "amount0": int(round(float(s["amount0"]) * scale0)),
                    "amount1": int(round(float(s["amount1"]) * scale1)),
                    "sqrt_price_x96": int(s["sqrtPriceX96"]),
                    "liquidity": int(s["liquidity"]),
                    "tick": int(s["tick"]),
                }
            )

        # advance cursor. The subgraph doesn't accept compound keyset
        # pagination on (timestamp, logIndex), so we advance one second past
        # the last batch's timestamp -- this can drop rows sharing that
        # exact second. Guard against it by de-duping on (block, logIndex)
        # after we're done.
        new_last_ts = int(batch[-1]["timestamp"])
        if new_last_ts == last_ts:
            # entire batch at the same timestamp; jump forward by 1s to
            # avoid an infinite loop, accepting the small dedup risk
            last_ts = new_last_ts + 1
        else:
            last_ts = new_last_ts

        if progress_cb is not None:
            progress_cb(len(rows), last_ts)

        if cancel_cb is not None and cancel_cb():
            break
        if len(batch) < 1000:
            break
        if last_ts >= end_ts:
            break

    if not rows:
        return pd.DataFrame(
            columns=[
                "block_number", "block_time", "amount0", "amount1",
                "sqrt_price_x96", "liquidity", "tick",
            ]
        )

    df = pd.DataFrame(rows)
    df = df.drop_duplicates(
        subset=["block_number", "block_time", "sqrt_price_x96"], keep="first"
    )
    df["block_time"] = pd.to_datetime(df["block_time"], unit="s", utc=True)
    return df.sort_values("block_time").reset_index(drop=True)


def ensure_pool_swaps(
    start: str,
    end: str,
    pool_address: str = ETH_USDC_POOL,
    out_dir: Path | str = DATA_DIR,
    progress_cb=None,
    cancel_cb=None,
) -> dict:
    """Download individual pool swaps for [start, end] and cache as parquet.
    Idempotent -- returns immediately if the parquet already exists.

    `start` / `end` are 'YYYY-MM-DD' strings.

    Returns {'path': str, 'cached': bool, 'rows': int}."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"swaps_{pool_address[:8]}_{start}_{end}.parquet"
    if dest.exists():
        # Probe file is intact -- a half-written parquet from a killed
        # download would raise here and we fall through to re-fetch.
        try:
            n = len(pd.read_parquet(dest, columns=["block_number"]))
            return {"path": str(dest), "cached": True, "rows": n}
        except Exception:
            dest.unlink()

    df = fetch_pool_swaps(
        start, end, pool_address=pool_address,
        progress_cb=progress_cb, cancel_cb=cancel_cb,
    )
    if df.empty:
        raise RuntimeError("Uniswap subgraph returned no swaps for that window.")
    # Atomic: write to .tmp then rename, so a killed process never leaves
    # a partial parquet in the swaps-file dropdown.
    tmp = dest.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False)
    tmp.replace(dest)
    return {"path": str(dest), "cached": False, "rows": len(df)}


def ensure_all_data(
    start: str = "2024-01",
    end: str = "2024-06",
    pool_address: str = ETH_USDC_POOL,
    cex_kind: str = "klines",
    klines_interval: str = "1m",
    include_pool_hourly: bool = False,
    out_dir: Path | str = DATA_DIR,
) -> dict:
    """One-shot: ensure Binance CSVs for [start, end] are on disk, and
    optionally a cached parquet of hourly pool aggregates.

    `cex_kind` picks the price source: 'klines' (~5 MB/month at 1m,
    ~200 KB/month at 1h — enough for exploration) or 'aggTrades'
    (~3.5 GB/month, only worth it for path-exact LVR).
    """
    months = _months_between(start, end)
    result: dict = {
        "binance": ensure_binance_csvs(
            months,
            kinds=(cex_kind, "fundingRate"),
            out_dir=out_dir,
            klines_interval=klines_interval,
        )
    }

    if include_pool_hourly:
        start_day = f"{start}-01"
        end_day = str(pd.Period(end, freq="M").end_time.date())
        try:
            result["pool_hourly"] = ensure_pool_hourly(
                start_day, end_day, pool_address, out_dir=out_dir
            )
        except Exception as e:
            result["pool_hourly_error"] = str(e)
    return result
