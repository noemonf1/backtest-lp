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
        f.write(f"### {tag}\n")
        f.write(f"banner: {h3}\n")
        for c in loaded + capbase: f.write(f"caption: {c}\n")
        for m in ms: f.write(f"metric: {m}\n")
        f.write("\n")
    log(f"{tag} banner: {h3!r}")
    log(f"{tag} metrics: {ms}")
    return ms

with sync_playwright() as p:
    log("=== LOCAL session A start ===")
    b, pg = launch(p, live=False)
    open_app(pg, LOCAL)
    log(f"opened {LOCAL}; title={pg.title()!r}")

    # 5. Fetch data expander
    collapse_all(pg)
    set_expander(pg, "Fetch data", True)
    expander(pg, "Fetch data").scroll_into_view_if_needed()
    txt = expander(pg, "Fetch data").inner_text().replace("\n", " | ")
    log(f"local Fetch data expander text: {txt}")
    shot(pg, "16_local_sidebar_fetch_data.png", full=False)
    set_expander(pg, "Fetch data", False)

    # 6. Real data mode + file selectboxes
    set_select(pg, "Mode", "Real data (files)")
    for lab in ["Swaps file", "CEX price file", "Funding file"]:
        log(f"  {lab}: {select_value(pg, lab)!r}")
    pg.locator('[data-testid="stSidebar"]').first.evaluate("el=>{const s=el.querySelector('[data-testid=\"stSidebarContent\"]')||el; s.scrollTop=0}")
    time.sleep(0.8)
    shot(pg, "17_local_sidebar_real_data_defaults.png", full=False)
    set_select(pg, "CEX price file", KL)
    set_select(pg, "Funding file", FU)
    vals = {lab: select_value(pg, lab) for lab in ["Swaps file", "CEX price file", "Funding file"]}
    log(f"file selectboxes now: {vals}")
    pg.locator('[data-testid="stSidebar"]').first.evaluate("el=>{const s=el.querySelector('[data-testid=\"stSidebarContent\"]')||el; s.scrollTop=0}")
    time.sleep(0.8)
    shot(pg, "18_local_sidebar_real_data_files_set.png", full=False)

    # 7. Run at defaults
    open(METRICS_OUT, "w").write("Local app http://127.0.0.1:8501, engine swap_level, Mode Real data (files), swaps_0x88e6a0_2026-08-01_2026-08-31.parquet + ETHUSDT-klines-1m-2026-08.csv + ETHUSDT-fundingRate-2026-08.csv, all other sidebar controls at defaults. Text read from the DOM.\n\n")
    t0 = time.time()
    run_backtest(pg)
    log(f"run 7 took {time.time()-t0:.0f}s")
    alerts = pg.locator('[data-testid="stMain"] [data-testid="stAlert"]').all_inner_texts()
    log(f"run 7 alerts: {alerts}")
    dump_metrics(pg, "run7_default_real_data")
    full_shot(pg, "19_local_run_default_full.png")
    with Tall(pg):
        h3 = main_h3(pg, "APR")  # verdict banner h3
        cap = pg.locator('[data-testid="stMain"] [data-testid="stCaptionContainer"]', has_text="Capital base").first
        clip_shot(pg, "20_local_run_verdict_banner.png", h3, cap)
        ms = pg.locator('[data-testid="stMetric"]')
        boxes = [ms.nth(i).bounding_box() for i in range(ms.count())]
        top = min(bb["y"] for bb in boxes); bot = max(bb["y"] + bb["height"] for bb in boxes)
        clip_shot(pg, "21_local_run_metric_tiles.png", ms.first, bottom_y=bot)
        h_cum = main_h3(pg, "Cumulative PnL"); h_attr = main_h3(pg, "PnL attribution"); h_eth = main_h3(pg, "ETH price and LP delta")
        clip_shot(pg, "22_local_run_cumulative_pnl.png", h_cum, bottom_y=bbox(h_attr)["y"] - 20)
        click_tab(pg, "Cumulative"); time.sleep(1.5)
        clip_shot(pg, "23_local_run_attribution_cumulative.png", h_attr, bottom_y=bbox(h_eth)["y"] - 20)
        click_tab(pg, "Daily bars"); time.sleep(2)
        clip_shot(pg, "24_local_run_attribution_daily_bars.png", h_attr, bottom_y=bbox(h_eth)["y"] - 20)
        click_tab(pg, "Cumulative"); time.sleep(1)
        exp_sum = expander(pg, "Full summary")
        clip_shot(pg, "25_local_run_eth_price_lp_delta.png", h_eth, bottom_y=bbox(exp_sum)["y"] - 20)
    set_expander(pg, "Full summary", True); time.sleep(1.5)
    with Tall(pg):
        exp_sum = expander(pg, "Full summary")
        summ_txt = exp_sum.inner_text()
        with open(METRICS_OUT, "a") as f: f.write("### run7 Full summary expander text\n" + summ_txt + "\n\n")
        shot(pg, "26_local_run_full_summary_open.png", locator=exp_sum)
    set_expander(pg, "Full summary", False)
    set_expander(pg, "Timeseries (first 500 rows)", True); time.sleep(2.5)
    with Tall(pg):
        exp_ts = expander(pg, "Timeseries (first 500 rows)")
        shot(pg, "27_local_run_timeseries_open.png", locator=exp_ts)
    set_expander(pg, "Timeseries (first 500 rows)", False)

    # 8. Rolling window
    set_checkbox(pg, "Rolling-window distribution (30d)", True)
    t0 = time.time(); run_backtest(pg); log(f"run 8 took {time.time()-t0:.0f}s")
    dump_metrics(pg, "run8_rolling_on")
    log(f"run 8 h3s: {pg.locator('[data-testid=stMain] h3').all_inner_texts()}")
    with Tall(pg):
        h_roll = main_h3(pg, "30-day rolling APR")
        if h_roll.count():
            exp_sum = expander(pg, "Full summary")
            clip_shot(pg, "28_local_run_rolling_apr.png", h_roll, bottom_y=bbox(exp_sum)["y"] - 20)
        else:
            infos = pg.locator('[data-testid="stMain"] [data-testid="stAlert"]', has_text="rolling")
            log(f"rolling: no h3; alert text: {infos.all_inner_texts()}")
            shot(pg, "28_local_run_rolling_apr.png", locator=infos.first)

    # 9. By month
    set_checkbox(pg, "Backtest by month", True)
    t0 = time.time(); run_backtest(pg); log(f"run 9 took {time.time()-t0:.0f}s")
    dump_metrics(pg, "run9_rolling_bymonth_on")
    with Tall(pg):
        h_bm = main_h3(pg, "Backtest by month")
        nxt = next_h3_after(pg, h_bm)
        by = bbox(nxt)["y"] - 20 if nxt is not None else main_bottom_y(pg)
        sec_txt = pg.evaluate("""(y0)=>{const els=[...document.querySelectorAll('[data-testid="stMain"] [data-testid="stAlert"]')];return els.map(e=>e.innerText)}""", 0)
        log(f"by-month alerts: {sec_txt}")
        clip_shot(pg, "29_local_run_by_month.png", h_bm, bottom_y=by)
        # 11. comparison (>=2 runs exist)
        h_cmp = main_h3(pg, "Run comparison")
        log(f"Run comparison h3 count: {h_cmp.count()}")
        clip_shot(pg, "30_local_run_comparison.png", h_cmp, bottom_y=main_bottom_y(pg))
        ms_sel = pg.locator('[data-testid="stMultiSelect"]').first
        log(f"comparison multiselect: {ms_sel.inner_text().replace(chr(10),' | ')!r}")
    b.close()
    log("=== LOCAL session A end ===")
