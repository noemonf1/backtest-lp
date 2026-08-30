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

import contextlib
import json
import logging
import os
import time
import traceback
from copy import deepcopy
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

import backtest as bt

REPO = Path(__file__).parent
DATA = REPO / "data"

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #
#
# One named logger for the app. Railway pipes stderr straight to its log
# viewer, so we don't need file handlers or rotation. Structured format
# = "TIMESTAMP LEVEL logger.module: MESSAGE" -- greppable in the Railway
# UI and searchable across deploys.
#
# `basicConfig` is idempotent-ish (no-ops if root already has handlers)
# so this is safe under streamlit's script-rerun model.

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("backtest_app")


@contextlib.contextmanager
def _log_stage(stage: str, **fields):
    """Log 'stage start' + 'stage done in Xs' around a block, and
    'stage failed after Xs' if it raises. Structured fields render as
    `k=v` on the same line for grep-friendliness.

    Usage:
        with _log_stage("swap_level_backtest", swaps=len(swaps), days=30):
            res = bt.run_swap_level(a, swaps, cex, funding)
    """
    ctx = " ".join(f"{k}={v}" for k, v in fields.items())
    log.info(f"{stage} start {ctx}".rstrip())
    t0 = time.time()
    try:
        yield
    except Exception as e:
        dt = time.time() - t0
        log.error(
            f"{stage} failed after {dt:.2f}s {ctx} error={type(e).__name__}: {e}\n"
            f"{traceback.format_exc()}"
        )
        raise
    else:
        dt = time.time() - t0
        log.info(f"{stage} done in {dt:.2f}s {ctx}".rstrip())

