import os, time, json
from playwright.sync_api import sync_playwright
LIVE="https://backtest-lp-production.up.railway.app/"
with sync_playwright() as p:
    b=p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=["--proxy-server="+os.environ["HTTPS_PROXY"],"--ssl-version-max=tls1.2"])
    pg=b.new_page(viewport={"width":1600,"height":1000})
    pg.goto(LIVE, wait_until="networkidle", timeout=120000); time.sleep(5)
    exps=pg.locator('[data-testid="stExpander"]')
    print("expanders", exps.count())
    for i in range(exps.count()):
        e=exps.nth(i)
        summ=e.locator('summary')
        html=e.evaluate("el=>el.outerHTML.slice(0,300)")
        print(i, repr(summ.inner_text()[:60]), html.replace("\n"," ")[:300])
    sbs=pg.locator('[data-testid="stSelectbox"]')
    print("selectboxes", sbs.count())
    for i in range(sbs.count()):
        print(i, repr(sbs.nth(i).inner_text()[:80]))
    tabs=pg.locator('button[role="tab"]')
    print("tabs", tabs.count(), [tabs.nth(i).inner_text() for i in range(tabs.count())])
    print("status widget", pg.locator('[data-testid="stStatusWidget"]').count())
    print("sidebar", pg.locator('[data-testid="stSidebar"]').count(), pg.locator('[data-testid="stSidebar"]').evaluate("el=>[el.scrollHeight, el.clientHeight]"))
    print("run btn", pg.locator('button', has_text="Run backtest").count())
    b.close()
