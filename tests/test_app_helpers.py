"""Tests for pure helper functions in app.py.

These functions don't depend on streamlit state and can be tested
directly. UI-integrated helpers (anything that touches st.session_state
or calls st.* widgets) belong in a separate integration test if you
add them later.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

import app


# --------------------------------------------------------------------------- #
# _apply_stress_shock
# --------------------------------------------------------------------------- #


def _fake_klines(days: int = 10, start_price: float = 3000.0) -> pd.DataFrame:
    """Hourly klines fixture — 24*days rows, all OHLC = start_price."""
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="1h", tz="UTC")
    return pd.DataFrame({
        "open": start_price,
        "high": start_price,
        "low": start_price,
        "close": start_price,
        "volume": 100.0,
        "quote_volume": 100.0 * start_price,
    }, index=idx)


def test_stress_shock_step_moves_only_last_n_days():
    kl = _fake_klines(days=10)
    stressed = app._apply_stress_shock(
        kl, {"days": 3, "pct": -0.20, "shape": "step"}
    )
    # First 7 days unchanged (10 - 3 = 7)
    cutoff = kl.index[-1] - pd.Timedelta(days=3)
    unchanged = stressed[stressed.index < cutoff]
    changed = stressed[stressed.index >= cutoff]
    assert (unchanged["close"] == 3000.0).all()
    assert changed["close"].iloc[0] == pytest.approx(2400.0)  # -20%
    assert changed["close"].iloc[-1] == pytest.approx(2400.0)


def test_stress_shock_linear_ramps():
    kl = _fake_klines(days=10)
    stressed = app._apply_stress_shock(
        kl, {"days": 3, "pct": -0.20, "shape": "linear"}
    )
    cutoff = kl.index[-1] - pd.Timedelta(days=3)
    changed = stressed[stressed.index >= cutoff]["close"]
    # Ramp: first bar in window = 3000 (fraction 0), last = 2400 (fraction 1)
    assert changed.iloc[0] == pytest.approx(3000.0)
    assert changed.iloc[-1] == pytest.approx(2400.0)
    # Monotonically decreasing since pct < 0
    assert (changed.diff().dropna() <= 0).all()


def test_stress_shock_preserves_ohlc_ratios():
    """If we start with high != low, the shock should scale all four
    proportionally so the bar shape is unchanged."""
    kl = _fake_klines(days=5)
    kl["high"] = 3050.0
    kl["low"] = 2950.0
    kl["open"] = 3010.0
    kl["close"] = 3020.0

    stressed = app._apply_stress_shock(
        kl, {"days": 2, "pct": 0.10, "shape": "step"}
    )
    last_bar = stressed.iloc[-1]
    assert last_bar["high"] == pytest.approx(3050.0 * 1.10)
    assert last_bar["low"] == pytest.approx(2950.0 * 1.10)
    assert last_bar["open"] == pytest.approx(3010.0 * 1.10)
    assert last_bar["close"] == pytest.approx(3020.0 * 1.10)


def test_stress_shock_empty_klines_no_crash():
    empty = pd.DataFrame(columns=["open", "high", "low", "close"])
    assert app._apply_stress_shock(
        empty, {"days": 1, "pct": 0.5, "shape": "step"}
    ).empty


def test_stress_shock_leaves_volume_alone():
    kl = _fake_klines(days=5)
    stressed = app._apply_stress_shock(
        kl, {"days": 2, "pct": -0.3, "shape": "step"}
    )
    assert (stressed["volume"] == kl["volume"]).all()


# --------------------------------------------------------------------------- #
# _clip_series / _clip_swaps
# --------------------------------------------------------------------------- #


def test_clip_series_both_blank_is_noop():
    s = pd.Series(
        [1, 2, 3],
        index=pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC"),
    )
    out = app._clip_series(s, "", "")
    pd.testing.assert_series_equal(out, s)


def test_clip_series_start_only():
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    s = pd.Series(range(10), index=idx)
    out = app._clip_series(s, "2024-01-05", "")
    assert out.index.min() >= pd.Timestamp("2024-01-05", tz="UTC")
    assert len(out) == 6  # 5, 6, 7, 8, 9, 10


def test_clip_series_end_only():
    idx = pd.date_range("2024-01-01", periods=10, freq="D", tz="UTC")
    s = pd.Series(range(10), index=idx)
    out = app._clip_series(s, "", "2024-01-05")
    assert out.index.max() <= pd.Timestamp("2024-01-05", tz="UTC")
    assert len(out) == 5  # 1, 2, 3, 4, 5


def test_clip_series_start_and_end():
    idx = pd.date_range("2024-01-01", periods=30, freq="D", tz="UTC")
    s = pd.Series(range(30), index=idx)
    out = app._clip_series(s, "2024-01-05", "2024-01-10")
    assert out.index.min() >= pd.Timestamp("2024-01-05", tz="UTC")
    assert out.index.max() <= pd.Timestamp("2024-01-10", tz="UTC")
    assert len(out) == 6  # 5..10 inclusive


def test_clip_swaps_by_block_time():
    df = pd.DataFrame({
        "block_time": pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC"),
        "amount0": range(100),
    })
    out = app._clip_swaps(df, "2024-01-01T12:00:00", "2024-01-02T12:00:00")
    assert len(out) == 25  # inclusive both ends, 12 → 12 next day = 25 hours
    assert out["block_time"].min() >= pd.Timestamp("2024-01-01T12:00:00", tz="UTC")
    assert out["block_time"].max() <= pd.Timestamp("2024-01-02T12:00:00", tz="UTC")


def test_clip_swaps_both_blank_is_noop():
    df = pd.DataFrame({
        "block_time": pd.date_range("2024-01-01", periods=5, freq="D", tz="UTC"),
        "amount0": range(5),
    })
    out = app._clip_swaps(df, "", "")
    pd.testing.assert_frame_equal(out, df)


# --------------------------------------------------------------------------- #
# _rolling_apr
# --------------------------------------------------------------------------- #


def test_rolling_apr_empty_input():
    empty = pd.DataFrame()
    out = app._rolling_apr(empty, capital_base=1_000_000, window_days=30)
    assert out.empty


def test_rolling_apr_missing_net_usd_column():
    df = pd.DataFrame({"other": [1, 2, 3]})
    out = app._rolling_apr(df, capital_base=1_000_000)
    assert out.empty


def test_rolling_apr_insufficient_days():
    """Fewer than window_days of daily bars → empty result."""
    idx = pd.date_range("2024-01-01", periods=100, freq="1h", tz="UTC")
    df = pd.DataFrame({"net_usd": 100.0}, index=idx)  # ~4 days
    out = app._rolling_apr(df, capital_base=1_000_000, window_days=30)
    assert out.empty


def test_rolling_apr_computes_expected_value():
    """40 days of $100/day PnL, 30-day window, $1M capital.

    Rolling window sum = 30 × 100 = 3000. Annualised = 3000 / 1M * (365/30)
    * 100 ≈ 3.65% APR.
    """
    idx = pd.date_range("2024-01-01", periods=40 * 24, freq="1h", tz="UTC")
    net_hourly = 100.0 / 24  # so daily = 100
    df = pd.DataFrame({"net_usd": net_hourly}, index=idx)

    out = app._rolling_apr(df, capital_base=1_000_000, window_days=30)
    assert not out.empty
    # Last full-window value
    expected = 3000.0 / 1_000_000 * (365 / 30) * 100  # 3.65
    assert out.iloc[-1] == pytest.approx(expected, rel=0.01)


# --------------------------------------------------------------------------- #
# _push_run (touches st.session_state)
# --------------------------------------------------------------------------- #


class _FakeResult:
    def __init__(self, engine="test", summary=None, timeseries=None):
        self.engine = engine
        self.summary = summary or {"net_apr_pct": 5.0, "days": 30}
        self.timeseries = (
            timeseries if timeseries is not None else pd.DataFrame()
        )


def _fake_ts(days: int = 15):
    idx = pd.date_range("2024-01-01", periods=days * 24, freq="1h", tz="UTC")
    cum = pd.Series(range(days * 24), index=idx, dtype=float)
    return pd.DataFrame({"cum_net": cum})


@pytest.fixture(autouse=True)
def _reset_session_state():
    """Give every push-run test a clean session_state.runs stack.
    Streamlit's SessionState is stored on a module-level singleton so
    without this tests would leak into each other."""
    import streamlit as st
    st.session_state.clear() if hasattr(st.session_state, "clear") else None
    st.session_state["runs"] = []
    yield
    st.session_state["runs"] = []


def test_push_run_stores_daily_resampled_not_full_ts():
    """The hot fix from Tier 1 #6: full timeseries would OOM, so we
    only keep a daily resample."""
    ts = _fake_ts(days=10)  # 240 hourly rows
    res = _FakeResult(timeseries=ts)
    app._push_run(res, "test-label", {"capital_usd": 1000})

    import streamlit as st
    runs = st.session_state["runs"]
    assert len(runs) == 1
    entry = runs[0]
    assert entry["label"] == "test-label"
    # daily-resampled = 10 rows, not 240
    assert len(entry["daily_cum_net"]) == 10
    assert "timeseries" not in entry  # explicitly not stored


def test_push_run_caps_at_5():
    """Pushing more than 5 should evict the oldest."""
    for i in range(8):
        app._push_run(
            _FakeResult(timeseries=_fake_ts(days=3)),
            label=f"run-{i}",
            params={},
        )
    import streamlit as st
    runs = st.session_state["runs"]
    assert len(runs) == 5
    # Should keep the last 5 (labels run-3 through run-7)
    assert [r["label"] for r in runs] == [f"run-{i}" for i in range(3, 8)]


def test_push_run_generates_default_label_from_position():
    """Empty label → 'Run N' where N is the pushed position."""
    app._push_run(_FakeResult(timeseries=_fake_ts(days=3)), "", {})
    import streamlit as st
    assert st.session_state["runs"][0]["label"] == "Run 1"


def test_push_run_deepcopies_summary_and_params():
    """Mutating the caller's dicts must not corrupt the stored run."""
    summary = {"net_apr_pct": 5.0, "days": 30, "list_field": [1, 2, 3]}
    params = {"capital_usd": 1000, "nested": {"a": 1}}
    res = _FakeResult(summary=summary, timeseries=_fake_ts(days=3))
    app._push_run(res, "test", params)

    # Mutate originals
    summary["net_apr_pct"] = 999.0
    summary["list_field"].append(999)
    params["capital_usd"] = 999
    params["nested"]["a"] = 999

    import streamlit as st
    stored = st.session_state["runs"][0]
    assert stored["summary"]["net_apr_pct"] == 5.0
    assert stored["summary"]["list_field"] == [1, 2, 3]
    assert stored["params"]["capital_usd"] == 1000
    assert stored["params"]["nested"]["a"] == 1