# Bump when the Assumptions field set changes in a way that could
# silently mis-load an older config. Loads with a lower version fall
# through to a warning; loads with a higher version warn about unknown
# fields.
CONFIG_SCHEMA_VERSION = 1

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
        cfg["start"] = st.text_input(
            "From (YYYY-MM)", value=cfg["start"], key="fetch_start",
            help="Earliest month to download (inclusive). Binance packages "
                 "one file per calendar month; already-cached months are "
                 "skipped.",
        )
        cfg["end"] = st.text_input(
            "To (YYYY-MM)", value=cfg["end"], key="fetch_end",
            help="Latest month to download (inclusive). Set equal to "
                 "**From** for a single-month fetch.",
        )

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
                    with _log_stage(
                        "binance_fetch",
                        start=cfg["start"], end=cfg["end"],
                        kind=cfg["cex_kind"], interval=cfg["klines_interval"],
                    ):
                        result = bt.ensure_all_data(
                            start=cfg["start"].strip(),
                            end=cfg["end"].strip(),
                            cex_kind=cfg["cex_kind"],
                            klines_interval=cfg["klines_interval"],
                            include_pool_hourly=False,
                        )
                    n_dl = sum(len(v["downloaded"]) for v in result["binance"].values())
                    n_skip = sum(len(v["skipped"]) for v in result["binance"].values())
                    log.info(f"binance_fetch result downloaded={n_dl} skipped={n_skip}")
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
            "Swaps from (YYYY-MM-DD)", value=cfg["swap_start"], key="fetch_swap_start",
            help="First day to fetch swap events from (inclusive). "
                 "Subgraph pagination is slow — start narrow (~1 week) "
                 "and extend once you know throughput.",
        )
        cfg["swap_end"] = st.text_input(
            "Swaps to (YYYY-MM-DD)", value=cfg["swap_end"], key="fetch_swap_end",
            help="Last day to fetch (inclusive). Longer ranges take "
                 "proportionally longer; ~2–3M swaps per month for "
                 "ETH/USDC 0.05%.",
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
        with _log_stage(
            "swap_fetch", start=swap_start, end=swap_end,
        ):
            r = bt.ensure_pool_swaps(
                swap_start.strip(), swap_end.strip(),
                progress_cb=_cb,
                cancel_cb=lambda: st.session_state.get("cancel_swap_fetch", False),
            )
        prog.empty()
        status.empty()
        tag = "already cached" if r["cached"] else "downloaded"
        cancelled = st.session_state["cancel_swap_fetch"]
        log.info(
            f"swap_fetch result rows={r['rows']} cached={r['cached']} "
            f"cancelled={cancelled} path={r['path']}"
        )
        if cancelled:
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
        # Wrap the raw assumption dict in an envelope with schema_version
        # so future field renames/removals can be detected on load. Bump
        # CONFIG_SCHEMA_VERSION whenever the field set changes.
        envelope = {
            "schema_version": CONFIG_SCHEMA_VERSION,
            "assumptions": st.session_state["assumptions"],
        }
        cfg_json = json.dumps(envelope, indent=2)
        st.download_button(
            "Download current config (JSON)",
            data=cfg_json,
            file_name="backtest_config.json",
            mime="application/json",
            width="stretch",
        )
        upload = st.file_uploader(
            "Load config", type=["json"], key="cfg_upload",
            help="Restore a previously downloaded config JSON. Loading "
                 "triggers a full-page rerun so sliders reflect the new "
                 "values immediately.",
        )
        # Streamlit's UploadedFile has had `.file_id` since 1.31 but `.id`
        # in older versions. Fall back to name+size hash so we don't crash
        # on either.
        if upload is not None:
            uid = (
                getattr(upload, "file_id", None)
                or getattr(upload, "id", None)
                or hash((upload.name, upload.size))
            )
            if st.session_state.get("_last_upload_id") != uid:
                try:
                    loaded = json.load(upload)
                    # Accept both envelope form and legacy flat form
                    # (pre-schema-versioning). Flat form triggers a
                    # visible warning so users know to re-export.
                    if isinstance(loaded, dict) and "assumptions" in loaded:
                        ver = loaded.get("schema_version")
                        assumptions = loaded["assumptions"]
                        if ver is None:
                            st.warning(
                                "Config has no schema_version. Loading as "
                                "best-effort — re-save to add versioning."
                            )
                        elif ver > CONFIG_SCHEMA_VERSION:
                            st.warning(
                                f"Config schema version {ver} is newer than "
                                f"this app's version {CONFIG_SCHEMA_VERSION}. "
                                "Some fields may not apply."
                            )
                    else:
                        # Legacy flat form (pre-#12 configs)
                        assumptions = loaded
                        st.warning(
                            "Legacy config format (no schema_version wrapper). "
                            "Loading anyway — re-save to migrate to the "
                            "versioned format."
                        )
                    bt.Assumptions.from_dict(assumptions)  # validate
                    st.session_state["assumptions"].update(assumptions)
                    st.session_state["_last_upload_id"] = uid
                    st.success("Config loaded — full page rerun to update sliders…")
                    st.rerun()
                except Exception as e:
                    st.error(f"Invalid config file: {e}")
        if st.button("Reset all to defaults", width="stretch"):
            st.session_state["assumptions"] = bt.Assumptions().to_dict()
            st.session_state["fetch_cfg"] = dict(DEFAULT_FETCH)
            # Clear widget-owned state so sliders re-initialise from defaults
            for k in list(st.session_state.keys()):
                if k.startswith(("fetch_", "win_", "cfg_upload")):
                    del st.session_state[k]
            st.rerun()  # full page rerun so all widgets pick up defaults


# --------------------------------------------------------------------------- #
# Sidebar: assumption sliders (grouped + fragmented)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Schema-driven assumption widgets
# --------------------------------------------------------------------------- #
#
# Each ASSUMPTION_FIELDS entry is a dict describing one slider/input:
#   field:    dict key in st.session_state["assumptions"]
#   kind:     "number" | "select_slider" | "select" | "yesno"
#   label:    displayed label
#   options:  for select_slider/select; iterable of values (sample's type
#             determines int-vs-float caster on load)
#   help:     optional tooltip
#   cast:     Python type to coerce the current stored value before display
#             (defaults inferred from `kind`)
#   kwargs:   dict of extra widget kwargs (step, min_value, format, ...)
#   derive:   optional callable(new_value, A) that runs after the widget
#             writes back -- used to keep cross-field invariants in sync
#             (e.g. binance_taker_fee = taker_fee_bps / 10_000)


def _widget_for(spec: dict, A: dict):
    """Render one assumption widget and write the result back to A[spec['field']].

    Handles the "value not in options" edge case for select/select_slider
    by defaulting to a specified fallback index instead of crashing.
    """
    field = spec["field"]
    kind = spec["kind"]
    label = spec["label"]
    kwargs = dict(spec.get("kwargs", {}))
    if "help" in spec:
        kwargs["help"] = spec["help"]

    if kind == "number":
        cast = spec.get("cast", float)
        cur = cast(A[field]) if A.get(field) is not None else cast(spec.get("default", 0))
        A[field] = st.number_input(label, value=cur, **kwargs)

    elif kind == "select_slider":
        options = spec["options"]
        cast = spec.get("cast", float)
        cur = cast(A[field])
        # select_slider requires the current value be in the options list
        if cur not in options:
            cur = options[len(options) // 2]  # middle option as fallback
        A[field] = st.select_slider(label, options=options, value=cur, **kwargs)

    elif kind == "select":
        options = spec["options"]
        cast = spec.get("cast", type(options[0]))
        cur = cast(A[field]) if A.get(field) is not None else options[0]
        idx = options.index(cur) if cur in options else spec.get("fallback_index", 0)
        A[field] = st.selectbox(label, options=options, index=idx, **kwargs)

    elif kind == "yesno":
        A[field] = st.selectbox(
            label, options=["Yes", "No"],
            index=0 if A[field] else 1, **kwargs,
        ) == "Yes"

    else:
        raise ValueError(f"unknown widget kind: {kind!r}")

    if "derive" in spec:
        spec["derive"](A[field], A)


# One row per widget. Editing / adding / removing an assumption slider
# is a matter of adding a dict here rather than a new block of widget code.
_TAKER_FEE_OPTS = [2.0, 3.0, 4.0, 4.5, 5.0]
_LEVERAGE_OPTS = [1, 2, 3, 4, 5, 10]

ASSUMPTION_FIELDS = {
    # Position sizing
    "capital_usd": {
        "field": "capital_usd", "kind": "number", "label": "Capital deployed (USD)",
        "kwargs": {"min_value": 1_000.0, "step": 50_000.0},
        "help": "Notional USD you're allocating to the LP position. "
                "Together with `Perp leverage × margin buffer`, this "
                "determines the **capital base** used as the APR "
                "denominator.",
    },
    "range_width_symm": {
        "field": "range_width", "kind": "select_slider",
        "label": "Range width (± fraction around spot)",
        "options": [0.02, 0.05, 0.10, 0.15, 0.25, 0.50, 0.75],
        "help": "Width of the LP price band, symmetric around current "
                "spot. A value of 0.10 means the range spans [spot × 0.90, "
                "spot × 1.10]. Tighter = higher fees per dollar but exits "
                "range more often.",
    },
    "lower_price": {
        "field": "lower_price", "kind": "number",
        "label": "Lower price (USDC/ETH)",
        "kwargs": {"step": 50.0}, "default": 1800.0,
        "help": "Absolute lower bound of the LP range. Only fees are "
                "earned while spot is between this and the upper bound.",
    },
    "upper_price": {
        "field": "upper_price", "kind": "number",
        "label": "Upper price (USDC/ETH)",
        "kwargs": {"step": 50.0}, "default": 2200.0,
        "help": "Absolute upper bound of the LP range. Above this, the "
                "position is 100% USDC and earns no fees until price "
                "returns.",
    },
    "pool_share": {
        "field": "pool_share", "kind": "number",
        "label": "Our share of pool active liquidity",
        "kwargs": {"min_value": 0.00001, "max_value": 0.5, "step": 0.0005,
                   "format": "%.5f"},
        "help": "Assumed fraction of the pool's in-range liquidity you "
                "own. Fees earned = pool_volume × fee_tier × share. "
                "Only used by the hourly engine (swap_level derives this "
                "from real events).",
    },
    "pool_volume_multiplier": {
        "field": "pool_volume_multiplier", "kind": "number",
        "label": "Pool volume as fraction of Binance quote volume",
        "kwargs": {"min_value": 0.001, "max_value": 1.0, "step": 0.01},
        "help": "Rough proxy — the hourly engine doesn't have real Uniswap "
                "volume, so it multiplies Binance quote volume by this. "
                "8% is a reasonable default for ETH/USDC.",
    },
    "reposition": {
        "field": "reposition", "kind": "yesno",
        "label": "Reposition when out of range?",
        "help": "If **Yes**, re-centre the LP band once price has been "
                "outside for `Reposition buffer` hours. Costs gas + LP "
                "swap slippage per reposition.",
    },
    "reposition_buffer_hours": {
        "field": "reposition_buffer_hours", "kind": "number",
        "label": "Reposition buffer (hours)",
        "kwargs": {"min_value": 0.0, "step": 1.0},
        "help": "How many continuous hours out-of-range before triggering "
                "a reposition. Higher = fewer repositions but more time "
                "earning zero fees. 0 = reposition immediately.",
    },

    # Hedge policy
    "hedge_band": {
        "field": "hedge_band", "kind": "select_slider",
        "label": "Hedge band (fraction of max delta)",
        "options": [0.005, 0.01, 0.02, 0.03, 0.05, 0.10, 0.25],
        "help": "Rebalance the perp hedge when residual delta drift "
                "exceeds this fraction of the position's max ETH delta. "
                "Tighter = more precise hedge but more taker fees paid.",
    },
    "rebalance_mode": {
        "field": "rebalance_mode", "kind": "select",
        "label": "Rebalance mode", "options": ["threshold", "periodic"],
        "help": "**threshold** = trigger on delta drift. **periodic** = "
                "trigger on a fixed clock (every N hours). Threshold "
                "adapts to volatility; periodic gives predictable costs.",
    },
    "rebalance_threshold_pct": {
        "field": "rebalance_threshold_pct", "kind": "select_slider",
        "label": "Rebalance threshold (fraction of |delta|)",
        "options": [0.01, 0.02, 0.05, 0.10, 0.20],
        "help": "Hourly-engine analog of hedge band. Rebalance when "
                "residual delta drifts by this fraction of current |delta|.",
    },
    "rebalance_period_h": {
        "field": "rebalance_period_h", "kind": "select",
        "label": "Rebalance every (hours)",
        "options": [1, 4, 8, 12, 24, 48], "fallback_index": 4,
        "help": "Periodic-mode: force a rebalance every N hours "
                "regardless of drift. Trades hedge tracking error for "
                "predictable execution cost.",
    },
    "leverage": {
        "field": "leverage", "kind": "select",
        "label": "Perp leverage", "options": _LEVERAGE_OPTS,
        "fallback_index": 3,
        "help": "Perp margin leverage. Margin required = "
                "max_notional / leverage × margin_buffer. Higher = less "
                "capital tied up but closer to liquidation.",
    },
    "margin_buffer": {
        "field": "margin_buffer", "kind": "number",
        "label": "Margin buffer (× notional/leverage)",
        "kwargs": {"min_value": 1.0, "step": 0.1},
        "help": "Multiplier on the minimum margin for liquidation "
                "safety. 1.5 means you post 50% more than the minimum. "
                "The extra sits in your capital base, dragging APR.",
    },

    # Cost model
    "fee_tier_bps": {
        # This one edits fee_tier through a bps-scaled selectbox and
        # writes back the fraction via derive().
        "field": "fee_tier", "kind": "select",
        "label": "LP fee tier (bps)", "options": [1, 5, 30, 100],
        "cast": lambda v: int(round(float(v) * 10_000)),
        "help": "Uniswap pool fee tier. 1 = 0.01%, 5 = 0.05%, "
                "30 = 0.30%, 100 = 1.00%. Default 5 = ETH/USDC 0.05% pool.",
        "derive": lambda new_bps, A: A.__setitem__("fee_tier", new_bps / 10_000),
    },
    "taker_fee_bps": {
        "field": "taker_fee_bps", "kind": "select",
        "label": "Binance taker fee (bps)", "options": _TAKER_FEE_OPTS,
        "fallback_index": 3,
        "help": "Binance perp taker fee. 5 bps = VIP0 default, 4.5 = "
                "BNB discount, lower with VIP tier. Applies to every "
                "hedge rebalance trade.",
        # Keep binance_taker_fee in sync so downstream engines see both.
        "derive": lambda new_bps, A: A.__setitem__("binance_taker_fee", new_bps / 10_000),
    },
    "half_spread_bps": {
        "field": "half_spread_bps", "kind": "number",
        "label": "Half-spread when using klines (bps)",
        "kwargs": {"min_value": 0.0, "step": 0.1},
        "help": "Because klines only give last-trade price, this "
                "approximates the bid/ask you'd actually cross. 0.5 bps "
                "is typical for ETH-perp in normal conditions.",
    },
    "impact_bps_per_100k": {
        "field": "impact_bps_per_100k", "kind": "number",
        "label": "Linear impact (bps per $100k clip)",
        "kwargs": {"min_value": 0.0, "step": 0.1},
        "help": "Linear market-impact model for larger rebalance trades: "
                "impact_bps = coefficient × (clip_size / $100k). Set to 0 "
                "to ignore impact (safe for small notionals).",
    },
    "gas_usd_per_reposition": {
        "field": "gas_usd_per_reposition", "kind": "number",
        "label": "Gas per reposition (USD)",
        "kwargs": {"min_value": 0.0, "step": 5.0},
        "help": "Cost to re-mint the LP position at a new range. Depends "
                "on gas price × complexity; $40 is a reasonable Ethereum "
                "mainnet estimate.",
    },
    "lp_rebalance_swap_bps": {
        "field": "lp_rebalance_swap_bps", "kind": "number",
        "label": "Swap cost to re-ratio on reposition (bps)",
        "kwargs": {"min_value": 0.0, "step": 0.5},
        "help": "Slippage paid when swapping token amounts to match the "
                "new range's target ratio during reposition. Rough proxy "
                "since pool depth varies.",
    },
    "slippage_bps": {
        "field": "slippage_bps", "kind": "number",
        "label": "Flat slippage on hedge trades (bps)",
        "kwargs": {"min_value": 0.0, "step": 0.5},
        "help": "Hourly-engine analog of half-spread + impact combined "
                "into one flat number. 1 bp is a reasonable ETH-perp "
                "default.",
    },

    # Mechanics
    "grid_seconds": {
        "field": "grid_seconds", "kind": "select",
        "label": "Hedge decision grid (s)",
        "options": [10, 30, 60, 120, 300], "fallback_index": 2,
        "help": "How often the swap_level engine checks whether to "
                "rebalance the hedge. Finer = closer tracking but more "
                "compute; coarser = fewer rebalances at cost of "
                "discrete-hedge error.",
    },
    "markout_seconds": {
        "field": "markout_seconds", "kind": "select",
        "label": "Fee markout window (s)",
        "options": [0, 60, 300, 900], "fallback_index": 0,
        "help": "CEX mark delay for measuring LP fill quality. 0 = mark "
                "at block time; larger windows show how the fill looked "
                "against a slightly-later reference price.",
    },
}


def _render_fields(A: dict, keys: list[str]) -> None:
    for k in keys:
        _widget_for(ASSUMPTION_FIELDS[k], A)


@st.fragment
def _sidebar_assumptions(engine_key: str):
    """Render the assumption sliders in 4 expanders and 1 run-controls
    section. Writes slider values through to st.session_state['assumptions']
    so the Run flow picks them up.

    Fragmented so slider changes only rerun THIS block, not the whole
    page (fetch expanders, engine selector, data-source dropdowns,
    etc.). `engine_key` is passed as an argument, not read via closure,
    so a fragment-only rerun sees the same value as the last full-page
    render -- which is correct, because changing the engine triggers a
    full-page rerun anyway (it's a selectbox outside this fragment)."""
    A = st.session_state["assumptions"]

    with st.expander("Position sizing", expanded=True):
        _render_fields(A, ["capital_usd"])
        if engine_key == "swap_level":
            _render_fields(A, ["range_width_symm"])
            A["lower_price"] = None
            A["upper_price"] = None
        else:
            range_mode = st.selectbox(
                "Range specification",
                ["Symmetric ± around spot", "Absolute price bounds"],
                help="**Symmetric** centres the range on current spot "
                     "and grows both ways by the same %. **Absolute** "
                     "pins fixed lower / upper prices regardless of "
                     "where spot is.",
            )
            if range_mode.startswith("Symmetric"):
                _render_fields(A, ["range_width_symm"])
                A["lower_price"] = A["upper_price"] = None
            else:
                _render_fields(A, ["lower_price", "upper_price"])
            _render_fields(A, ["pool_share", "pool_volume_multiplier"])

        if engine_key == "swap_level":
            st.divider()
            st.caption("**Repositioning**")
            _render_fields(A, ["reposition", "reposition_buffer_hours"])

    with st.expander("Hedge policy", expanded=True):
        if engine_key == "swap_level":
            _render_fields(A, ["hedge_band"])
        else:
            _render_fields(A, ["rebalance_mode"])
            if A["rebalance_mode"] == "threshold":
                _render_fields(A, ["rebalance_threshold_pct"])
            else:
                _render_fields(A, ["rebalance_period_h"])
        _render_fields(A, ["leverage", "margin_buffer"])

    with st.expander("Cost model", expanded=False):
        _render_fields(A, ["fee_tier_bps", "taker_fee_bps"])
        if engine_key == "swap_level":
            _render_fields(A, [
                "half_spread_bps", "impact_bps_per_100k",
                "gas_usd_per_reposition", "lp_rebalance_swap_bps",
            ])
        else:
            _render_fields(A, ["slippage_bps"])

    if engine_key == "swap_level":
        with st.expander("Mechanics (advanced)", expanded=False):
            _render_fields(A, ["grid_seconds", "markout_seconds"])

    with st.expander("Analysis + Run", expanded=True):
        do_sweep = False
        if engine_key == "swap_level":
            do_sweep = st.checkbox(
                "Run range × hedge-band sweep", value=False,
                help="After the main backtest, run a 2-axis grid sweep "
                     "on top and show a heatmap. Slow (25 backtests on "
                     "real data can take minutes) — cancellable.",
            )
        do_rolling = st.checkbox(
            "Rolling-window distribution (30d)", value=False,
            help="Split the backtest into overlapping 30-day windows and "
                 "show the APR variation across them. Reveals whether a "
                 "positive net APR is stable or driven by one lucky window.",
        )
        do_by_month = st.checkbox(
            "Backtest by month", value=False,
            help="Run one backtest per calendar month and show a monthly "
                 "APR bar chart. Reveals regime dependency. Needs ≥ 2 "
                 "months of loaded data.",
        )

        stress_cfg = None
        if engine_key == "hourly":
            do_stress = st.checkbox(
                "Stress test (price shock)", value=False,
                help="Overlay a synthetic price move on the last N days "
                     "of klines before running the engine. Hourly-engine "
                     "only — swap-level uses baked-in real swap events.",
            )
            if do_stress:
                shock_days = st.number_input(
                    "Shock over last N days", min_value=1, max_value=90, value=7,
                    help="How many days at the END of the loaded window "
                         "to apply the shock to. Earlier data is left "
                         "untouched.",
                )
                shock_pct = st.slider(
                    "Total price move (%)", min_value=-50, max_value=50, value=-20,
                    help="Total price change over the shock window. "
                         "Negative = crash, positive = pump. Applied "
                         "uniformly to OHLC so bar shape is preserved.",
                )
                shock_shape = st.selectbox(
                    "Shape", ["linear", "step"],
                    help="**linear:** smooth ramp from 0% to `pct` over the "
                         "shock window. **step:** instant jump at the start "
                         "of the window (worst-case impulse test).",
                )
                stress_cfg = {
                    "days": int(shock_days),
                    "pct": float(shock_pct) / 100.0,
                    "shape": str(shock_shape),
                }

        run_label = st.text_input(
            "Run label (optional)", value="",
            placeholder="e.g. 'wide range, tight band'",
            help="Annotates this run in the comparison table. Blank "
                 "auto-generates 'Run N'. Only kept in-memory for the "
                 "session (last 5 runs).",
        )

    # Store the run-controls for the main tab to read. Can't return
    # them (a @st.fragment returns None regardless of what the function
    # returns), so persist them via session_state.
    st.session_state["_run_controls"] = {
        "do_sweep": do_sweep,
        "do_rolling": do_rolling,
        "do_by_month": do_by_month,
        "run_label": run_label,
        "stress_cfg": stress_cfg,
    }


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
            help="**Demo** generates a synthetic GBM CEX tape + fake pool "
                 "swaps (no network). **Real data** uses cached parquet + "
                 "Binance CSVs from `data/`.",
        )
        if mode.startswith("Demo"):
            demo_days = st.slider(
                "Demo days", 5, 120, 25, step=5,
                help="Length of the synthetic tape in days. Longer = more "
                     "statistically stable results but slower to generate.",
            )
            demo_vol = st.slider(
                "Annualised vol", 0.20, 1.50, 0.65, step=0.05,
                help="σ used in the GBM price path. 0.65 ≈ typical ETH "
                     "realised vol; crank higher to stress the hedge.",
            )
            demo_s0 = st.number_input(
                "Starting ETH price (USD)", value=3000.0, step=100.0,
                help="Initial price for the GBM tape. Affects nothing "
                     "material other than the scale of dollar figures.",
            )
            demo_seed = st.number_input(
                "RNG seed", value=7, step=1,
                help="Deterministic seed for the GBM tape. Different "
                     "seeds give different price paths — useful for "
                     "checking whether a result is a one-off.",
            )
        else:
            swap_files = _list_swap_files()
            if not swap_files:
                st.warning(
                    "No swap files found. Use **Fetch data → Fetch swap "
                    "events** above (requires `THEGRAPH_API_KEY`) or drop a "
                    "cryo/Dune/Allium dump named `*swap*.parquet` in the "
                    "repo root or `data/`."
                )
            swaps_path = st.selectbox(
                "Swaps file", swap_files or ["(none)"],
                help="Per-swap event parquet. Auto-detected from any "
                     "`*swap*.parquet` under the repo root or `data/`.",
            )

            cex_files = _list_files("*aggTrades*.csv") + _list_files("*klines*.csv")
            fund_files_avail = _list_files("*fundingRate*.csv")
            if not cex_files or not fund_files_avail:
                st.info(
                    "Missing Binance CSVs. Use **Fetch data** above to "
                    "download them (they're cached in `data/`)."
                )
            cex_path = st.selectbox(
                "CEX price file", cex_files or ["(none)"],
                help="Binance klines or aggTrades CSV. Provides the CEX "
                     "mark used for LVR and hedge PnL. Missing files are "
                     "auto-fetched on Run.",
            )
            fund_files = ["(none — assume 0)"] + fund_files_avail
            funding_path = st.selectbox(
                "Funding file", fund_files,
                help="Binance perp funding-rate CSV. **(none — assume 0)** "
                     "sets funding to zero (useful for isolating LVR/fees).",
            )
    else:
        mode = st.selectbox(
            "Mode", ["Live fetch from Binance", "Local CSV files"],
            help="**Live** pulls the last N days of klines + funding "
                 "directly from Binance (no local files). **Local** uses "
                 "cached CSVs from `data/`.",
        )
        if mode.startswith("Live"):
            live_days = st.slider(
                "Lookback (days)", 7, 180, 30, step=1,
                help="How many trailing days of Binance data to pull. "
                     "Live fetch is capped by Binance's rate limits — "
                     "keep under 180 to avoid throttling.",
            )
            interval = st.selectbox(
                "Bar interval", ["1h", "4h", "1d"], index=0,
                help="Kline bar granularity. The hourly engine treats "
                     "each bar as one hedge decision; 1h is standard.",
            )
        else:
            cex_files = _list_files("*aggTrades*.csv") + _list_files("*klines*.csv")
            fund_files_avail = _list_files("*fundingRate*.csv")
            if not cex_files or not fund_files_avail:
                st.info(
                    "Missing Binance CSVs. Use **Fetch data** above to "
                    "download them (they're cached in `data/`)."
                )
            cex_path = st.selectbox(
                "CEX price file", cex_files or ["(none)"],
                help="Binance klines CSV. Missing files are auto-fetched "
                     "on Run using the date range from Fetch data.",
            )
            funding_path = st.selectbox(
                "Funding file", fund_files_avail or ["(none)"],
                help="Binance perp funding-rate CSV. Empty = funding "
                     "assumed zero.",
            )

    # ---------------- Backtest window --------------------------------------- #
    with st.expander("Backtest window", expanded=False):
        st.caption(
            "Optional. If set, series are clipped to `[start, end]` "
            "before the engine sees them. Leave both blank for the full range."
        )
        win_start = st.text_input(
            "Window start (YYYY-MM-DD)", value="", key="win_start",
            placeholder="e.g. 2024-01-15",
            help="Clip the loaded data to start on this date. Useful "
                 "for isolating a sub-period from a large cached CSV "
                 "without re-downloading.",
        )
        win_end = st.text_input(
            "Window end (YYYY-MM-DD)", value="", key="win_end",
            placeholder="e.g. 2024-02-01",
            help="Clip the loaded data to end on this date (inclusive). "
                 "Leave blank to run to the last available data point.",
        )

    # ---------------- Assumption sliders (grouped + fragmented) ------------- #
    _sidebar_assumptions(engine_key)
    _controls = st.session_state.get(
        "_run_controls",
        {
            "do_sweep": False, "do_rolling": False, "do_by_month": False,
            "run_label": "", "stress_cfg": None,
        },
    )
    do_sweep = _controls["do_sweep"]
    do_rolling = _controls["do_rolling"]
    do_by_month = _controls.get("do_by_month", False)
    run_label = _controls["run_label"]
    stress_cfg = _controls.get("stress_cfg")


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


def _auto_fetch_missing_binance() -> tuple[str | None, str | None]:
    """Download Binance CSVs for the date range in the fetch expander,
    then return (cex_path, funding_path) as REPO-relative strings, or
    (None, None) on failure.

    Called from the Run flow when the user has selected 'Real data' /
    'Local CSV files' but hasn't picked (or hasn't yet downloaded) the
    Binance files. The slow subgraph swap fetch is NEVER auto-triggered
    -- users must click that button explicitly.
    """
    cfg = st.session_state["fetch_cfg"]
    with st.spinner(
        f"Auto-fetching Binance data for {cfg['start']}..{cfg['end']}…"
    ):
        try:
            bt.ensure_all_data(
                start=cfg["start"].strip(),
                end=cfg["end"].strip(),
                cex_kind=cfg["cex_kind"],
                klines_interval=cfg["klines_interval"],
                include_pool_hourly=False,
            )
        except Exception as e:
            st.error(f"Auto-fetch failed: {e}")
            return None, None

    cex_files = _list_files("*aggTrades*.csv") + _list_files("*klines*.csv")
    fund_files = _list_files("*fundingRate*.csv")
    if not cex_files:
        st.error(
            "Auto-fetch produced no CEX files. Check the From/To dates in "
            "the Fetch data expander."
        )
        return None, None
    return cex_files[0], (fund_files[0] if fund_files else None)


def _load_swap_level_inputs():
    if mode.startswith("Demo"):
        return _demo(int(demo_days), float(demo_s0), float(demo_vol), int(demo_seed))

    # Swap parquet: never auto-fetch (slow), give clear guidance instead.
    if not swaps_path or swaps_path == "(none)":
        st.error(
            "No swap file selected. Swap-level data can't be "
            "auto-downloaded — use **Fetch data → Fetch swap events** or "
            "drop a cryo/Dune/Allium dump, or switch to the **hourly** engine."
        )
        return None

    # CEX + funding: fast, safe to auto-fetch on the user's behalf.
    cex_p = cex_path
    fund_p = funding_path
    if not cex_p or cex_p == "(none)":
        cex_p, auto_fund = _auto_fetch_missing_binance()
        if cex_p is None:
            return None
        if (not fund_p or fund_p.startswith("(none")) and auto_fund:
            fund_p = auto_fund

    swaps = _clip_swaps(_load_swaps(str(REPO / swaps_path)), win_start, win_end)
    cex = _clip_series(_load_cex(str(REPO / cex_p)), win_start, win_end)
    funding = (
        _clip_series(_load_funding_series(str(REPO / fund_p)), win_start, win_end)
        if fund_p and not fund_p.startswith("(none")
        else pd.Series(dtype=float)
    )
    if len(swaps) == 0 or len(cex) == 0:
        st.error("Backtest window is empty after clipping — check the dates.")
        return None
    return swaps, cex, funding


def _load_hourly_inputs():
    if mode.startswith("Live"):
        return _fetch_klines(int(live_days), str(interval)), _fetch_funding(int(live_days))

    cex_p = cex_path
    fund_p = funding_path
    if not cex_p or cex_p == "(none)":
        cex_p, auto_fund = _auto_fetch_missing_binance()
        if cex_p is None:
            return None
        if (not fund_p or fund_p == "(none)") and auto_fund:
            fund_p = auto_fund

    cex_series = _clip_series(_load_cex(str(REPO / cex_p)), win_start, win_end)
    klines = (
        cex_series.resample("1h")
        .agg(["first", "max", "min", "last"])
        .rename(columns={"first": "open", "max": "high", "min": "low", "last": "close"})
        .dropna()
    )
    klines["volume"] = 0.0
    klines["quote_volume"] = 0.0
    funding = (
        _clip_series(_load_funding_df(str(REPO / fund_p)), win_start, win_end)
        if fund_p and fund_p != "(none)"
        else pd.DataFrame({"fundingRate": []})
    )
    if klines.empty:
        st.error("No klines in the selected window.")
        return None
    return klines, funding


# --------------------------------------------------------------------------- #
# Analysis helpers
# --------------------------------------------------------------------------- #


def _num(x) -> float:
    """Coerce None / 'n/a' / non-numeric to NaN; leave numbers as floats.

    Used when packing summary-dict values into DataFrames -- the hourly
    engine sometimes returns None (e.g. sharpe when there aren't enough
    daily bars), which mixes with floats to make an object-dtype column
    that pyarrow refuses to serialise for st.dataframe.
    """
    if x is None:
        return float("nan")
    try:
        return float(x)
    except (TypeError, ValueError):
        return float("nan")


def _rolling_apr(df: pd.DataFrame, capital_base: float, window_days: int = 30) -> pd.Series:
    """Rolling annualised return of net PnL across a `window_days` window."""
    if "net_usd" not in df.columns or df.empty:
        return pd.Series(dtype=float)
    daily = df["net_usd"].resample("1D").sum()
    if len(daily) < window_days:
        return pd.Series(dtype=float)
    return (daily.rolling(window_days).sum() / capital_base) * (365 / window_days) * 100


def _push_run(res, label: str, params: dict):
    """Store a run in session_state (cap at 5).

    Only the daily-resampled `cum_net` is kept, not the full timeseries.
    A month of swap-level backtest produces millions of rows; five of
    those in memory (deepcopy'd for isolation) is enough to OOM Railway's
    default 512 MB tier. Daily-resampled is ~30-180 rows and is what the
    comparison chart plots anyway.
    """
    ts = res.timeseries
    daily_cum = (
        ts["cum_net"].resample("1D").last().ffill()
        if not ts.empty else pd.Series(dtype=float)
    )
    entry = {
        "label": label or f"Run {len(st.session_state['runs']) + 1}",
        "engine": res.engine,
        "summary": deepcopy(res.summary),
        "daily_cum_net": daily_cum,  # <200 rows for a year of hourly data
        "params": deepcopy(params),
        "when": pd.Timestamp.now(tz="UTC"),
    }
    st.session_state["runs"].append(entry)
    st.session_state["runs"] = st.session_state["runs"][-5:]


# --------------------------------------------------------------------------- #
# Render helpers
# --------------------------------------------------------------------------- #


SHORT_WINDOW_DAYS = 30  # below this, the verdict is unreliable

# Explicit chart colour palette. Reused across every st.line_chart /
# st.bar_chart / plotly call so the same series always gets the same
# colour and different chart types visually cluster distinct meanings.
# Order matches the "role" of each series so users learn the mapping.
COLOR_NET = "#1f77b4"        # blue — headline PnL
COLOR_FEES = "#2ca02c"       # green — income
COLOR_FUNDING = "#17becf"    # cyan — income (secondary)
COLOR_LVR = "#d62728"        # red — biggest cost
COLOR_HEDGE = "#ff7f0e"      # orange — cost
COLOR_GAS = "#9467bd"        # purple — cost (small)
COLOR_PRICE = "#7f7f7f"      # grey — reference series
COLOR_LP_DELTA = "#8c564b"   # brown
COLOR_HEDGE_DELTA = "#e377c2"  # pink


class ResultRenderer:
    """Renders every panel that depends on a single BacktestResult.

    Bundling `res` in __init__ avoids the "pass res into 6 free functions"
    pattern and centralises the summary-dict key lookups. Adding a new
    panel is one method here rather than one new module-level function.

    Panels that don't take `res` (sweep, by-month, comparison) stay as
    free functions since their inputs differ.
    """

    def __init__(self, res):
        self.res = res
        self.s = res.summary
        self.df = res.timeseries
        # Convenience derivations for the templates below
        self.net_apr = self.s.get("net_apr_pct") or 0.0
        self.days = self.s.get("days") or 0

    def verdict(self, is_demo: bool) -> None:
        if is_demo:
            st.warning(
                "**Synthetic tape** — GBM prices + simulated swaps. Great "
                "for validating the engine and exploring parameter "
                "sensitivity, but numbers do NOT reflect real ETH/USDC PnL."
            )

        # Short-window caveat: a 3-day backtest showing +2% APR is inside
        # noise, not a decision. Downgrade the verdict signal in that case.
        is_short = self.days < SHORT_WINDOW_DAYS
        if is_short:
            color = "orange"
            verdict = f"INSUFFICIENT WINDOW ({self.days:.0f}d < {SHORT_WINDOW_DAYS}d)"
        elif self.net_apr > 0:
            color = "green"
            verdict = "FEES COVER THE COST"
        else:
            color = "red"
            verdict = "FEES DO NOT COVER THE COST"

        st.markdown(
            f"### :{color}[{verdict}] — net **{self.net_apr:.2f}% APR** · "
            f"engine: `{self.res.engine}` · {self.days:.0f} days"
        )

        if is_short:
            st.caption(
                f":orange[Treat this as directional only.] Regime dependency "
                f"and noise dominate over windows shorter than "
                f"{SHORT_WINDOW_DAYS} days — extend the backtest window or "
                f"turn on the rolling-window distribution to see stability."
            )

        cap = self.s.get("capital_base_usd", 0)
        lp = self.s.get("capital_lp_usd", self.s.get("capital_usd", cap))
        margin = cap - lp if cap and lp else 0
        st.caption(
            f"Capital base = LP ${lp:,.0f} + hedge margin ${margin:,.0f} = "
            f"**${cap:,.0f}** (denominator for all APRs below)"
        )

    def metrics(self) -> None:
        s = self.s
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Net PnL (USD)", f"${s['net_usd']:,}")
        c2.metric(
            "Fees APR", f"{s['fee_apr_pct']:.2f}%",
            help="Fees earned by the LP, annualised over the backtest window.",
        )
        c3.metric(
            "LVR APR",
            f"{s['lvr_apr_pct']:.2f}%" if s.get("lvr_apr_pct") is not None else "n/a",
            help="Loss-versus-rebalancing. Negative = LP loses value to "
            "informed flow vs a continuously rebalancing portfolio.",
        )

        # Funding sign convention: positive APR = collected, negative = paid.
        # Show a hint under the metric so users don't have to remember it.
        funding_apr = s.get("funding_apr_pct", 0.0) or 0.0
        funding_hint = (
            "collected (perp paid you)" if funding_apr > 0.001
            else "paid (you paid the perp)" if funding_apr < -0.001
            else "flat"
        )
        c4.metric(
            "Funding APR", f"{funding_apr:.2f}%",
            delta=funding_hint, delta_color="normal",
            help="Perp funding, annualised. **Positive = you collected** "
            "(short perp received funding), **negative = you paid**. "
            "Convention: hedging by shorting ETH-perp; funding flows to the "
            "short side when funding rate is positive.",
        )
        c5.metric(
            "Hedge cost APR", f"{s['hedge_cost_apr_pct']:.2f}%",
            help="Taker fees + spread + market impact on rebalance trades. "
            "Always negative (execution costs money).",
        )

        c6, c7, c8, c9 = st.columns(4)
        c6.metric(
            "Fees / LVR", f"{s.get('fee_over_lvr', float('nan'))}",
            help="Ratio of fee income to LVR loss. Above 1 = fees cover "
                 "LVR (necessary but not sufficient for profitability — "
                 "funding + hedge costs still eat in).",
        )
        c7.metric(
            "% time in range", f"{s.get('pct_time_in_range', 0)}%",
            help="Fraction of the backtest window during which spot was "
                 "inside the LP band. Fees only accrue in-range; below "
                 "~80% usually means the range is too tight for the vol.",
        )
        c8.metric(
            "Sharpe (daily)", f"{s.get('sharpe', 'n/a')}",
            help="Daily net-PnL Sharpe ratio, annualised by √365. "
                 "Above 1 is decent; negative means the mean daily PnL "
                 "is worse than the day-to-day noise.",
        )
        c9.metric(
            "Max drawdown", f"${s.get('max_drawdown_usd', 0):,}",
            help="Worst peak-to-trough drop in cumulative net PnL over "
                 "the backtest window, in USD.",
        )

    def export_buttons(self) -> None:
        """JSON summary + CSV timeseries download buttons, side by side."""
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                "Download summary (JSON)",
                data=json.dumps(self.s, indent=2, default=str),
                file_name=f"backtest_summary_{self.res.engine}.json",
                mime="application/json",
                width="stretch",
            )
        with col2:
            # Streamlit streams the download so this doesn't OOM the browser
            # even on multi-million-row timeseries.
            st.download_button(
                "Download timeseries (CSV)",
                data=self.df.to_csv().encode("utf-8"),
                file_name=f"backtest_timeseries_{self.res.engine}.csv",
                mime="text/csv",
                width="stretch",
            )

    def charts(self) -> None:
        df = self.df

        st.subheader("Cumulative PnL")
        st.line_chart(
            df[["cum_net"]].rename(columns={"cum_net": "Cumulative net PnL (USD)"}),
            color=[COLOR_NET],
        )

        st.subheader("PnL attribution")
        tab_cum, tab_daily = st.tabs(["Cumulative", "Daily bars"])
        # Colour order MUST match column order in the DataFrame:
        #   Fees, -LVR, Funding, -Hedge cost, -Gas
        attr_colors = [COLOR_FEES, COLOR_LVR, COLOR_FUNDING, COLOR_HEDGE, COLOR_GAS]
        with tab_cum:
            attr = pd.DataFrame({
                "Fees": df["fee_usd"].cumsum(),
                "-LVR": -df["lvr_usd"].cumsum(),
                "Funding": df["funding_usd"].cumsum(),
                "-Hedge cost": -df["hedge_cost_usd"].cumsum(),
                "-Gas": -df["gas_usd"].cumsum(),
            })
            st.line_chart(attr, color=attr_colors)
        with tab_daily:
            daily = (
                df[["fee_usd", "lvr_usd", "funding_usd", "hedge_cost_usd", "gas_usd"]]
                .resample("1D").sum()
            )
            daily.columns = ["Fees", "LVR", "Funding", "Hedge cost", "Gas"]
            for col in ["LVR", "Hedge cost", "Gas"]:
                daily[col] = -daily[col]
            st.bar_chart(daily, color=attr_colors)
            st.caption(
                "Positive bars = income (fees, funding-received), negative = "
                "costs. Net = sum of the stack per day."
            )

        st.subheader("ETH price and LP delta")
        st.line_chart(df[["price"]], color=[COLOR_PRICE])
        st.line_chart(
            df[["lp_delta", "hedge_delta"]],
            color=[COLOR_LP_DELTA, COLOR_HEDGE_DELTA],
        )

    def rolling(self) -> None:
        cap = self.s.get("capital_base_usd", 1)
        rolling = _rolling_apr(self.df, cap, window_days=30)
        if rolling.empty:
            st.info("Not enough data for 30-day rolling windows.")
            return
        st.subheader("30-day rolling APR")
        col_a, col_b = st.columns([2, 1])
        with col_a:
            st.line_chart(
                rolling.rename("Rolling 30d APR (%)"), color=[COLOR_NET]
            )
        with col_b:
            st.caption("Distribution")
            # round(x, n) rather than x.round(n): pandas reductions can
            # return plain Python floats in some paths, which have no
            # .round() method.
            st.write(
                pd.DataFrame({
                    "stat": ["min", "p25", "median", "p75", "max", "std"],
                    "APR (%)": [
                        round(float(rolling.min()), 2),
                        round(float(rolling.quantile(0.25)), 2),
                        round(float(rolling.median()), 2),
                        round(float(rolling.quantile(0.75)), 2),
                        round(float(rolling.max()), 2),
                        round(float(rolling.std()), 2),
                    ],
                }).set_index("stat")
            )

    def raw_expanders(self) -> None:
        """Full summary JSON + first-500-row timeseries preview."""
        with st.expander("Full summary"):
            st.json(self.s)
        with st.expander("Timeseries (first 500 rows)"):
            st.dataframe(self.df.head(500))


