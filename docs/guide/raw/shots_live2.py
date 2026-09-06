import sys, time, traceback
sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
from shots_common import *
DOC_TABS = ["Engines", "Data", "Inputs", "Outputs", "Interpreting"]
with sync_playwright() as p:
    log("=== LIVE session 2 (redo full-page shots with tall viewport) ===")
    b, pg = launch(p, live=True)
    open_app(pg, LIVE)
    click_tab(pg, "Backtest")
    full_shot(pg, "01_live_backtest_tab.png")
    click_tab(pg, "Documentation"); time.sleep(1.5)
    for i, t in enumerate(DOC_TABS):
        click_tab(pg, t); time.sleep(1.5)
        full_shot(pg, f"{2+i:02d}_live_docs_{t.lower()}.png")
    click_tab(pg, "Backtest"); time.sleep(1)
    log(f"Mode select shows: {select_value(pg,'Mode')!r}; engine: {select_value(pg,'Backtest engine')!r}")
    run_backtest(pg)
    time.sleep(3)
    log(f"live demo metrics: {metrics_text(pg)}")
    h3s = pg.locator('[data-testid="stMain"] h3').all_inner_texts()[:6]
    log(f"live demo h3: {h3s}")
    full_shot(pg, "15_live_demo_run.png")
    b.close()
    log("=== LIVE session 2 end ===")