def test_push_run_empty_timeseries():
    """Guard against zero-row timeseries -- ffill/resample would raise
    without the empty check."""
    res = _FakeResult(timeseries=pd.DataFrame())
    app._push_run(res, "empty", {})
    import streamlit as st
    stored = st.session_state["runs"][0]
    assert stored["daily_cum_net"].empty


# --------------------------------------------------------------------------- #
# _num (numeric coercion used to keep Arrow-compatible column dtypes)
# --------------------------------------------------------------------------- #


def test_num_passes_through_floats():
    assert app._num(1.5) == 1.5
    assert app._num(0) == 0.0
    assert app._num(-3.2) == -3.2


def test_num_coerces_none_to_nan():
    import math
    assert math.isnan(app._num(None))


def test_num_coerces_non_numeric_to_nan():
    import math
    assert math.isnan(app._num("n/a"))
    assert math.isnan(app._num("hello"))
    assert math.isnan(app._num([1, 2]))


def test_num_passes_through_numpy_scalars():
    assert app._num(np.float64(2.5)) == 2.5
    assert app._num(np.int64(7)) == 7.0


# --------------------------------------------------------------------------- #
# KPI DataFrame is Arrow-serialisable across mixed-engine runs
#
# Regression: the comparison table used to lay runs as columns, which
# mixed strings (engine name) with floats (APRs) and Nones (sharpe from
# the hourly engine). PyArrow couldn't serialise those object columns
# and st.dataframe would blow up with 'Expected bytes, got numpy.float64'.
# We now transpose (runs as rows) AND coerce numerics via _num.
# --------------------------------------------------------------------------- #


