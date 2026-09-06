#!/usr/bin/env python3
"""AppTest harness for docs/guide/APP_MAP.md.

Runs app.py headless via streamlit.testing.v1.AppTest in every sidebar
mode, dumps every widget (label / key / type / value / options / limits /
help) and the Documentation tab text.  No network: swap_level Demo mode
and hourly Local-CSV mode never fetch; the hourly Live mode is only
rendered (Run is never clicked there).

Outputs (all under docs/guide/raw/):
  appmap_widgets_<scenario>.txt   one file per sidebar scenario
  appmap_doc_tabs.txt             Documentation tab + 5 sub-tabs, verbatim
  appmap_main_after_run.txt       main-area widgets after two demo runs
"""
import sys
import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = "/home/user/backtest-lp/app.py"
OUT = Path("/home/user/backtest-lp/docs/guide/raw")

WIDGET_TYPES = {
    "selectbox", "select_slider", "slider", "number_input", "text_input",
    "checkbox", "button", "download_button", "file_uploader", "multiselect",
    "radio", "toggle", "text_area", "date_input", "time_input", "color_picker",
}
TEXT_TYPES = {"markdown", "caption", "header", "subheader", "title", "info",
              "warning", "error", "success", "text", "code", "json", "help"}


def _proto_fields(el):
    """Pull min/max/step/options/help/placeholder from the element proto."""
    p = getattr(el, "proto", None)
    out = {}
    if p is None:
        return out
    for f in ("min", "max", "step", "help", "placeholder", "format",
              "has_min", "has_max", "default", "options", "type"):
        if hasattr(p, f):
            try:
                v = getattr(p, f)
            except Exception:
                continue
            if f == "options":
                v = list(v)
            if f == "help" and not v:
                continue
            if f in ("has_min", "has_max") and not v:
                continue
            out[f] = v
    return out


def walk(block, depth, lines, only_widgets=False):
    children = getattr(block, "children", None)
    if children is None:
        return
    for _, ch in sorted(children.items(), key=lambda kv: kv[0]):
        t = getattr(ch, "type", type(ch).__name__)
        label = getattr(ch, "label", None)
        pad = "  " * depth
        if t in ("expander", "tab", "column", "container", "form", "sidebar", "main"):
            hdr = f"{pad}[{t}]"
            if label:
                hdr += f" label={label!r}"
            lines.append(hdr)
            walk(ch, depth + 1, lines, only_widgets)
            continue
        if t in WIDGET_TYPES:
            key = getattr(ch, "key", None)
            val = getattr(ch, "value", None)
            extra = _proto_fields(ch)
            opts = getattr(ch, "options", None)
            if opts is not None:
                extra["options"] = list(opts)
            for f in ("min", "max", "step"):
                if hasattr(ch, f) and f not in extra:
                    extra[f] = getattr(ch, f)
            lines.append(
                f"{pad}<{t}> label={label!r} key={key!r} value={val!r} {extra}"
            )
        elif not only_widgets and t in TEXT_TYPES:
            v = getattr(ch, "value", None)
            if v is None:
                v = getattr(ch, "body", None)
            lines.append(f"{pad}({t}) {v!r}")
        elif not only_widgets:
            lines.append(f"{pad}({t})")
            walk(ch, depth + 1, lines, only_widgets)
        else:
            walk(ch, depth + 1, lines, only_widgets)


def dump(at, name, only_widgets=False, which="sidebar"):
    lines = []
    blk = at.sidebar if which == "sidebar" else at.main
    walk(blk, 0, lines, only_widgets)
    text = "\n".join(lines) + "\n"
    (OUT / f"appmap_widgets_{name}.txt").write_text(text)
    print(f"##### {name} ({which}) #####")
    print(text)
    n = sum(1 for l in lines if l.lstrip().startswith("<"))
    print(f"##### {name}: {n} widgets #####\n")
    return n


def fresh():
    at = AppTest.from_file(APP, default_timeout=600)
    at.run()
    assert not at.exception, [str(e) for e in at.exception]
    return at


def sidebar_sel(at, label):
    for s in at.sidebar.selectbox:
        if s.label == label:
            return s
    raise KeyError(label)


