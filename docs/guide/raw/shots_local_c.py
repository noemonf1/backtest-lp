# Session C: swap_level real data + sweep only (fresh browser session)
import sys, time, traceback, json, shutil
sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
from shots_common import *
KL = "data/ETHUSDT-klines-1m-2026-08.csv"; FU = "data/ETHUSDT-fundingRate-2026-08.csv"
SWEEP_OUT = f"{ROOT}/docs/guide/raw/shots_local_sweep_table.txt"
def ybox(loc):
    b = bbox(loc); return b["y"], b["y"] + b["height"]

with sync_playwright() as p:
    log("=== LOCAL session C (sweep) start ===")
    b, pg = launch(p, live=False)
    pg.set_default_timeout(120000)
    open_app(pg, LOCAL)
    set_select(pg, "Mode", "Real data (files)")
    set_select(pg, "CEX price file", KL)
    set_select(pg, "Funding file", FU)
    log(f"files: { {lab: select_value(pg, lab) for lab in ['Swaps file','CEX price file','Funding file']} }")
    set_checkbox(pg, "Run range × hedge-band sweep", True)
    for lab in ["Rolling-window distribution (30d)", "Backtest by month"]:
        log(f"checkbox {lab!r} checked={checkbox(pg, lab).locator('input').first.is_checked()}")
    t0 = time.time()
    run_backtest(pg, timeout=1500000)
    log(f"run 10 (sweep) took {time.time()-t0:.0f}s")
    time.sleep(3)
    log(f"run 10 metrics: {metrics_text(pg)}")
    log(f"run 10 h3s: {pg.locator('[data-testid=stMain] h3').all_inner_texts()}")
    log(f"run 10 alerts: {pg.locator('[data-testid=stMain] [data-testid=stAlert]').all_inner_texts()}")
    # sweep controls text
    for key in ["sweep_x_field", "sweep_y_field"]:
        pass
    sw_inputs = pg.locator('[data-testid="stMain"] [data-testid="stTextInput"]').all_inner_texts()
    log(f"sweep text inputs: {[t.replace(chr(10),' | ') for t in sw_inputs]}")
    h = tall_once(pg); src = page_png(pg, "run10_full.png")
    shutil.copy(src, f"{SHOTS}/31_local_run_sweep_full.png"); log(f"screenshot 31_local_run_sweep_full.png viewport 1600x{h}")
    hs = main_h3(pg, "Parameter sweep"); nxt = next_h3_after(pg, hs)
    y1 = bbox(nxt)["y"] - 24 if nxt is not None else main_bottom_y(pg)
    crop(src, "32_local_run_sweep_section.png", ybox(hs)[0], y1, pg)
    set_expander(pg, "Full sweep table", True); time.sleep(3); tall_once(pg)
    e = expander(pg, "Full sweep table")
    e.screenshot(path=f"{SHOTS}/33_local_run_sweep_table_open.png"); log("screenshot 33_local_run_sweep_table_open.png")
    # try to read the grid text
    txt = ""
    try:
        cells = e.locator('[role="gridcell"], [role="columnheader"], [role="rowheader"]')
        n = cells.count(); log(f"grid cells found: {n}")
        rows = {}
        for i in range(n):
            c = cells.nth(i)
            r = c.get_attribute("aria-rowindex") or c.evaluate("el=>el.closest('[role=row]')?.getAttribute('aria-rowindex')")
            rows.setdefault(r, []).append(c.inner_text())
        txt = "\n".join("\t".join(v) for k, v in rows.items())
    except Exception as ex:
        log(f"grid read failed: {ex}")
    if not txt.strip():
        txt = e.inner_text()
    with open(SWEEP_OUT, "w") as f:
        f.write("Sweep table as read from the DOM of the Full sweep table expander (glide-data-grid only exposes the rows currently rendered).\n\n" + txt + "\n")
    log(f"sweep table text chars: {len(txt)}")
    b.close()
    log("=== LOCAL session C end ===")