def _sweep_axis_picker(
    key_prefix: str, default_x: str, default_y: str
) -> tuple[str, list, str, list]:
    """Render the X/Y field pickers and per-axis value editors.
    Returns (x_field, x_values, y_field, y_values)."""
    fields = list(bt.SWEEPABLE_FIELDS.keys())

    col_x, col_y = st.columns(2)
    with col_x:
        x_field = st.selectbox(
            "X axis (columns)", fields,
            index=fields.index(default_x),
            format_func=lambda f: bt.SWEEPABLE_FIELDS[f]["label"],
            key=f"{key_prefix}_x_field",
            help="Assumption field varied along the heatmap's X axis "
                 "(columns). Each unique X value becomes one column.",
        )
        x_default = bt.SWEEPABLE_FIELDS[x_field]["default_values"]
        x_str = st.text_input(
            "X values (comma-separated)",
            value=", ".join(str(v) for v in x_default),
            key=f"{key_prefix}_x_values",
            help="Comma-separated list of values to try along the X "
                 "axis. Types are inferred from the field's defaults "
                 "(int for grid_seconds, float for hedge_band, etc).",
        )
    with col_y:
        # Guard against picking the same field for both axes
        y_options = [f for f in fields if f != x_field]
        y_default = default_y if default_y != x_field else y_options[0]
        y_field = st.selectbox(
            "Y axis (rows)", y_options,
            index=y_options.index(y_default),
            format_func=lambda f: bt.SWEEPABLE_FIELDS[f]["label"],
            key=f"{key_prefix}_y_field",
            help="Assumption field varied along the Y axis (rows). Must "
                 "differ from the X axis — same field on both axes is "
                 "meaningless.",
        )
        y_default_vals = bt.SWEEPABLE_FIELDS[y_field]["default_values"]
        y_str = st.text_input(
            "Y values (comma-separated)",
            value=", ".join(str(v) for v in y_default_vals),
            key=f"{key_prefix}_y_values",
            help="Comma-separated list of Y-axis values. Total cell count "
                 "= |X values| × |Y values|; each cell is a full backtest.",
        )

    def _parse(s: str, field: str) -> list:
        raw = [x.strip() for x in s.split(",") if x.strip()]
        # Preserve int-vs-float using the default's type
        sample = bt.SWEEPABLE_FIELDS[field]["default_values"][0]
        caster = int if isinstance(sample, int) else float
        return [caster(x) for x in raw]

    return x_field, _parse(x_str, x_field), y_field, _parse(y_str, y_field)


