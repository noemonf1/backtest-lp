import sys, time; sys.path.insert(0, "/home/user/backtest-lp/docs/guide/raw")
from shots_common import *
with sync_playwright() as p:
    b, pg = launch(p, live=False); pg.set_default_timeout(60000)
    open_app(pg, LOCAL)
    set_select(pg, "Backtest engine", "hourly (fast fallback)")
    set_checkbox(pg, "Stress test (price shock)", True)
    sl = pg.locator('[data-testid="stSlider"]', has_text="Total price move").first
    print(sl.evaluate("el=>el.outerHTML")[:3000])
    b.close()
