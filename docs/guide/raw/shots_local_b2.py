import sys, time, traceback, json
sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
from shots_common import *

KL = "data/ETHUSDT-klines-1m-2026-08.csv"
FU = "data/ETHUSDT-fundingRate-2026-08.csv"
METRICS_OUT = f"{ROOT}/docs/guide/raw/shots_local_default_metrics.txt"

def dump_metrics(pg, tag):
    ms = metrics_text(pg)
    h3 = pg.locator('[data-testid="stMain"] h3').first.inner_text()
    caps = pg.locator('[data-testid="stMain"] [data-testid="stCaptionContainer"]').all_inner_texts()
    capbase = [c for c in caps if "Capital base" in c]
    loaded = [c for c in caps if "Loaded" in c]
    with open(METRICS_OUT, "a") as f:
        f.write(f"### {tag}\n"); f.write(f"banner: {h3}\n")
        for c in loaded + capbase: f.write(f"caption: {c}\n")
        for m in ms: f.write(f"metric: {m}\n")
        f.write("\n")
    log(f"{tag} banner: {h3!r}"); log(f"{tag} metrics: {ms}")
    return ms

def ybox(loc):
    b = bbox(loc); return b["y"], b["y"] + b["height"]

with sync_playwright() as p:
    log("=== LOCAL session B2 start ===")
    b, pg = launch(p, live=False)
    pg.set_default_timeout(120000)
    open_app(pg, LOCAL)
    set_select(pg, "Mode", "Real data (files)")
    set_select(pg, "CEX price file", KL)
    set_select(pg, "Funding file", FU)
    vals = {lab: select_value(pg, lab) for lab in ["Swaps file", "CEX price file", "Funding file"]}
    log(f"file selectboxes: {vals}")

    # 7. Run at defaults
    open(METRICS_OUT, "w").write("Local app http://127.0.0.1:8501, engine swap_level (reference), Mode Real data (files), Swaps file data/swaps_0x88e6a0_2026-08-01_2026-08-31.parquet, CEX price file data/ETHUSDT-klines-1m-2026-08.csv, Funding file data/ETHUSDT-fundingRate-2026-08.csv, all other sidebar controls at defaults. Text read from the DOM with Playwright.\n\n")
    t0 = time.time(); run_backtest(pg); log(f"run 7 took {time.time()-t0:.0f}s")
    log(f"run 7 alerts: {pg.locator('[data-testid=stMain] [data-testid=stAlert]').all_inner_texts()}")
    dump_metrics(pg, "run7_default_real_data")
    h = tall_once(pg); log(f"tall viewport {h}")
    src = page_png(pg, "run7_full.png")
    log("full page png kept in scratch only (19 already exists)")
    log('shots 19-25 already captured in session B; skipping crops')
    # expanders
    set_expander(pg, "Full summary", True); time.sleep(2); tall_once(pg)
    e = expander(pg, "Full summary")
    with open(METRICS_OUT, "a") as f: f.write("### run7 Full summary expander text\n" + e.inner_text() + "\n\n")
    e.screenshot(path=f"{SHOTS}/26_local_run_full_summary_open.png"); log("screenshot 26_local_run_full_summary_open.png")
    set_expander(pg, "Full summary", False); time.sleep(1)
    set_expander(pg, "Timeseries (first 500 rows)", True); time.sleep(3); tall_once(pg)
    e = expander(pg, "Timeseries (first 500 rows)")
    e.screenshot(path=f"{SHOTS}/27_local_run_timeseries_open.png"); log("screenshot 27_local_run_timeseries_open.png")
    set_expander(pg, "Timeseries (first 500 rows)", False)
    pg.set_viewport_size({"width": 1600, "height": 1000}); time.sleep(2)

    # 8. Rolling
    set_checkbox(pg, "Rolling-window distribution (30d)", True)
    t0 = time.time(); run_backtest(pg); log(f"run 8 took {time.time()-t0:.0f}s")
    dump_metrics(pg, "run8_rolling_on")
    h = tall_once(pg); src = page_png(pg, "run8_full.png")
    log(f"run 8 h3s: {pg.locator('[data-testid=stMain] h3').all_inner_texts()}")
    hr = main_h3(pg, "30-day rolling APR")
    if hr.count():
        y_r = ybox(hr); y_sum = ybox(expander(pg, "Full summary"))
        tbl = pg.locator('[data-testid="stMain"] table').all_inner_texts()
        log(f"rolling table text: {[t.replace(chr(10),' | ') for t in tbl]}")
        with open(METRICS_OUT, "a") as f: f.write("### run8 rolling distribution table\n" + "\n".join(tbl) + "\n\n")
        crop(src, "28_local_run_rolling_apr.png", y_r[0], y_sum[0] - 16, pg)
    else:
        al = pg.locator('[data-testid="stMain"] [data-testid="stAlert"]', has_text="rolling").first
        log(f"rolling alert: {al.inner_text()}")
        y = ybox(al); crop(src, "28_local_run_rolling_apr.png", y[0], y[1], pg)
    pg.set_viewport_size({"width": 1600, "height": 1000}); time.sleep(2)

    # 9. By month (+ 11 comparison)
    set_checkbox(pg, "Backtest by month", True)
    t0 = time.time(); run_backtest(pg); log(f"run 9 took {time.time()-t0:.0f}s")
    dump_metrics(pg, "run9_rolling_bymonth_on")
    h = tall_once(pg); src = page_png(pg, "run9_full.png")
    log(f"run 9 h3s: {pg.locator('[data-testid=stMain] h3').all_inner_texts()}")
    log(f"run 9 alerts: {pg.locator('[data-testid=stMain] [data-testid=stAlert]').all_inner_texts()}")
    hbm = main_h3(pg, "Backtest by month"); nxt = next_h3_after(pg, hbm)
    y1 = bbox(nxt)["y"] - 24 if nxt is not None else main_bottom_y(pg)
    crop(src, "29_local_run_by_month.png", ybox(hbm)[0], y1, pg)
    hc = main_h3(pg, "Run comparison"); log(f"Run comparison h3 count {hc.count()}")
    crop(src, "30_local_run_comparison.png", ybox(hc)[0], main_bottom_y(pg), pg)
    log(f"comparison multiselect: {pg.locator('[data-testid=stMultiSelect]').first.inner_text().replace(chr(10),' | ')!r}")
    b.close()
    log("=== LOCAL session B2 end ===")
