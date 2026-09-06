import sys, time, traceback
sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
from shots_common import *

DOC_TABS = ["Engines", "Data", "Inputs", "Outputs", "Interpreting"]
EXPANDERS = ["Fetch data", "Save / load config", "Backtest window", "Position sizing",
             "Hedge policy", "Cost model", "Mechanics (advanced)", "Analysis + Run"]

with sync_playwright() as p:
    log("=== LIVE session start ===")
    b, pg = launch(p, live=True)
    open_app(pg, LIVE)
    log(f"opened {LIVE}; title={pg.title()!r}")

    # 1. Backtest tab default
    click_tab(pg, "Backtest")
    shot(pg, "01_live_backtest_tab.png", full=True)

    # 2. Documentation sub-tabs
    click_tab(pg, "Documentation")
    time.sleep(1.5)
    for i, t in enumerate(DOC_TABS):
        click_tab(pg, t)
        time.sleep(1.5)
        shot(pg, f"{2+i:02d}_live_docs_{t.lower()}.png", full=True)
    click_tab(pg, "Backtest")
    time.sleep(1)

    # 3. Sidebar expanders one at a time
    collapse_all(pg)
    log("collapsed all expanders")
    for i, lab in enumerate(EXPANDERS):
        try:
            collapse_all(pg)
            set_expander(pg, lab, True)
            e = expander(pg, lab)
            e.scroll_into_view_if_needed()
            time.sleep(0.8)
            # confirm only this one open
            opened = [EXPANDERS[j] for j in range(len(EXPANDERS)) if expander_is_open(pg, EXPANDERS[j])]
            log(f"open expanders now: {opened}")
            # collect widget labels inside
            txt = e.inner_text().replace("\n", " | ")
            log(f"expander {lab!r} text: {txt[:600]}")
            shot(pg, f"{7+i:02d}_live_sidebar_{lab.lower().replace(' ', '_').replace('/', '').replace('(', '').replace(')', '').replace('+', 'plus').replace('__','_')}.png", full=False)
        except Exception as ex:
            log(f"ERROR expander {lab}: {ex}\n{traceback.format_exc()}")
    # restore defaults: open Position sizing, Hedge policy, Analysis + Run
    collapse_all(pg)
    for lab in ["Position sizing", "Hedge policy", "Analysis + Run"]:
        set_expander(pg, lab, True)
    pg.locator('[data-testid="stSidebar"]').first.evaluate("el=>{const s=el.querySelector('[data-testid=\"stSidebarContent\"]')||el; s.scrollTop=0}")

    # 4. Demo run at defaults
    log(f"Mode select shows: {select_value(pg,'Mode')!r}; engine: {select_value(pg,'Backtest engine')!r}")
    run_backtest(pg)
    time.sleep(3)
    ms = metrics_text(pg)
    log(f"live demo metrics: {ms}")
    hdr = pg.locator('h3').all_inner_texts()
    log(f"live demo h3: {hdr}")
    warns = pg.locator('[data-testid="stAlert"]').all_inner_texts()
    log(f"live demo alerts: {[w[:120] for w in warns]}")
    shot(pg, "15_live_demo_run.png", full=True)
    b.close()
    log("=== LIVE session end ===")
