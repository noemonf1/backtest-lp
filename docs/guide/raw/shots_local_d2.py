# Session D: hourly engine, Local CSV files, stress test
import sys, time, traceback, json, shutil
sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
from shots_common import *
KL = "data/ETHUSDT-klines-1m-2026-08.csv"; FU = "data/ETHUSDT-fundingRate-2026-08.csv"
METRICS_OUT = f"{ROOT}/docs/guide/raw/shots_local_hourly_metrics.txt"
def ybox(loc):
    b = bbox(loc); return b["y"], b["y"] + b["height"]
def sidebar_top(pg):
    pg.locator('[data-testid="stSidebar"]').first.evaluate("el=>{const s=el.querySelector('[data-testid=\"stSidebarContent\"]')||el; s.scrollTop=0}"); time.sleep(0.6)
def dump(pg, tag):
    ms = metrics_text(pg); h3 = pg.locator('[data-testid="stMain"] h3').first.inner_text()
    al = pg.locator('[data-testid="stMain"] [data-testid="stAlert"]').all_inner_texts()
    caps = [c for c in pg.locator('[data-testid="stMain"] [data-testid="stCaptionContainer"]').all_inner_texts() if "Capital base" in c or "Loaded" in c]
    with open(METRICS_OUT, "a") as f:
        f.write(f"### {tag}\nbanner: {h3}\n" + "".join(f"alert: {a}\n" for a in al) + "".join(f"caption: {c}\n" for c in caps) + "".join(f"metric: {m}\n" for m in ms) + "\n")
    log(f"{tag} banner {h3!r} alerts {al} metrics {ms}")

with sync_playwright() as p:
    log("=== LOCAL session D2 (hourly stress) start ===")
    b, pg = launch(p, live=False)
    pg.set_default_timeout(120000)
    open_app(pg, LOCAL)
    set_select(pg, "Backtest engine", "hourly (fast fallback)")
    set_select(pg, "Mode", "Local CSV files")
    set_select(pg, "CEX price file", KL)
    log(f"hourly files: { {lab: select_value(pg, lab) for lab in ['CEX price file','Funding file']} }")
    log(f"hourly widgets: {[t.replace(chr(10),' | ')[:80] for t in pg.locator('[data-testid=stSidebar] [data-testid=stSelectbox]').all_inner_texts()]}")
    open(METRICS_OUT, "w").write("Local app, engine hourly (fast fallback), Mode Local CSV files, CEX price file data/ETHUSDT-klines-1m-2026-08.csv, Funding file data/ETHUSDT-fundingRate-2026-08.csv.\n\n")
    log('34-37 already captured in session D; skipping')
    collapse_all(pg)
    for lab in ['Position sizing', 'Hedge policy']: set_expander(pg, lab, True)
    # 12. stress controls
    set_expander(pg, "Analysis + Run", True)
    set_checkbox(pg, "Stress test (price shock)", True)
    e = expander(pg, "Analysis + Run"); e.scroll_into_view_if_needed(); time.sleep(1)
    log(f"Analysis + Run (stress on): {e.inner_text().replace(chr(10),' | ')}")
    ni = pg.locator('[data-testid="stNumberInput"]', has_text="Shock over last N days").first
    log(f"Shock days value: {ni.locator('input').first.input_value()}")
    sl = pg.locator('[data-testid="stSlider"]', has_text="Total price move").first
    log(f"slider text: {sl.inner_text().replace(chr(10),' | ')}; value {sl.locator('input[type=range]').first.input_value()}")
    log(f"Shape: {select_value(pg,'Shape')}")
    collapse_all(pg); set_expander(pg, "Analysis + Run", True); e.scroll_into_view_if_needed(); time.sleep(0.8)
    shot(pg, "38_local_hourly_stress_controls_default.png", full=False)
    t0 = time.time(); run_backtest(pg); log(f"stress run A took {time.time()-t0:.0f}s")
    dump(pg, "hourly_stress_-20pct_7d_linear")
    h = tall_once(pg); src = page_png(pg, "stressA_full.png")
    shutil.copy(src, f"{SHOTS}/39_local_hourly_stress_default_full.png"); log(f"screenshot 39_local_hourly_stress_default_full.png viewport 1600x{h}")
    warn = pg.locator('[data-testid="stMain"] [data-testid="stAlert"]', has_text="Stress test active").first
    ms = pg.locator('[data-testid="stMetric"]'); mb = [ms.nth(i).bounding_box() for i in range(ms.count())]
    crop(src, "40_local_hourly_stress_default_metrics.png", ybox(warn)[0], max(x["y"]+x["height"] for x in mb), pg)
    pg.set_viewport_size({"width": 1600, "height": 1000}); time.sleep(2)
    # set shock to -30 over 1 day
    ni = pg.locator('[data-testid="stNumberInput"]', has_text="Shock over last N days").first
    inp = ni.locator('input').first; inp.scroll_into_view_if_needed(); inp.click(); inp.press("Control+a"); inp.type("1"); inp.press("Enter"); settle(pg, 2)
    log(f"Shock days now: {ni.locator('input').first.input_value()}")
    sl = pg.locator('[data-testid="stSlider"]', has_text="Total price move").first
    inp = sl.locator('input[type=range]').first
    inp.scroll_into_view_if_needed(); inp.focus()
    for _ in range(10):
        inp.press("ArrowLeft"); time.sleep(0.2)
    settle(pg, 2)
    v = sl.locator('input[type=range]').first.input_value(); log(f"slider now: {v}")
    if v != "-30":
        cur = int(float(v)); inp = sl.locator('input[type=range]').first; inp.focus()
        for _ in range(abs(cur + 30)):
            inp.press("ArrowLeft" if cur > -30 else "ArrowRight"); time.sleep(0.2)
        settle(pg, 2); v = sl.locator('input[type=range]').first.input_value(); log(f"slider now (2nd try): {v}")
    log(f"stress controls now: days={pg.locator('[data-testid=stNumberInput]', has_text='Shock over last N days').first.locator('input').first.input_value()} pct={v} shape={select_value(pg,'Shape')}")
    e = expander(pg, "Analysis + Run"); e.scroll_into_view_if_needed(); time.sleep(0.8)
    shot(pg, "41_local_hourly_stress_controls_-30_1d.png", full=False)
    t0 = time.time(); run_backtest(pg); log(f"stress run B took {time.time()-t0:.0f}s")
    dump(pg, "hourly_stress_-30pct_1d_linear")
    h = tall_once(pg); src = page_png(pg, "stressB_full.png")
    shutil.copy(src, f"{SHOTS}/42_local_hourly_stress_-30_1d_full.png"); log(f"screenshot 42_local_hourly_stress_-30_1d_full.png viewport 1600x{h}")
    warn = pg.locator('[data-testid="stMain"] [data-testid="stAlert"]', has_text="Stress test active").first
    ms = pg.locator('[data-testid="stMetric"]'); mb = [ms.nth(i).bounding_box() for i in range(ms.count())]
    crop(src, "43_local_hourly_stress_-30_1d_metrics.png", ybox(warn)[0], max(x["y"]+x["height"] for x in mb), pg)
    hc = main_h3(pg, "Run comparison")
    if hc.count():
        crop(src, "44_local_hourly_run_comparison.png", ybox(hc)[0], main_bottom_y(pg), pg)
    b.close()
    log("=== LOCAL session D2 end ===")