def sidebar_chk(at, label):
    for c in at.sidebar.checkbox:
        if c.label == label:
            return c
    raise KeyError(label)


def main_btn(at, label):
    for b in at.main.button:
        if b.label == label:
            return b
    raise KeyError(label)


counts = {}

# ---- 1. swap_level + Demo (defaults) -------------------------------------
at = fresh()
counts["swap_level_demo"] = dump(at, "swap_level_demo")

# Documentation tab text --------------------------------------------------
doc_lines = []
for tab in at.tabs:
    if tab.label == "Documentation":
        doc_lines.append("=== Documentation tab (top-level text) ===")
        for md in tab.markdown:
            doc_lines.append(md.value)
        for sub in tab.tabs:
            doc_lines.append(f"\n=== Sub-tab: {sub.label} ===")
            for md in sub.markdown:
                doc_lines.append(md.value)
            for sh in sub.subheader:
                doc_lines.append(f"[subheader] {sh.value}")
            # st.help renders as a 'doc_string' element
            for ch in sub.children.values():
                if getattr(ch, "type", "") == "doc_string":
                    doc_lines.append(f"[st.help] {ch.proto}")
doc_text = "\n".join(doc_lines) + "\n"
(OUT / "appmap_doc_tabs.txt").write_text(doc_text)
print("##### DOC TABS #####")
print(doc_text)

# ---- 2. swap_level + Real data (files) -----------------------------------
at = fresh()
sidebar_sel(at, "Mode").set_value("Real data (files)").run()
assert not at.exception, [str(e) for e in at.exception]
counts["swap_level_real"] = dump(at, "swap_level_real")

# ---- 3. hourly + Live fetch (defaults) -----------------------------------
at = fresh()
sidebar_sel(at, "Backtest engine").set_value("hourly (fast fallback)").run()
assert not at.exception, [str(e) for e in at.exception]
counts["hourly_live"] = dump(at, "hourly_live")

# ---- 4. hourly + Absolute bounds + periodic + stress on ------------------
sidebar_sel(at, "Range specification").set_value("Absolute price bounds").run()
sidebar_sel(at, "Rebalance mode").set_value("periodic").run()
sidebar_chk(at, "Stress test (price shock)").set_value(True).run()
assert not at.exception, [str(e) for e in at.exception]
counts["hourly_live_abs_periodic_stress"] = dump(at, "hourly_live_abs_periodic_stress")

# ---- 5. hourly + Local CSV files -----------------------------------------
at = fresh()
sidebar_sel(at, "Backtest engine").set_value("hourly (fast fallback)").run()
sidebar_sel(at, "Mode").set_value("Local CSV files").run()
assert not at.exception, [str(e) for e in at.exception]
counts["hourly_local"] = dump(at, "hourly_local")

# ---- 6. main area before any run -----------------------------------------
at = fresh()
dump(at, "main_before_run", which="main")

# ---- 7. Demo run x2 with rolling + sweep + by-month on --------------------
at = fresh()
for s in at.sidebar.slider:
    if s.label == "Demo days":
        s.set_value(5)
at.run()
sidebar_chk(at, "Rolling-window distribution (30d)").set_value(True)
sidebar_chk(at, "Backtest by month").set_value(True)
sidebar_chk(at, "Run range × hedge-band sweep").set_value(True)
at.run()
t0 = time.time()
main_btn(at, "Run backtest").click().run()
print(f"run 1 took {time.time()-t0:.1f}s; exceptions: {[str(e) for e in at.exception]}")
t0 = time.time()
main_btn(at, "Run backtest").click().run()
print(f"run 2 took {time.time()-t0:.1f}s; exceptions: {[str(e) for e in at.exception]}")
lines = []
walk(at.main, 0, lines, only_widgets=False)
text = "\n".join(lines) + "\n"
(OUT / "appmap_main_after_run.txt").write_text(text)
print("##### MAIN AFTER 2 RUNS #####")
print(text)
print("session_state keys after runs:")
for k in sorted(at.session_state.filtered_state.keys() if hasattr(at.session_state, "filtered_state") else at.session_state):
    print("  ", k)

print("\n##### COUNTS #####")
for k, v in counts.items():
    print(f"{k}: {v}")
