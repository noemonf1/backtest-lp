#!/usr/bin/env python3
"""Streamlit UI for the delta-hedged Uniswap v3 LP backtest.

Two engines, one UI:

- **swap_level** (run_claude.py): reference engine. Path-exact fees + LVR
  from swap-level events; explicit hedge execution model; closed-loop
  accounting identity with a closed-form LVR sanity check. Needs a swaps
  dump (cryo/Dune/Allium) OR uses the built-in synthetic demo tape.

- **hourly** (kimi): lightweight fallback. Only needs Binance hourly klines
  + funding; can live-fetch both. Coarser (no LVR, flat bps hedge slippage,
  fees estimated from pool-share × pool-volume-multiplier × Binance vol).
"""

from __future__ import annotations

import json
import os
import time
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import backtest as bt

REPO = Path(__file__).parent
DATA = REPO / "data"

st.set_page_config(page_title="ETH/USDC Hedged LP Backtest", layout="wide")
st.title("ETH/USDC v3 delta-hedged LP backtest")
st.caption(
    "Sizes a symmetric LP position, delta-hedges on Binance perps, and "
    "attributes PnL into fees / LVR / funding / hedge cost / gas."
)


# --------------------------------------------------------------------------- #
# Session-state defaults
# --------------------------------------------------------------------------- #

DEFAULT_FETCH = {
    "start": "2024-01",
    "end": "2024-01",
    "cex_kind": "klines",
    "klines_interval": "1m",
    "swap_start": "2024-01-01",
    "swap_end": "2024-01-08",
}


def _init_state():
    st.session_state.setdefault("fetch_cfg", dict(DEFAULT_FETCH))
    st.session_state.setdefault("assumptions", bt.Assumptions().to_dict())
    st.session_state.setdefault("runs", [])  # list of {label, engine, summary, ts_df, params}
    st.session_state.setdefault("cancel_swap_fetch", False)


_init_state()


# --------------------------------------------------------------------------- #
# File helpers
# --------------------------------------------------------------------------- #


def _list_files(pattern: str) -> list[str]:
    if not DATA.exists():
        return []
    return sorted(str(p.relative_to(REPO)) for p in DATA.glob(pattern))


def _list_swap_files() -> list[str]:
    hits: list[Path] = []
    for pat in ("*.parquet", "*.csv"):
        hits.extend(REPO.glob(pat))
        hits.extend(DATA.glob(pat))
    keep = [
        p for p in hits
        if "swap" in p.name.lower()
        and not p.name.endswith(".tmp")
        and not p.name.endswith(".parquet.tmp")
    ]
    return sorted(str(p.relative_to(REPO)) for p in keep)


# --------------------------------------------------------------------------- #
# Cached loaders (parametrised on path only -- files themselves are immutable
# once written, so we don't need to invalidate the cache on new downloads)
# --------------------------------------------------------------------------- #


@st.cache_data(show_spinner="Generating synthetic tape…")
def _demo(days, s0, vol, seed):
    return bt.make_demo_tape(days=days, s0=s0, vol_annual=vol, seed=seed)


@st.cache_data(show_spinner="Loading swaps…")
def _load_swaps(path: str):
    return bt.load_swaps(path)


@st.cache_data(show_spinner="Loading CEX prices…")
def _load_cex(path: str):
    return bt.load_cex_prices(path)


@st.cache_data(show_spinner="Loading funding series…")
def _load_funding_series(path: str):
    return bt.load_funding_series(path)


@st.cache_data(show_spinner="Loading funding table…")
def _load_funding_df(path: str):
    return bt.load_funding_df(path)


@st.cache_data(show_spinner="Fetching Binance klines…")
def _fetch_klines(days, interval):
    return bt.fetch_binance_klines(interval=interval, days=days)


@st.cache_data(show_spinner="Fetching Binance funding…")
def _fetch_funding(days):
    return bt.fetch_binance_funding(days=days)


# --------------------------------------------------------------------------- #
# Sidebar sections (fragments so section reruns don't ripple)
# --------------------------------------------------------------------------- #


def _fmt_bytes(m: str, kind: str, interval: str) -> str:
    return {"aggTrades": "~3.5 GB/mo", "klines": {"1m": "~5 MB/mo", "1h": "~200 KB/mo"}.get(interval, "?"), "fundingRate": "~10 KB/mo"}.get(kind, "?")


