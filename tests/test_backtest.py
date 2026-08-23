"""Regression + smoke tests for the backtest engines and loaders.

These are guardrails for the numerical core: any edit that changes hedge
math, LVR accounting, or the loader schemas should either preserve these
numbers or bump the golden values with an explanation.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import pytest

import backtest as bt


# --------------------------------------------------------------------------- #
# Golden test: swap_level engine on the synthetic demo tape
# --------------------------------------------------------------------------- #

# Locks the current numeric behaviour of run_claude on a deterministic tape.
# If you change hedge/LVR logic and this shifts, update the numbers and note
# WHY in the commit.
GOLDEN_DEMO = {
    "n_swaps": 101433,
    "cex_len": 1_296_000,
    "net_apr_pct": 26.78,
    "fee_apr_pct": 75.03,
    "lvr_apr_pct": -46.07,
    "funding_apr_pct": 5.03,
    "hedge_cost_apr_pct": -5.30,
    "pct_time_in_range": 100.0,
    "n_hedge_trades": 166,
}


def test_swap_level_demo_matches_golden():
    a = bt.Assumptions()
    swaps, cex, funding = bt.make_demo_tape(
        days=15, s0=3000.0, vol_annual=0.60, seed=42
    )
    assert len(swaps) == GOLDEN_DEMO["n_swaps"]
    assert len(cex) == GOLDEN_DEMO["cex_len"]

    res = bt.run_swap_level(a, swaps, cex, funding)
    s = res.summary

    # Tight tolerance: same seed, same code, same numbers to two decimals.
    assert round(s["net_apr_pct"], 2) == pytest.approx(GOLDEN_DEMO["net_apr_pct"], abs=0.01)
    assert round(s["fee_apr_pct"], 2) == pytest.approx(GOLDEN_DEMO["fee_apr_pct"], abs=0.01)
    assert round(s["lvr_apr_pct"], 2) == pytest.approx(GOLDEN_DEMO["lvr_apr_pct"], abs=0.01)
    assert round(s["funding_apr_pct"], 2) == pytest.approx(
        GOLDEN_DEMO["funding_apr_pct"], abs=0.01
    )
    assert round(s["hedge_cost_apr_pct"], 2) == pytest.approx(
        GOLDEN_DEMO["hedge_cost_apr_pct"], abs=0.01
    )
    assert round(s["pct_time_in_range"], 1) == pytest.approx(
        GOLDEN_DEMO["pct_time_in_range"], abs=0.1
    )
    assert s["n_hedge_trades"] == GOLDEN_DEMO["n_hedge_trades"]


# --------------------------------------------------------------------------- #
# Assumptions round-trip
# --------------------------------------------------------------------------- #


def test_assumptions_round_trip():
    a = bt.Assumptions(capital_usd=250_000, range_width=0.20, hedge_band=0.02)
    d = a.to_dict()
    a2 = bt.Assumptions.from_dict(d)
    assert a == a2


def test_assumptions_from_dict_ignores_unknown_keys():
    d = bt.Assumptions().to_dict()
    d["not_a_real_field"] = 999
    a = bt.Assumptions.from_dict(d)
    assert a == bt.Assumptions()  # unknown key silently dropped


# --------------------------------------------------------------------------- #
# Loader schemas
# --------------------------------------------------------------------------- #


def test_load_cex_prices_from_klines(tmp_path):
    # Fake a 3-row headerless Binance klines CSV: open_time, open, high, low,
    # close, volume, close_time, ...
    ts0 = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000)
    rows = []
    for i in range(3):
        rows.append(
            f"{ts0 + i * 60_000},2300.0,2305.0,2295.0,{2300 + i},10.0,"
            f"{ts0 + (i+1) * 60_000 - 1},0,0,0,0,0"
        )
    csv = tmp_path / "klines.csv"
    csv.write_text("\n".join(rows) + "\n")

    s = bt.load_cex_prices(str(csv))
    assert isinstance(s, pd.Series)
    assert s.index.tz is not None  # tz-aware
    assert s.index.freqstr == "s"  # resampled to 1s
    # Values should be forward-filled prices, all >= 2300
    assert (s >= 2300).all()


def test_ensure_binance_csvs_idempotent(tmp_path, monkeypatch):
    # Route real HTTP calls to a fake that returns nothing so we prove the
    # skip path never invokes them
    called = {"count": 0}

    def fake_download(*args, **kwargs):
        called["count"] += 1
        return []

    monkeypatch.setattr(bt.run_claude, "download_binance", fake_download)

    # Pre-create the "cached" files
    for kind, interval in [("klines", "1m"), ("fundingRate", None)]:
        p = bt._binance_csv_path(kind, "2024-01", "ETHUSDT", tmp_path, interval)
        p.write_text("stub")

    result = bt.ensure_binance_csvs(
        ["2024-01"],
        kinds=("klines", "fundingRate"),
        out_dir=tmp_path,
        klines_interval="1m",
    )

    assert called["count"] == 0, "download_binance was called for cached files!"
    assert all(len(v["skipped"]) == 1 for v in result.values())
    assert all(len(v["downloaded"]) == 0 for v in result.values())


def test_klines_filename_includes_interval(tmp_path):
    p1m = bt._binance_csv_path("klines", "2024-01", "ETHUSDT", tmp_path, "1m")
    p1h = bt._binance_csv_path("klines", "2024-01", "ETHUSDT", tmp_path, "1h")
    assert p1m != p1h, "1m and 1h klines would collide on disk!"
    assert "1m" in p1m.name
    assert "1h" in p1h.name


# --------------------------------------------------------------------------- #
# Demo tape properties
# --------------------------------------------------------------------------- #


def test_make_demo_tape_deterministic():
    a = bt.make_demo_tape(days=5, s0=3000.0, vol_annual=0.5, seed=1)
    b = bt.make_demo_tape(days=5, s0=3000.0, vol_annual=0.5, seed=1)
    for x, y in zip(a, b):
        if isinstance(x, pd.DataFrame):
            pd.testing.assert_frame_equal(x, y)
        else:
            pd.testing.assert_series_equal(x, y)


def test_make_demo_tape_seeds_differ():
    _, cex1, _ = bt.make_demo_tape(days=5, seed=1)
    _, cex2, _ = bt.make_demo_tape(days=5, seed=2)
    assert not cex1.equals(cex2)