def _kpi_row(run_label: str, engine: str, summary: dict) -> dict:
    """Mirrors what _render_comparison builds per row."""
    return {
        "run": run_label,
        "engine": engine,
        "net APR (%)": app._num(summary.get("net_apr_pct")),
        "fees APR (%)": app._num(summary.get("fee_apr_pct")),
        "LVR APR (%)": app._num(summary.get("lvr_apr_pct")),
        "funding APR (%)": app._num(summary.get("funding_apr_pct")),
        "hedge cost APR (%)": app._num(summary.get("hedge_cost_apr_pct")),
        "Sharpe": app._num(summary.get("sharpe")),
        "% in range": app._num(summary.get("pct_time_in_range")),
    }


def test_kpi_mixed_engine_frame_is_arrow_serialisable():
    """Two runs from different engines produce a KPI frame with
    uniform-typed columns that pyarrow can convert without error.

    This is the regression guard for the crash in the tier-4 rollout."""
    import pyarrow as pa

    swap_run = _kpi_row(
        "1. swap-run", "swap_level",
        {
            "net_apr_pct": 26.78, "fee_apr_pct": 75.03,
            "lvr_apr_pct": -46.07, "funding_apr_pct": 5.03,
            "hedge_cost_apr_pct": -5.30, "sharpe": 1.8,
            "pct_time_in_range": 100.0,
        },
    )
    hourly_run = _kpi_row(
        "★ 2. hourly-run", "hourly",
        {
            # Hourly returns Nones for fields the fast fallback can't compute
            "net_apr_pct": 4.1, "fee_apr_pct": 8.2,
            "lvr_apr_pct": None, "funding_apr_pct": 1.2,
            "hedge_cost_apr_pct": -0.9, "sharpe": None,
            "pct_time_in_range": 87.4,
        },
    )
    kpi = pd.DataFrame([swap_run, hourly_run]).set_index("run")

    # Numeric columns must be float64, not object
    for col in ["net APR (%)", "Sharpe", "LVR APR (%)"]:
        assert kpi[col].dtype == "float64", f"{col} is not float64: {kpi[col].dtype}"

    # Arrow round-trip must succeed
    table = pa.Table.from_pandas(kpi, preserve_index=True)
    assert table.num_rows == 2