@st.fragment
def _sidebar_fetch():
    """Fetch-data expander. Writes into st.session_state['fetch_cfg'].
    Wrapped in st.fragment so downloads / progress updates don't cascade
    a full-page rerun of the sidebar and main tab."""
    cfg = st.session_state["fetch_cfg"]

    with st.expander("Fetch data", expanded=False):
        st.caption(
            "Download missing Binance CSVs into `data/`. Files already on "
            "disk are skipped — no network call."
        )
        cfg["start"] = st.text_input("From (YYYY-MM)", value=cfg["start"], key="fetch_start")
        cfg["end"] = st.text_input("To (YYYY-MM)", value=cfg["end"], key="fetch_end")

        choice = st.selectbox(
            "CEX price resolution",
            [
                "klines 1m (~5 MB/month) — recommended",
                "klines 1h (~200 KB/month) — smallest",
                "aggTrades (~3.5 GB/month) — path-exact LVR",
            ],
            index=0,
            key="fetch_cex_choice",
            help=(
                "The swap_level engine resamples whatever you give it to 1s "
                "internally. aggTrades is only worth downloading if you "
                "genuinely need sub-minute CEX mark accuracy for LVR."
            ),
        )
        if choice.startswith("aggTrades"):
            cfg["cex_kind"], cfg["klines_interval"] = "aggTrades", "1m"
        elif "1m" in choice:
            cfg["cex_kind"], cfg["klines_interval"] = "klines", "1m"
        else:
            cfg["cex_kind"], cfg["klines_interval"] = "klines", "1h"

        if st.button("Fetch now", key="fetch_binance_btn"):
            with st.spinner("Checking / downloading…"):
                try:
                    result = bt.ensure_all_data(
                        start=cfg["start"].strip(),
                        end=cfg["end"].strip(),
                        cex_kind=cfg["cex_kind"],
                        klines_interval=cfg["klines_interval"],
                        include_pool_hourly=False,
                    )
                    n_dl = sum(len(v["downloaded"]) for v in result["binance"].values())
                    n_skip = sum(len(v["skipped"]) for v in result["binance"].values())
                    st.success(
                        f"Binance: {n_dl} downloaded, {n_skip} already present."
                    )
                    # No cache_data.clear() -- loaders are keyed on path;
                    # files are new so no old entry to invalidate.
                except Exception as e:
                    st.exception(e)

        st.divider()
        st.caption(
            "**Pool swap events** (needed by the swap_level engine). "
            "Fetched from the Uniswap v3 subgraph via The Graph gateway — "
            "**requires `THEGRAPH_API_KEY` in your env** "
            "([get a free key](https://thegraph.com/studio/)). "
            "Slow: ~2–3M swaps/month for ETH/USDC 0.05%, many minutes per "
            "month. Cached in `data/`."
        )
        cfg["swap_start"] = st.text_input(
            "Swaps from (YYYY-MM-DD)", value=cfg["swap_start"], key="fetch_swap_start"
        )
        cfg["swap_end"] = st.text_input(
            "Swaps to (YYYY-MM-DD)", value=cfg["swap_end"], key="fetch_swap_end"
        )
        col_a, col_b = st.columns(2)
        with col_a:
            fetch_swaps = st.button("Fetch swap events", key="fetch_swaps_btn")
        with col_b:
            if st.button("Cancel", key="fetch_swaps_cancel"):
                st.session_state["cancel_swap_fetch"] = True

        if fetch_swaps:
            _run_swap_fetch(cfg["swap_start"], cfg["swap_end"])


def _run_swap_fetch(swap_start: str, swap_end: str):
    if not os.environ.get("THEGRAPH_API_KEY"):
        st.error(
            "THEGRAPH_API_KEY not set. Export it in your shell and restart "
            "the app: `export THEGRAPH_API_KEY=...`"
        )
        return

    st.session_state["cancel_swap_fetch"] = False
    prog = st.progress(0.0, text="Fetching swaps…")
    status = st.empty()
    t0 = time.time()
    span_ts = pd.Timestamp(swap_end, tz="UTC").timestamp() - pd.Timestamp(
        swap_start, tz="UTC"
    ).timestamp()
    span_ts = max(span_ts, 1.0)
    start_ts = pd.Timestamp(swap_start, tz="UTC").timestamp()

    def _cb(n_rows, cursor_ts):
        frac = min((cursor_ts - start_ts) / span_ts, 1.0)
        elapsed = time.time() - t0
        eta = (elapsed / frac - elapsed) if frac > 0.01 else float("inf")
        eta_str = f"~{int(eta)}s remaining" if eta < 3600 else f"~{eta/60:.0f} min remaining"
        prog.progress(frac, text=f"Fetching swaps… {n_rows:,} so far")
        status.caption(
            f"Cursor {pd.Timestamp(cursor_ts, unit='s', tz='UTC')} · "
            f"elapsed {elapsed:.0f}s · {eta_str}"
        )

    try:
        r = bt.ensure_pool_swaps(
            swap_start.strip(), swap_end.strip(),
            progress_cb=_cb,
            cancel_cb=lambda: st.session_state.get("cancel_swap_fetch", False),
        )
        prog.empty()
        status.empty()
        tag = "already cached" if r["cached"] else "downloaded"
        if st.session_state["cancel_swap_fetch"]:
            st.warning(
                f"Cancelled. Partial data ({r['rows']:,} rows) saved to "
                f"`{r['path']}`."
            )
        else:
            st.success(f"Swaps: {r['rows']:,} rows {tag} → `{r['path']}`")
    except Exception as e:
        prog.empty()
        status.empty()
        st.exception(e)
    finally:
        st.session_state["cancel_swap_fetch"] = False


@st.fragment
def _sidebar_config_persist():
    """Save/load/reset controls for the sidebar Assumptions state.
    Fragmented so uploads / downloads don't rerun the whole app."""
    with st.expander("Save / load config", expanded=False):
        cfg_json = json.dumps(st.session_state["assumptions"], indent=2)
        st.download_button(
            "Download current config (JSON)",
            data=cfg_json,
            file_name="backtest_config.json",
            mime="application/json",
            use_container_width=True,
        )
        upload = st.file_uploader("Load config", type=["json"], key="cfg_upload")
        if upload is not None and st.session_state.get("_last_upload_id") != upload.file_id:
            try:
                loaded = json.load(upload)
                bt.Assumptions.from_dict(loaded)  # validate
                st.session_state["assumptions"].update(loaded)
                st.session_state["_last_upload_id"] = upload.file_id
                st.success("Config loaded — full page rerun to update sliders…")
                st.rerun()
            except Exception as e:
                st.error(f"Invalid config file: {e}")
        if st.button("Reset all to defaults", use_container_width=True):
            st.session_state["assumptions"] = bt.Assumptions().to_dict()
            st.session_state["fetch_cfg"] = dict(DEFAULT_FETCH)
            # Clear widget-owned state so sliders re-initialise from defaults
            for k in list(st.session_state.keys()):
                if k.startswith(("fetch_", "win_", "cfg_upload")):
                    del st.session_state[k]
            st.rerun()  # full page rerun so all widgets pick up defaults