def _render_sweep(a: bt.Assumptions, swaps, cex, funding):
    st.subheader("Parameter sweep")
    st.caption(
        "Pick any two Assumptions fields as the sweep axes. Each cell "
        "runs a full backtest — a 5×5 grid on real data can take many "
        "minutes; cancel any time to see partial results."
    )

    x_field, x_values, y_field, y_values = _sweep_axis_picker(
        key_prefix="sweep",
        default_x="hedge_band",
        default_y="range_width",
    )

    if not x_values or not y_values:
        st.warning("Provide at least one value per axis.")
        return
    if x_field == y_field:
        st.error("X and Y axes must differ.")
        return

    st.session_state.setdefault("cancel_sweep", False)
    st.session_state["cancel_sweep"] = False

    prog = st.progress(0.0, text="Sweep starting…")
    status = st.empty()
    cancel_col = st.empty()
    with cancel_col.container():
        if st.button("Cancel sweep", key="cancel_sweep_btn"):
            st.session_state["cancel_sweep"] = True

    x_fmt = bt.SWEEPABLE_FIELDS[x_field]["fmt"]
    y_fmt = bt.SWEEPABLE_FIELDS[y_field]["fmt"]
    t0 = time.time()

    def _cb(done, total, params):
        xv, yv = params
        frac = done / total
        elapsed = time.time() - t0
        eta = (elapsed / frac - elapsed) if frac > 0.05 else float("inf")
        eta_str = (
            f"{int(eta)}s remaining" if eta < 3600
            else f"~{eta/60:.0f} min remaining"
        )
        prog.progress(
            frac,
            text=(
                f"Sweep cell {done}/{total} · "
                f"{bt.SWEEPABLE_FIELDS[x_field]['label']} {x_fmt.format(xv)} × "
                f"{bt.SWEEPABLE_FIELDS[y_field]['label']} {y_fmt.format(yv)}"
            ),
        )
        status.caption(f"Elapsed {elapsed:.0f}s · {eta_str}")

    with _log_stage(
        "sweep",
        x_field=x_field, x_n=len(x_values),
        y_field=y_field, y_n=len(y_values),
        total_cells=len(x_values) * len(y_values),
    ):
        tbl = bt.sweep_swap_level_axes(
            a, swaps, cex, funding,
            x_field=x_field, x_values=x_values,
            y_field=y_field, y_values=y_values,
            progress_cb=_cb,
            cancel_cb=lambda: st.session_state.get("cancel_sweep", False),
        )

    prog.empty()
    status.empty()
    cancel_col.empty()

    log.info(
        f"sweep_result cells_completed={len(tbl)} "
        f"cancelled={st.session_state.get('cancel_sweep', False)}"
    )

    if st.session_state["cancel_sweep"]:
        st.warning(f"Sweep cancelled with {len(tbl)} cells completed.")
    if tbl.empty:
        st.info("No sweep results to display.")
        return

    net_pivot = tbl.pivot(index=y_field, columns=x_field, values="net_apr")
    sharpe_pivot = (
        tbl.pivot(index=y_field, columns=x_field, values="sharpe")
        if "sharpe" in tbl.columns else None
    )
    fee_over_lvr_pivot = (
        tbl.pivot(index=y_field, columns=x_field, values="fee_over_lvr")
        if "fee_over_lvr" in tbl.columns else None
    )

    hover = np.empty(net_pivot.shape, dtype=object)
    for i, ry in enumerate(net_pivot.index):
        for j, cx in enumerate(net_pivot.columns):
            parts = [
                f"{bt.SWEEPABLE_FIELDS[y_field]['label']}: {y_fmt.format(ry)}",
                f"{bt.SWEEPABLE_FIELDS[x_field]['label']}: {x_fmt.format(cx)}",
                f"Net APR: {net_pivot.iloc[i, j]:.2f}%",
            ]
            if sharpe_pivot is not None:
                parts.append(f"Sharpe: {sharpe_pivot.iloc[i, j]:.2f}")
            if fee_over_lvr_pivot is not None:
                parts.append(f"Fees/LVR: {fee_over_lvr_pivot.iloc[i, j]:.2f}")
            hover[i, j] = "<br>".join(parts)

    fig = go.Figure(data=go.Heatmap(
        z=net_pivot.values,
        x=[x_fmt.format(c) for c in net_pivot.columns],
        y=[y_fmt.format(r) for r in net_pivot.index],
        colorscale="RdYlGn",
        zmid=0,
        hovertext=hover,
        hoverinfo="text",
        colorbar=dict(title="Net APR (%)"),
    ))
    fig.update_layout(
        xaxis_title=bt.SWEEPABLE_FIELDS[x_field]["label"],
        yaxis_title=bt.SWEEPABLE_FIELDS[y_field]["label"],
        height=400,
        margin=dict(l=60, r=20, t=20, b=40),
    )
    st.plotly_chart(fig, width="stretch")

    # Best cell callout
    best_idx = tbl["net_apr"].idxmax()
    best = tbl.loc[best_idx]
    st.info(
        f"**Best cell:** {bt.SWEEPABLE_FIELDS[y_field]['label']} "
        f"{y_fmt.format(best[y_field])} × "
        f"{bt.SWEEPABLE_FIELDS[x_field]['label']} "
        f"{x_fmt.format(best[x_field])} → net APR {best['net_apr']:.2f}%"
        + (f", Sharpe {best['sharpe']:.2f}" if "sharpe" in tbl.columns else "")
    )
    with st.expander("Full sweep table"):
        st.dataframe(tbl, width="stretch")


