"""Phase 3 items 1, 10 and 12 through the UI (streamlit.testing.v1.AppTest).

Drives app.py exactly as a user would: engine swap_level, Mode `Real data (files)`,
CEX price file = 1m klines, Funding file = August funding CSV, click `Run backtest`.
Captures the nine metric tiles, the verdict banner, the Full summary JSON, the
30-day rolling section, the by-month section and the default range x hedge-band
sweep table. Outputs under docs/guide/raw/apptest/.
"""
import json
import re
import sys
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

OUT = Path("/home/user/backtest-lp/docs/guide/raw/apptest")
OUT.mkdir(parents=True, exist_ok=True)
APP = "/home/user/backtest-lp/app.py"
T0 = time.time()


def sel(at, label):
    for s in at.sidebar.selectbox:
        if s.label == label:
            return s
    raise KeyError(label)


def chk(at, label):
    for c in at.sidebar.checkbox:
        if c.label == label:
            return c
    raise KeyError(label)


def btn(at, label):
    for b in at.main.button:
        if b.label == label:
            return b
    raise KeyError(label)


def walk_text(block, lines, depth=0):
    for _, ch in sorted(getattr(block, "children", {}).items(), key=lambda kv: kv[0]):
        t = getattr(ch, "type", type(ch).__name__)
        if t == "metric":
            lines.append(f"{'  '*depth}METRIC label={ch.label!r} value={ch.value!r} delta={getattr(ch,'delta',None)!r}")
        elif t in ("markdown", "caption", "subheader", "header", "info", "warning", "error", "success", "text"):
            v = getattr(ch, "value", None) or getattr(ch, "body", None)
            lines.append(f"{'  '*depth}{t.upper()}: {v!r}")
        elif t == "json":
            lines.append(f"{'  '*depth}JSON: {ch.value}")
        elif t in ("dataframe", "table", "arrow_data_frame"):
            try:
                lines.append(f"{'  '*depth}DATAFRAME:\n{ch.value.to_string()}")
            except Exception as e:  # noqa: BLE001
                lines.append(f"{'  '*depth}DATAFRAME (unreadable: {e})")
        elif t in ("expander", "tab", "column", "container", "form", "main", "sidebar"):
            lines.append(f"{'  '*depth}[{t} {getattr(ch,'label',None)!r}]")
            walk_text(ch, lines, depth + 1)
        else:
            walk_text(ch, lines, depth + 1)


def setup(at):
    sel(at, "Mode").set_value("Real data (files)")
    at.run()
    sel(at, "CEX price file").set_value("data/ETHUSDT-klines-1m-2026-08.csv")
    sel(at, "Funding file").set_value("data/ETHUSDT-fundingRate-2026-08.csv")
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def run_and_dump(at, name):
    t = time.time()
    btn(at, "Run backtest").click().run()
    assert not at.exception, [str(e) for e in at.exception]
    lines = []
    walk_text(at.main, lines)
    text = "\n".join(lines) + "\n"
    (OUT / f"{name}.txt").write_text(text)
    metrics = {}
    for m in at.main.metric:
        metrics[m.label] = m.value
    js = None
    for el in at.main.json:
        try:
            js = json.loads(el.value)
        except Exception:  # noqa: BLE001
            js = el.value
    (OUT / f"{name}_metrics.json").write_text(json.dumps({"metrics": metrics, "full_summary": js}, indent=1, default=str))
    print(f"[{time.time()-T0:6.1f}s] {name}: run took {time.time()-t:.1f}s; metrics={metrics}", flush=True)
    return text


# 1. default run through the UI
at = AppTest.from_file(APP, default_timeout=3600)
at.run()
setup(at)
sidebar_lines = []
walk_text(at.sidebar, sidebar_lines)
(OUT / "sidebar_real_data.txt").write_text("\n".join(sidebar_lines) + "\n")
run_and_dump(at, "default_ui")

# 10. rolling + by-month
chk(at, "Rolling-window distribution (30d)").set_value(True)
chk(at, "Backtest by month").set_value(True)
at.run()
run_and_dump(at, "default_ui_rolling_bymonth")
chk(at, "Rolling-window distribution (30d)").set_value(False)
chk(at, "Backtest by month").set_value(False)

# 12. the app's own sweep at its default grid
chk(at, "Run range × hedge-band sweep").set_value(True)
at.run()
text = run_and_dump(at, "default_ui_sweep")
print("sweep dump lines:", text.count("\n"), flush=True)
print(f"ALL DONE {time.time()-T0:.0f}s", flush=True)