# --------------------------------------------------------------------------- #
# Sidebar
# --------------------------------------------------------------------------- #

with st.sidebar:
    _sidebar_fetch()
    _sidebar_config_persist()

    st.header("Engine")
    engine = st.selectbox(
        "Backtest engine",
        ["swap_level (reference)", "hourly (fast fallback)"],
        help=(
            "**swap_level** is path-exact and models LVR + gas + realistic "
            "hedge execution, but needs swap events.\n\n"
            "**hourly** just needs Binance klines (can live-fetch) and gives "
            "a rough fees-vs-funding picture."
        ),
    )
    engine_key = "swap_level" if engine.startswith("swap") else "hourly"

    st.header("Data source")
    swaps_path = cex_path = funding_path = None
    demo_days = demo_vol = demo_s0 = demo_seed = None
    live_days = 30
    interval = "1h"

    if engine_key == "swap_level":
        mode = st.selectbox(
            "Mode",
            ["Demo (synthetic tape)", "Real data (files)"],
            help="Demo generates a GBM CEX tape + synthetic pool swaps.",
        )
        if mode.startswith("Demo"):
            demo_days = st.slider("Demo days", 5, 120, 25, step=5)
            demo_vol = st.slider("Annualised vol", 0.20, 1.50, 0.65, step=0.05)
            demo_s0 = st.number_input("Starting ETH price (USD)", value=3000.0, step=100.0)
            demo_seed = st.number_input("RNG seed", value=7, step=1)
        else:
            swap_files = _list_swap_files()
            if not swap_files:
                st.warning(
                    "No swap files found. Use **Fetch data → Fetch swap "
                    "events** above (requires `THEGRAPH_API_KEY`) or drop a "
                    "cryo/Dune/Allium dump named `*swap*.parquet` in the "
                    "repo root or `data/`."
                )
            swaps_path = st.selectbox("Swaps file", swap_files or ["(none)"])

            cex_files = _list_files("*aggTrades*.csv") + _list_files("*klines*.csv")
            fund_files_avail = _list_files("*fundingRate*.csv")
            if not cex_files or not fund_files_avail:
                st.info(
                    "Missing Binance CSVs. Use **Fetch data** above to "
                    "download them (they're cached in `data/`)."
                )
            cex_path = st.selectbox("CEX price file", cex_files or ["(none)"])
            fund_files = ["(none — assume 0)"] + fund_files_avail
            funding_path = st.selectbox("Funding file", fund_files)
    else:
        mode = st.selectbox("Mode", ["Live fetch from Binance", "Local CSV files"])
        if mode.startswith("Live"):
            live_days = st.slider("Lookback (days)", 7, 180, 30, step=1)
            interval = st.selectbox("Bar interval", ["1h", "4h", "1d"], index=0)
        else:
            cex_files = _list_files("*aggTrades*.csv") + _list_files("*klines*.csv")
            fund_files_avail = _list_files("*fundingRate*.csv")
            if not cex_files or not fund_files_avail:
                st.info(
                    "Missing Binance CSVs. Use **Fetch data** above to "
                    "download them (they're cached in `data/`)."
                )
            cex_path = st.selectbox("CEX price file", cex_files or ["(none)"])
            funding_path = st.selectbox("Funding file", fund_files_avail or ["(none)"])

    # ---------------- Backtest window --------------------------------------- #
    st.header("Backtest window")
    st.caption(
        "Optional. If set, series are clipped to `[start, end]` before "
        "handing them to the engine. Leave both blank to use the full range."
    )
    win_start = st.text_input(
        "Window start (YYYY-MM-DD)", value="", key="win_start",
        placeholder="e.g. 2024-01-15",
    )
    win_end = st.text_input(
        "Window end (YYYY-MM-DD)", value="", key="win_end",
        placeholder="e.g. 2024-02-01",
    )

    # ---------------- Assumptions ------------------------------------------- #
    A = st.session_state["assumptions"]  # short alias

    st.header("LP leg")
    A["capital_usd"] = st.number_input(
        "Capital deployed (USD)", min_value=1_000.0,
        value=float(A["capital_usd"]), step=50_000.0,
    )

    if engine_key == "swap_level":
        A["range_width"] = st.select_slider(
            "Range width (± fraction around spot)",
            options=[0.02, 0.05, 0.10, 0.15, 0.25, 0.50, 0.75],
            value=float(A["range_width"]),
        )
        A["lower_price"] = None
        A["upper_price"] = None
    else:
        range_mode = st.selectbox(
            "Range specification",
            ["Symmetric ± around spot", "Absolute price bounds"],
        )
        if range_mode.startswith("Symmetric"):
            A["range_width"] = st.select_slider(
                "Range width (± fraction)",
                options=[0.02, 0.05, 0.10, 0.15, 0.25, 0.50, 0.75],
                value=float(A["range_width"]),
            )
            A["lower_price"] = A["upper_price"] = None
        else:
            A["lower_price"] = st.number_input(
                "Lower price (USDC/ETH)",
                value=float(A.get("lower_price") or 1800.0), step=50.0,
            )
            A["upper_price"] = st.number_input(
                "Upper price (USDC/ETH)",
                value=float(A.get("upper_price") or 2200.0), step=50.0,
            )
        A["pool_share"] = st.number_input(
            "Our share of pool active liquidity",
            value=float(A["pool_share"]), min_value=0.00001, max_value=0.5,
            step=0.0005, format="%.5f",
            help="Fees earned = pool_volume × fee_tier × share.",
        )
        A["pool_volume_multiplier"] = st.number_input(
            "Pool volume as fraction of Binance quote volume",
            value=float(A["pool_volume_multiplier"]),
            min_value=0.001, max_value=1.0, step=0.01,
            help="Rough proxy since we don't have real pool swap data. "
            "8% is a reasonable default for ETH/USDC.",
        )

    if engine_key == "swap_level":
        A["reposition"] = st.selectbox(
            "Reposition when out of range?", ["Yes", "No"],
            index=0 if A["reposition"] else 1,
        ) == "Yes"
        A["reposition_buffer_hours"] = st.number_input(
            "Reposition buffer (hours)",
            value=float(A["reposition_buffer_hours"]),
            min_value=0.0, step=1.0,
        )
        A["gas_usd_per_reposition"] = st.number_input(
            "Gas per reposition (USD)",
            value=float(A["gas_usd_per_reposition"]),
            min_value=0.0, step=5.0,
        )
        A["lp_rebalance_swap_bps"] = st.number_input(
            "Swap cost to re-ratio on reposition (bps)",
            value=float(A["lp_rebalance_swap_bps"]),
            min_value=0.0, step=0.5,
        )

    st.header("Hedge leg")
    if engine_key == "swap_level":
        A["hedge_band"] = st.select_slider(
            "Hedge band (fraction of max delta)",
            options=[0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.25],
            value=float(A["hedge_band"]),
        )
        A["taker_fee_bps"] = st.selectbox(
            "Binance taker fee (bps)", [2.0, 3.0, 4.0, 4.5, 5.0],
            index=[2.0, 3.0, 4.0, 4.5, 5.0].index(float(A["taker_fee_bps"]))
                if float(A["taker_fee_bps"]) in [2.0, 3.0, 4.0, 4.5, 5.0] else 3,
        )
        A["half_spread_bps"] = st.number_input(
            "Half-spread when using klines (bps)",
            value=float(A["half_spread_bps"]), min_value=0.0, step=0.1,
        )
        A["impact_bps_per_100k"] = st.number_input(
            "Linear impact (bps per $100k clip)",
            value=float(A["impact_bps_per_100k"]), min_value=0.0, step=0.1,
        )
        A["binance_taker_fee"] = A["taker_fee_bps"] / 10_000
    else:
        A["rebalance_mode"] = st.selectbox(
            "Rebalance mode", ["threshold", "periodic"],
            index=0 if A["rebalance_mode"] == "threshold" else 1,
        )
        if A["rebalance_mode"] == "threshold":
            A["rebalance_threshold_pct"] = st.select_slider(
                "Rebalance threshold (fraction of |delta|)",
                options=[0.01, 0.02, 0.05, 0.10, 0.20],
                value=float(A["rebalance_threshold_pct"]),
            )
        else:
            A["rebalance_period_h"] = st.selectbox(
                "Rebalance every (hours)", [1, 4, 8, 12, 24, 48],
                index=[1, 4, 8, 12, 24, 48].index(int(A["rebalance_period_h"]))
                    if int(A["rebalance_period_h"]) in [1, 4, 8, 12, 24, 48] else 4,
            )
        fee_bps = st.selectbox(
            "Binance taker fee (bps)", [2.0, 3.0, 4.0, 4.5, 5.0],
            index=3,
        )
        A["binance_taker_fee"] = fee_bps / 10_000
        A["taker_fee_bps"] = fee_bps
        A["slippage_bps"] = st.number_input(
            "Flat slippage on hedge trades (bps)",
            value=float(A["slippage_bps"]), min_value=0.0, step=0.5,
        )

    A["leverage"] = st.selectbox(
        "Perp leverage", [1, 2, 3, 4, 5, 10],
        index=[1, 2, 3, 4, 5, 10].index(int(A["leverage"]))
            if int(A["leverage"]) in [1, 2, 3, 4, 5, 10] else 3,
    )
    A["margin_buffer"] = st.number_input(
        "Margin buffer (× notional/leverage)",
        value=float(A["margin_buffer"]), min_value=1.0, step=0.1,
    )

    if engine_key == "swap_level":
        st.header("Mechanics")
        A["grid_seconds"] = st.selectbox(
            "Hedge decision grid (s)", [10, 30, 60, 120, 300],
            index=[10, 30, 60, 120, 300].index(int(A["grid_seconds"]))
                if int(A["grid_seconds"]) in [10, 30, 60, 120, 300] else 2,
        )
        A["markout_seconds"] = st.selectbox(
            "Fee markout window (s)", [0, 60, 300, 900],
            index=[0, 60, 300, 900].index(int(A["markout_seconds"]))
                if int(A["markout_seconds"]) in [0, 60, 300, 900] else 0,
        )

    fee_tier_bps = st.selectbox(
        "LP fee tier (bps)", [1, 5, 30, 100],
        index=[1, 5, 30, 100].index(int(round(A["fee_tier"] * 10_000)))
            if int(round(A["fee_tier"] * 10_000)) in [1, 5, 30, 100] else 1,
        help="0.01% / 0.05% / 0.30% / 1.00%",
    )
    A["fee_tier"] = fee_tier_bps / 10_000

    st.header("Run")
    do_sweep = False
    if engine_key == "swap_level":
        do_sweep = st.checkbox("Run range × hedge-band sweep", value=False)
    do_rolling = st.checkbox(
        "Rolling-window distribution (30d)", value=False,
        help="Split the backtest into overlapping 30-day windows and plot "
        "the APR distribution across them.",
    )
    run_label = st.text_input(
        "Run label (optional)", value="",
        placeholder="e.g. 'wide range, tight band'",
    )