def _render_by_month(
    a: bt.Assumptions,
    engine_key: str,
    swap_inputs: tuple | None,
    hourly_inputs: tuple | None,
):
    """Run one backtest per calendar month contained in the input data,
    then show monthly net-APR bars. Skips months with < 5 days of data
    (start/end fragments) since those are noisy."""
    st.subheader("Backtest by month")
    st.caption(
        "Splits the loaded data into calendar months and runs one "
        "backtest per month. Reveals regime dependency (e.g. does the "
        "strategy work in low-vol months?)."
    )

    # Figure out the union date range from whatever we've got
    if engine_key == "swap_level" and swap_inputs is not None:
        swaps, cex, funding = swap_inputs
        idx = swaps["block_time"]
    elif engine_key == "hourly" and hourly_inputs is not None:
        klines, funding = hourly_inputs
        idx = pd.Series(klines.index)
    else:
        st.info("No data loaded for the by-month analysis.")
        return

    if len(idx) == 0:
        st.info("No data in the selected window.")
        return

    # Month buckets
    start = idx.min().tz_convert("UTC") if hasattr(idx.min(), "tz_convert") else idx.min()
    end = idx.max().tz_convert("UTC") if hasattr(idx.max(), "tz_convert") else idx.max()
    months = pd.period_range(start=start, end=end, freq="M")

    if len(months) < 2:
        st.info(
            f"Loaded data spans only {len(months)} month — need at least "
            "2 for a monthly breakdown. Widen the backtest window."
        )
        return

    prog = st.progress(0.0, text="Starting monthly runs…")
    status = st.empty()
    t0 = time.time()

    log.info(f"by_month_start engine={engine_key} months={len(months)}")
    rows = []
    skipped = 0
    for i, m in enumerate(months):
        m_start = m.to_timestamp(how="start").tz_localize("UTC")
        m_end = m.to_timestamp(how="end").tz_localize("UTC")

        if engine_key == "swap_level":
            m_swaps = swaps[(swaps["block_time"] >= m_start) & (swaps["block_time"] <= m_end)]
            m_cex = cex.loc[m_start:m_end]
            m_fund = funding.loc[m_start:m_end] if len(funding) else funding
            days = (m_end - m_start).days
            if len(m_swaps) < 100 or len(m_cex) < 100 or days < 5:
                skipped += 1
                continue
            try:
                res = bt.run_swap_level(a, m_swaps, m_cex, m_fund)
            except Exception as e:
                log.warning(f"by_month cell failed month={m} error={e}")
                continue
        else:
            m_kl = klines.loc[m_start:m_end]
            m_fund = funding.loc[m_start:m_end] if len(funding) else funding
            if len(m_kl) < 24 * 5:  # need ~5 days of hourly bars
                skipped += 1
                continue
            try:
                res = bt.run_hourly(a, m_kl, m_fund)
            except Exception as e:
                log.warning(f"by_month cell failed month={m} error={e}")
                continue

        s = res.summary
        # Coerce every numeric field via _num so cross-engine None values
        # don't produce object-dtype columns that fail Arrow serialisation.
        rows.append({
            "month": str(m),
            "net_apr_pct": _num(s.get("net_apr_pct")),
            "fee_apr_pct": _num(s.get("fee_apr_pct")),
            "lvr_apr_pct": _num(s.get("lvr_apr_pct")),
            "funding_apr_pct": _num(s.get("funding_apr_pct")),
            "hedge_cost_apr_pct": _num(s.get("hedge_cost_apr_pct")),
            "sharpe": _num(s.get("sharpe")),
            "pct_time_in_range": _num(s.get("pct_time_in_range")),
        })

        elapsed = time.time() - t0
        prog.progress(
            (i + 1) / len(months),
            text=f"Month {i+1}/{len(months)} · elapsed {elapsed:.0f}s",
        )

    prog.empty()
    status.empty()

    total_dt = time.time() - t0
    log.info(
        f"by_month_done in {total_dt:.2f}s completed={len(rows)} "
        f"skipped={skipped}"
    )

    if not rows:
        st.warning("No months had enough data for a meaningful backtest.")
        return

    df = pd.DataFrame(rows).set_index("month")

    # Colour bars by sign so wins vs losses read at a glance
    fig = go.Figure(data=go.Bar(
        x=df.index,
        y=df["net_apr_pct"],
        marker_color=[COLOR_FEES if v > 0 else COLOR_LVR for v in df["net_apr_pct"]],
        hovertext=[
            f"Month: {m}<br>Net APR: {v:.2f}%<br>"
            f"Fees: {f:.2f}%<br>LVR: {lv:.2f}%<br>"
            f"Sharpe: {sh:.2f}<br>% in range: {ir:.1f}%"
            for m, v, f, lv, sh, ir in zip(
                df.index, df["net_apr_pct"], df["fee_apr_pct"],
                df["lvr_apr_pct"], df["sharpe"], df["pct_time_in_range"]
            )
        ],
        hoverinfo="text",
    ))
    fig.update_layout(
        title="Net APR by month",
        xaxis_title="Month",
        yaxis_title="Net APR (%)",
        height=350,
        margin=dict(l=60, r=20, t=40, b=40),
    )
    st.plotly_chart(fig, width="stretch")

    # Summary
    positive = int((df["net_apr_pct"] > 0).sum())
    median_apr = df["net_apr_pct"].median()
    st.info(
        f"**{positive}/{len(df)} months positive**, median net APR "
        f"{median_apr:.2f}%. Full breakdown below."
    )
    with st.expander("Full monthly table"):
        st.dataframe(df, width="stretch")