# --------------------------------------------------------------------------- #
# Data-loading helpers
# --------------------------------------------------------------------------- #


def _clip_series(s: pd.Series | pd.DataFrame, start: str, end: str):
    if not start and not end:
        return s
    s0 = pd.Timestamp(start, tz="UTC") if start else None
    s1 = pd.Timestamp(end, tz="UTC") if end else None
    return s.loc[s0:s1]


def _clip_swaps(df: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    if not start and not end:
        return df
    s0 = pd.Timestamp(start, tz="UTC") if start else df["block_time"].min()
    s1 = pd.Timestamp(end, tz="UTC") if end else df["block_time"].max()
    m = (df["block_time"] >= s0) & (df["block_time"] <= s1)
    return df.loc[m].reset_index(drop=True)


def _load_swap_level_inputs():
    if mode.startswith("Demo"):
        return _demo(int(demo_days), float(demo_s0), float(demo_vol), int(demo_seed))
    if not swaps_path or swaps_path == "(none)":
        st.error(
            "No swap file selected. Swap-level data can't be "
            "auto-downloaded — use **Fetch data → Fetch swap events** or "
            "drop a cryo/Dune/Allium dump, or switch to the **hourly** engine."
        )
        return None
    if not cex_path or cex_path == "(none)":
        st.error(
            "No CEX file selected. Use **Fetch data → Fetch now** to "
            "download klines for the range you need."
        )
        return None

    swaps = _clip_swaps(_load_swaps(str(REPO / swaps_path)), win_start, win_end)
    cex = _clip_series(_load_cex(str(REPO / cex_path)), win_start, win_end)
    funding = (
        _clip_series(_load_funding_series(str(REPO / funding_path)), win_start, win_end)
        if funding_path and not funding_path.startswith("(none")
        else pd.Series(dtype=float)
    )
    if len(swaps) == 0 or len(cex) == 0:
        st.error("Backtest window is empty after clipping — check the dates.")
        return None
    return swaps, cex, funding


def _load_hourly_inputs():
    if mode.startswith("Live"):
        return _fetch_klines(int(live_days), str(interval)), _fetch_funding(int(live_days))
    if not cex_path or cex_path == "(none)":
        st.error(
            "No CEX file selected. Use **Fetch data → Fetch now** to "
            "download klines."
        )
        return None
    cex_series = _clip_series(_load_cex(str(REPO / cex_path)), win_start, win_end)
    klines = (
        cex_series.resample("1h")
        .agg(["first", "max", "min", "last"])
        .rename(columns={"first": "open", "max": "high", "min": "low", "last": "close"})
        .dropna()
    )
    klines["volume"] = 0.0
    klines["quote_volume"] = 0.0
    funding = (
        _clip_series(_load_funding_df(str(REPO / funding_path)), win_start, win_end)
        if funding_path and funding_path != "(none)"
        else pd.DataFrame({"fundingRate": []})
    )
    if klines.empty:
        st.error("No klines in the selected window.")
        return None
    return klines, funding


# --------------------------------------------------------------------------- #
# Analysis helpers
# --------------------------------------------------------------------------- #


def _rolling_apr(df: pd.DataFrame, capital_base: float, window_days: int = 30) -> pd.Series:
    """Rolling annualised return of net PnL across a `window_days` window."""
    if "net_usd" not in df.columns or df.empty:
        return pd.Series(dtype=float)
    daily = df["net_usd"].resample("1D").sum()
    if len(daily) < window_days:
        return pd.Series(dtype=float)
    return (daily.rolling(window_days).sum() / capital_base) * (365 / window_days) * 100


def _push_run(res, label: str, params: dict):
    """Store this run in session_state (cap at 5)."""
    entry = {
        "label": label or f"Run {len(st.session_state['runs']) + 1}",
        "engine": res.engine,
        "summary": deepcopy(res.summary),
        "ts_df": res.timeseries.copy(),
        "params": deepcopy(params),
        "when": pd.Timestamp.utcnow(),
    }
    st.session_state["runs"].append(entry)
    st.session_state["runs"] = st.session_state["runs"][-5:]


# --------------------------------------------------------------------------- #
# Render helpers
# --------------------------------------------------------------------------- #


def _render_verdict(res, is_demo: bool):
    s = res.summary
    net_apr = s["net_apr_pct"] or 0.0

    if is_demo:
        st.warning(
            "**Synthetic tape** — GBM prices + simulated swaps. Great for "
            "validating the engine and exploring parameter sensitivity, but "
            "numbers do NOT reflect real ETH/USDC PnL."
        )

    color = "green" if net_apr > 0 else "red"
    verdict = "FEES COVER THE COST" if net_apr > 0 else "FEES DO NOT COVER THE COST"
    st.markdown(
        f"### :{color}[{verdict}] — net **{net_apr:.2f}% APR** · "
        f"engine: `{res.engine}`"
    )

    cap = s.get("capital_base_usd", 0)
    lp = s.get("capital_lp_usd", s.get("capital_usd", cap))
    margin = cap - lp if cap and lp else 0
    st.caption(
        f"Capital base = LP ${lp:,.0f} + hedge margin ${margin:,.0f} = "
        f"**${cap:,.0f}** (denominator for all APRs below)"
    )


def _render_metrics(res):
    s = res.summary
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Net PnL (USD)", f"${s['net_usd']:,}")
    c2.metric("Fees APR", f"{s['fee_apr_pct']:.2f}%")
    c3.metric(
        "LVR APR",
        f"{s['lvr_apr_pct']:.2f}%" if s.get("lvr_apr_pct") is not None else "n/a",
    )
    c4.metric("Funding APR", f"{s['funding_apr_pct']:.2f}%")
    c5.metric("Hedge cost APR", f"{s['hedge_cost_apr_pct']:.2f}%")

    c6, c7, c8, c9 = st.columns(4)
    c6.metric("Fees / LVR", f"{s.get('fee_over_lvr', float('nan'))}")
    c7.metric("% time in range", f"{s.get('pct_time_in_range', 0)}%")
    c8.metric("Sharpe (daily)", f"{s.get('sharpe', 'n/a')}")
    c9.metric("Max drawdown", f"${s.get('max_drawdown_usd', 0):,}")


def _render_charts(res):
    df = res.timeseries

    st.subheader("Cumulative PnL")
    st.line_chart(
        df[["cum_net"]].rename(columns={"cum_net": "Cumulative net PnL (USD)"})
    )

    st.subheader("PnL attribution")
    tab_cum, tab_daily = st.tabs(["Cumulative", "Daily bars"])
    with tab_cum:
        attr = pd.DataFrame(
            {
                "Fees": df["fee_usd"].cumsum(),
                "-LVR": -df["lvr_usd"].cumsum(),
                "Funding": df["funding_usd"].cumsum(),
                "-Hedge cost": -df["hedge_cost_usd"].cumsum(),
                "-Gas": -df["gas_usd"].cumsum(),
            }
        )
        st.line_chart(attr)
    with tab_daily:
        daily = (
            df[["fee_usd", "lvr_usd", "funding_usd", "hedge_cost_usd", "gas_usd"]]
            .resample("1D").sum()
        )
        daily.columns = ["Fees", "LVR", "Funding", "Hedge cost", "Gas"]
        # Costs shown as negative so stacked bars visually net out
        for col in ["LVR", "Hedge cost", "Gas"]:
            daily[col] = -daily[col]
        st.bar_chart(daily)
        st.caption(
            "Positive bars = income (fees, funding-received), negative = "
            "costs. Net = sum of the stack per day."
        )

    st.subheader("ETH price and LP delta")
    st.line_chart(df[["price"]])
    st.line_chart(df[["lp_delta", "hedge_delta"]])


def _render_rolling(res):
    cap = res.summary.get("capital_base_usd", 1)
    rolling = _rolling_apr(res.timeseries, cap, window_days=30)
    if rolling.empty:
        st.info("Not enough data for 30-day rolling windows.")
        return
    st.subheader("30-day rolling APR")
    col_a, col_b = st.columns([2, 1])
    with col_a:
        st.line_chart(rolling.rename("Rolling 30d APR (%)"))
    with col_b:
        st.caption("Distribution")
        st.write(
            pd.DataFrame({
                "stat": ["min", "p25", "median", "p75", "max", "std"],
                "APR (%)": [
                    rolling.min().round(2),
                    rolling.quantile(0.25).round(2),
                    rolling.median().round(2),
                    rolling.quantile(0.75).round(2),
                    rolling.max().round(2),
                    rolling.std().round(2),
                ],
            }).set_index("stat")
        )


def _render_sweep(a: bt.Assumptions, swaps, cex, funding):
    st.subheader("Sweep: range width × hedge band")
    with st.spinner("Sweeping…"):
        tbl = bt.sweep_swap_level(a, swaps, cex, funding)

    # Plotly heatmap with per-cell hover including Sharpe + fees/LVR if
    # available in the sweep columns
    net_pivot = tbl.pivot(index="range_width", columns="hedge_band", values="net_apr")
    sharpe_pivot = (
        tbl.pivot(index="range_width", columns="hedge_band", values="sharpe")
        if "sharpe" in tbl.columns else None
    )
    fee_over_lvr_pivot = (
        tbl.pivot(index="range_width", columns="hedge_band", values="fee_over_lvr")
        if "fee_over_lvr" in tbl.columns else None
    )

    hover = np.empty(net_pivot.shape, dtype=object)
    for i, r in enumerate(net_pivot.index):
        for j, c in enumerate(net_pivot.columns):
            parts = [
                f"Range ±{r:.0%}",
                f"Band {c:.1%}",
                f"Net APR: {net_pivot.iloc[i, j]:.2f}%",
            ]
            if sharpe_pivot is not None:
                parts.append(f"Sharpe: {sharpe_pivot.iloc[i, j]:.2f}")
            if fee_over_lvr_pivot is not None:
                parts.append(f"Fees/LVR: {fee_over_lvr_pivot.iloc[i, j]:.2f}")
            hover[i, j] = "<br>".join(parts)

    fig = go.Figure(data=go.Heatmap(
        z=net_pivot.values,
        x=[f"{c:.1%}" for c in net_pivot.columns],
        y=[f"±{r:.0%}" for r in net_pivot.index],
        colorscale="RdYlGn",
        zmid=0,
        hovertext=hover,
        hoverinfo="text",
        colorbar=dict(title="Net APR (%)"),
    ))
    fig.update_layout(
        xaxis_title="Hedge band",
        yaxis_title="Range width",
        height=400,
        margin=dict(l=60, r=20, t=20, b=40),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Best cell callout
    best_idx = tbl["net_apr"].idxmax()
    best = tbl.loc[best_idx]
    st.info(
        f"**Best cell:** range ±{best['range_width']:.0%} × band "
        f"{best['hedge_band']:.1%} → net APR {best['net_apr']:.2f}%"
        + (f", Sharpe {best['sharpe']:.2f}" if "sharpe" in tbl.columns else "")
    )
    with st.expander("Full sweep table"):
        st.dataframe(tbl, use_container_width=True)


def _render_comparison():
    runs = st.session_state["runs"]
    if len(runs) < 2:
        return
    st.subheader("Run comparison")
    labels = [f"{i+1}. {r['label']}" for i, r in enumerate(runs)]
    picked = st.multiselect(
        "Overlay runs (up to 5)", labels, default=labels[-2:],
    )
    if not picked:
        return
    picked_idx = [labels.index(p) for p in picked]

    # Overlay cumulative net
    overlay = pd.DataFrame()
    for i in picked_idx:
        r = runs[i]
        overlay[r["label"]] = r["ts_df"]["cum_net"]
    st.line_chart(overlay)

    # KPI table
    kpi = pd.DataFrame(
        {
            r["label"]: {
                "engine": r["engine"],
                "net APR (%)": r["summary"].get("net_apr_pct"),
                "fees APR (%)": r["summary"].get("fee_apr_pct"),
                "LVR APR (%)": r["summary"].get("lvr_apr_pct"),
                "funding APR (%)": r["summary"].get("funding_apr_pct"),
                "hedge cost APR (%)": r["summary"].get("hedge_cost_apr_pct"),
                "Sharpe": r["summary"].get("sharpe"),
                "% in range": r["summary"].get("pct_time_in_range"),
            }
            for r in [runs[i] for i in picked_idx]
        }
    )
    st.dataframe(kpi, use_container_width=True)


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #

tab_run, tab_docs = st.tabs(["Backtest", "Documentation"])

# --------------------------------------------------------------------------- #
# Run tab
# --------------------------------------------------------------------------- #

with tab_run:
    run = st.button("Run backtest", type="primary", use_container_width=True)

if not run:
    with tab_run:
        st.info("Set your assumptions in the sidebar, then click **Run backtest**.")
        _render_comparison()  # still show history if it exists

if run:
    with tab_run:
        try:
            a = bt.Assumptions.from_dict(st.session_state["assumptions"])
            is_demo = engine_key == "swap_level" and mode.startswith("Demo")

            if engine_key == "swap_level":
                loaded = _load_swap_level_inputs()
                if loaded is None:
                    st.stop()
                swaps, cex, funding = loaded
                st.caption(
                    f"Loaded {len(swaps):,} swaps · CEX {cex.index[0]} → {cex.index[-1]}"
                )
                with st.spinner("Running swap-level backtest…"):
                    res = bt.run_swap_level(a, swaps, cex, funding)
            else:
                loaded = _load_hourly_inputs()
                if loaded is None:
                    st.stop()
                klines, funding = loaded
                st.caption(
                    f"Loaded {len(klines):,} bars · "
                    f"{klines.index[0]} → {klines.index[-1]}"
                )
                with st.spinner("Running hourly backtest…"):
                    res = bt.run_hourly(a, klines, funding)

            _push_run(res, run_label, st.session_state["assumptions"])

        except Exception as e:
            st.exception(e)
            st.stop()

        _render_verdict(res, is_demo)
        _render_metrics(res)
        _render_charts(res)

        if do_rolling:
            _render_rolling(res)

        with st.expander("Full summary"):
            st.json(res.summary)
        with st.expander("Timeseries (first 500 rows)"):
            st.dataframe(res.timeseries.head(500))

        if do_sweep and engine_key == "swap_level":
            _render_sweep(a, swaps, cex, funding)

        _render_comparison()


# --------------------------------------------------------------------------- #
# Documentation tab (nested sub-tabs)
# --------------------------------------------------------------------------- #

with tab_docs:
    st.markdown(
        "This app backtests a **delta-hedged Uniswap v3 LP position**. "
        "You provide liquidity to ETH/USDC inside a price band, and short an "
        "equivalent amount of ETH on Binance perpetual futures so your value "
        "stays roughly independent of ETH price. The question is whether the "
        "fees you earn on-chain cover LVR (loss-vs-rebalancing), funding, "
        "and the cost of running the hedge."
    )
    st.markdown(
        "> **Net PnL = Fees − LVR − Funding cost − Hedge execution − Gas**"
    )

    d_engines, d_data, d_inputs, d_outputs, d_interp = st.tabs(
        ["Engines", "Data", "Inputs", "Outputs", "Interpreting"]
    )

    with d_engines:
        st.markdown(
            """
| | swap_level (reference) | hourly (fast fallback) |
|---|---|---|
| Data granularity | Swap-level pool events + 1s CEX | Hourly OHLC only |
| LVR | Path-exact from swap-level events | Not modelled |
| Hedge execution | Half-spread + taker fee + linear impact | Flat bps slippage |
| Repositioning | Buffer-based, gas + swap cost modelled | Not modelled |
| Data required | Swap parquet + Binance klines/aggTrades | Binance klines only |
| Speed | Slower (millions of events) | Fast |
| Best for | Accurate PnL attribution | Quick parameter exploration |

The swap-level engine has a **Demo (synthetic tape)** mode that generates
GBM prices and simulated swap events — useful for validating the engine or
exploring parameters without downloading anything.
"""
        )

    with d_data:
        st.markdown(
            """
- **Uniswap swaps** — parquet with per-swap events. Two ways to obtain:
  1. **Auto-fetch from the subgraph** via **Fetch data → Fetch swap
     events**. Requires `THEGRAPH_API_KEY` in your env
     ([free key here](https://thegraph.com/studio/)).
     Slow — ~2–3M events per month for ETH/USDC 0.05%.
  2. Or drop your own `*swap*.parquet` dump in the repo root or `data/`
     — cryo, Dune, and Allium exports all work. Required columns:
     `block_number, block_time, amount0, amount1, sqrt_price_x96,
     liquidity, tick` (aliases handled automatically).
- **Binance klines / aggTrades** — auto-downloaded from
  `data.binance.vision`. Cached in `data/`. `load_cex_prices` handles
  both formats and resamples to 1-second bars internally.
- **Binance funding rates** — 8-hour funding history, auto-downloaded.
- **Uniswap pool hourly aggregates** — optional, needs a Graph API key.
  Disabled by default; 1-minute klines suffice for exploration.
"""
        )

    with d_inputs:
        st.markdown(
            """
### Fetch data
- **From / To (YYYY-MM)** — month range for Binance CSV downloads. Months
  already cached on disk are skipped.
- **CEX price resolution** — klines 1m (default, ~5 MB/mo) / klines 1h
  (smallest) / aggTrades (~3.5 GB/mo, only for path-exact LVR).

### Backtest window
- Optional YYYY-MM-DD start/end. Clips *all* input series before handing
  them to the engine, so you can backtest a slice of downloaded data
  without re-downloading.

### Engine
- **swap_level** (reference, needs swap events) vs **hourly** (fast
  fallback, klines only).

### LP leg
- **Capital deployed** — notional put into the LP position.
- **Range width** — position covers `[spot × (1−w), spot × (1+w)]`.
  Tighter = more fees per dollar, but exits range more often.
- **Absolute price bounds** (hourly only) — pin the range.
- **Reposition** — re-centre after N hours out of range. Costs gas +
  swap slippage.
- **Pool share** / **volume multiplier** (hourly) — proxies for fees
  when we don't have real pool volume data.

### Hedge leg
- **Hedge band** (swap_level) — rebalance when residual delta exceeds
  this fraction of max ETH delta.
- **Rebalance mode** (hourly) — *threshold* (drift %) or *periodic*
  (fixed hours).
- **Binance taker fee** — perp taker fee (bps).
- **Half-spread** (swap_level) — bid-ask approximation when using klines.
- **Linear impact** (swap_level) — market impact model for large clips.
- **Flat slippage** (hourly) — simpler analog.
- **Perp leverage / margin buffer** — `margin = notional / leverage × buffer`.

### Mechanics (swap_level)
- **Hedge decision grid (s)** — how often the hedge is checked.
- **Fee markout window (s)** — CEX mark delay for measuring fill quality.

### LP fee tier — 1 / 5 / 30 / 100 bps.

### Run controls
- **Range × hedge-band sweep** — Cartesian grid.
- **Rolling-window distribution** — split into 30-day windows, plot APR
  variation across them (regime dependency check).
- **Run label** — annotates this run in the comparison table.
"""
        )

    with d_outputs:
        st.markdown(
            """
### Verdict banner
Green if net APR > 0 (fees cover costs), red otherwise. Shows the
capital-base breakdown so you can see how much of it is LP vs hedge
margin.

### Top-row metrics
- **Net PnL (USD)** — total PnL over the backtest window.
- **Fees APR** — annualised fee income as % of capital base.
- **LVR APR** — annualised loss-vs-rebalancing. Path-exact in
  swap_level, n/a in hourly.
- **Funding APR** — annualised funding paid/received on the perp.
  Positive = you collect funding.
- **Hedge cost APR** — taker fees + spread + impact + slippage.

### Second-row metrics
- **Fees / LVR** — coverage ratio.
- **% time in range** — fees only accrue when in-range.
- **Sharpe (daily)** — daily-net-PnL Sharpe, annualised by √365.
- **Max drawdown** — worst peak-to-trough dip.

### Charts
- **Cumulative PnL** — running sum of net PnL.
- **PnL attribution** — cumulative (line) or daily (stacked bar).
  Cumulative shows which term is winning; daily shows which day cost
  or earned what.
- **ETH price** — reference price used to compute delta and mark hedge.
- **LP delta vs Hedge delta** — should track closely; gaps are
  discrete-hedge error.
- **30-day rolling APR** (if enabled) — regime dependency check.

### Sweep (swap_level only)
Interactive Plotly heatmap over `range_width × hedge_band`. Hover shows
per-cell Net APR, Sharpe, Fees/LVR. Callout points at the best cell.

### Run comparison
Overlay of cumulative-PnL curves for up to 5 stored runs, plus a KPI
table. Runs are pushed onto a bounded stack in session state; the last
five persist across button clicks in the same session (not across page
reloads).
"""
        )

    with d_interp:
        st.markdown(
            """
- If **Fees APR < |LVR APR|**, the position loses money to informed
  flow faster than it earns from uninformed flow — no hedge
  configuration can save it.
- If **Fees APR > |LVR APR|** but **Net APR < 0**, hedge costs or
  funding are eating the surplus. Try wider hedge band, higher fee
  tier, or a period with more favourable funding.
- **% time in range** below ~80% usually means the range is too tight
  for the vol regime. Widen it and re-run.
- The **sweep** helps: look for the cell with the highest net APR *and*
  reasonable Sharpe — a great APR with negative Sharpe means one lucky
  window carried the result.
- The **rolling-APR distribution** shows regime dependency: a strategy
  with median APR ≈ 0 but wide spread is a coin flip, not an edge.
- Use **run comparison** to A/B-test parameter changes without losing
  the baseline.
"""
        )
        st.divider()
        st.subheader("Assumptions dataclass (source of truth)")
        st.help(bt.Assumptions)