def _render_comparison():
    runs = st.session_state["runs"]
    if len(runs) < 2:
        return
    st.subheader("Run comparison")

    # "Current" = the most recently pushed run. Prefix with ★ so it
    # stands out in both the multiselect and the KPI table.
    current_idx = len(runs) - 1
    labels = [
        f"{'★ ' if i == current_idx else ''}{i+1}. {r['label']}"
        for i, r in enumerate(runs)
    ]
    st.caption("★ marks the current run (most recent).")
    picked = st.multiselect(
        "Overlay runs (up to 5)", labels, default=labels[-2:],
    )
    if not picked:
        return
    picked_idx = [labels.index(p) for p in picked]

    # Overlay cumulative net (daily-resampled to keep session_state small)
    overlay = pd.DataFrame()
    for i in picked_idx:
        r = runs[i]
        overlay[labels[i]] = r["daily_cum_net"]
    st.line_chart(overlay)

    # KPI table -- rows are runs, columns are KPIs.
    # Transposed vs the old layout (which had runs as columns) because:
    #   Runs come from different engines. The hourly engine returns
    #   `sharpe = None`; swap_level returns a float. Mixing those in a
    #   single pandas column produces an object dtype, which pyarrow
    #   can't serialise for st.dataframe. With runs as ROWS, each
    #   column is a single KPI with uniform type, and Nones become
    #   proper NaN floats via _num().
    kpi = pd.DataFrame([
        {
            "run": labels[i],
            "engine": r["engine"],
            "net APR (%)": _num(r["summary"].get("net_apr_pct")),
            "fees APR (%)": _num(r["summary"].get("fee_apr_pct")),
            "LVR APR (%)": _num(r["summary"].get("lvr_apr_pct")),
            "funding APR (%)": _num(r["summary"].get("funding_apr_pct")),
            "hedge cost APR (%)": _num(r["summary"].get("hedge_cost_apr_pct")),
            "Sharpe": _num(r["summary"].get("sharpe")),
            "% in range": _num(r["summary"].get("pct_time_in_range")),
        }
        for i, r in [(i, runs[i]) for i in picked_idx]
    ]).set_index("run")
    st.dataframe(kpi, width="stretch")


# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #

tab_run, tab_docs = st.tabs(["Backtest", "Documentation"])

# --------------------------------------------------------------------------- #
# Run tab
# --------------------------------------------------------------------------- #

with tab_run:
    run = st.button("Run backtest", type="primary", width="stretch")

if not run:
    with tab_run:
        st.info("Set your assumptions in the sidebar, then click **Run backtest**.")
        _render_comparison()  # still show history if it exists

def _apply_stress_shock(klines: pd.DataFrame, stress: dict) -> pd.DataFrame:
    """Apply a synthetic price shock to the last N days of klines.

    `stress` = {'days': N, 'pct': p, 'shape': 'linear'|'step'}
    - linear: multiply price by 1 + p*fraction, fraction ramping 0→1 over N days
    - step:   multiply price by (1 + p) starting at the first bar in the window

    Volumes are left untouched -- we're stressing price, not activity.
    OHLC columns are scaled uniformly so bar shape is preserved.
    """
    if klines.empty:
        return klines
    kl = klines.copy()
    end = kl.index[-1]
    window_start = end - pd.Timedelta(days=int(stress["days"]))
    mask = kl.index >= window_start
    if not mask.any():
        return kl

    n = int(mask.sum())
    if stress["shape"] == "step":
        mult = np.full(n, 1.0 + float(stress["pct"]))
    else:  # linear ramp
        frac = np.linspace(0.0, 1.0, n)
        mult = 1.0 + float(stress["pct"]) * frac

    for col in ("open", "high", "low", "close"):
        if col in kl.columns:
            kl.loc[mask, col] = kl.loc[mask, col].to_numpy() * mult
    return kl


def _execute_run():
    """Run + render one backtest.

    Uses `return` for early exits (missing data, load errors, etc) so
    they DON'T short-circuit the whole script -- otherwise the
    Documentation tab below wouldn't render. Any surviving exceptions
    still show up via st.exception; the docs tab is unaffected.
    """
    swap_inputs = None
    hourly_inputs = None
    try:
        a = bt.Assumptions.from_dict(st.session_state["assumptions"])
        is_demo = engine_key == "swap_level" and mode.startswith("Demo")

        if engine_key == "swap_level":
            loaded = _load_swap_level_inputs()
            if loaded is None:
                return
            swaps, cex, funding = loaded
            swap_inputs = (swaps, cex, funding)
            st.caption(
                f"Loaded {len(swaps):,} swaps · CEX {cex.index[0]} → {cex.index[-1]}"
            )
            with st.spinner("Running swap-level backtest…"), \
                    _log_stage(
                        "swap_level_backtest",
                        swaps=len(swaps), cex_len=len(cex),
                        capital=int(a.capital_usd),
                        range_w=a.range_width, hedge_b=a.hedge_band,
                    ):
                res = bt.run_swap_level(a, swaps, cex, funding)
        else:
            loaded = _load_hourly_inputs()
            if loaded is None:
                return
            klines, funding = loaded
            if stress_cfg is not None:
                klines = _apply_stress_shock(klines, stress_cfg)
                log.info(
                    f"stress_test_applied pct={stress_cfg['pct']} "
                    f"shape={stress_cfg['shape']} days={stress_cfg['days']}"
                )
                st.warning(
                    f"**Stress test active:** {stress_cfg['pct']:+.0%} "
                    f"{stress_cfg['shape']} price move over last "
                    f"{stress_cfg['days']} days. Results are synthetic."
                )
            hourly_inputs = (klines, funding)
            st.caption(
                f"Loaded {len(klines):,} bars · "
                f"{klines.index[0]} → {klines.index[-1]}"
            )
            with st.spinner("Running hourly backtest…"), \
                    _log_stage(
                        "hourly_backtest",
                        bars=len(klines), capital=int(a.capital_usd),
                        range_w=a.range_width,
                        rebalance=a.rebalance_mode,
                    ):
                res = bt.run_hourly(a, klines, funding)

        _push_run(res, run_label, st.session_state["assumptions"])
        log.info(
            f"backtest_complete engine={res.engine} "
            f"net_apr={res.summary.get('net_apr_pct'):.2f} "
            f"days={res.summary.get('days')}"
        )
    except Exception as e:
        # Log with full traceback -- streamlit only shows it in the UI,
        # so without this Railway's log viewer wouldn't have context.
        log.error(f"backtest_execute failed: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        st.exception(e)
        return

    r = ResultRenderer(res)
    r.verdict(is_demo)
    r.metrics()
    r.export_buttons()
    r.charts()
    if do_rolling:
        r.rolling()
    r.raw_expanders()

    if do_by_month:
        _render_by_month(a, engine_key, swap_inputs, hourly_inputs)

    if do_sweep and engine_key == "swap_level":
        _render_sweep(a, swaps, cex, funding)

    _render_comparison()


if run:
    with tab_run:
        _execute_run()


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
