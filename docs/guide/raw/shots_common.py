import os, time, datetime
from playwright.sync_api import sync_playwright

ROOT = "/home/user/backtest-lp"
SHOTS = f"{ROOT}/docs/guide/screenshots"
LOG = f"{ROOT}/docs/guide/raw/shots_log.txt"
LIVE = "https://backtest-lp-production.up.railway.app/"
LOCAL = "http://127.0.0.1:8501"

def log(msg):
    line = f"{datetime.datetime.utcnow().isoformat(timespec='seconds')}Z {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def launch(p, live):
    args = ["--proxy-server=" + os.environ["HTTPS_PROXY"], "--ssl-version-max=tls1.2"] if live else []
    b = p.chromium.launch(executable_path="/opt/pw-browsers/chromium", args=args)
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    return b, pg

def open_app(pg, url):
    pg.goto(url, wait_until="networkidle", timeout=120000)
    time.sleep(5)
    settle(pg, 2)

def settle(pg, sleep=2.5, timeout=600000):
    time.sleep(0.8)
    try:
        pg.wait_for_selector('[data-testid="stStatusWidget"]', state="detached", timeout=timeout)
    except Exception as e:
        log(f"settle: status widget wait failed: {e}")
    # also wait for any "Running..." text to vanish
    time.sleep(sleep)

def shot(pg, name, full=True, locator=None, clip=None):
    path = f"{SHOTS}/{name}"
    if locator is not None:
        locator.scroll_into_view_if_needed()
        time.sleep(0.6)
        locator.screenshot(path=path)
    else:
        pg.screenshot(path=path, full_page=full, clip=clip)
    log(f"screenshot {name} ({os.path.getsize(path)} bytes)")
    return path

def expander(pg, label):
    return pg.locator('[data-testid="stExpander"]', has=pg.locator("summary", has_text=label)).first

def expander_is_open(pg, label):
    return expander(pg, label).locator("details").first.evaluate("el=>el.hasAttribute('open')")

def set_expander(pg, label, open_):
    e = expander(pg, label)
    if expander_is_open(pg, label) != open_:
        e.locator("summary").first.scroll_into_view_if_needed()
        e.locator("summary").first.click()
        time.sleep(0.8)
    assert expander_is_open(pg, label) == open_, f"expander {label} state mismatch"

def collapse_all(pg):
    exps = pg.locator('[data-testid="stExpander"]')
    for i in range(exps.count()):
        e = exps.nth(i)
        if e.locator("details").first.evaluate("el=>el.hasAttribute('open')"):
            e.locator("summary").first.scroll_into_view_if_needed()
            e.locator("summary").first.click()
            time.sleep(0.5)

def selectbox(pg, label):
    return pg.locator('[data-testid="stSelectbox"]', has_text=label).first

def set_select(pg, label, option_text):
    sb = selectbox(pg, label)
    sb.scroll_into_view_if_needed()
    sb.locator('input').first.click()
    time.sleep(0.7)
    opts = pg.locator('[role="option"]')
    n = opts.count()
    texts = [opts.nth(i).inner_text() for i in range(n)]
    target = None
    for i, t in enumerate(texts):
        if t.strip() == option_text.strip():
            target = i; break
    if target is None:
        for i, t in enumerate(texts):
            if option_text in t:
                target = i; break
    if target is None:
        raise RuntimeError(f"option {option_text!r} not in {texts}")
    opts.nth(target).click()
    settle(pg, 2)
    got = sb.inner_text()
    log(f"set_select {label!r} -> {option_text!r}; widget now shows {got.replace(chr(10),' | ')!r}")
    return got

def select_value(pg, label):
    sb = selectbox(pg, label)
    return sb.locator('input').first.evaluate("el=>el.value") or sb.inner_text()

def click_tab(pg, label, nth_container=None):
    tabs = pg.locator('[data-testid="stTab"]', has_text=label)
    if tabs.count() == 0:
        tabs = pg.locator('button[role="tab"]', has_text=label)
    t = tabs.first
    t.scroll_into_view_if_needed()
    t.click()
    time.sleep(1.5)

def checkbox(pg, label):
    return pg.locator('[data-testid="stCheckbox"]', has_text=label).first

def set_checkbox(pg, label, value):
    cb = checkbox(pg, label)
    cb.scroll_into_view_if_needed()
    inp = cb.locator('input[type="checkbox"]').first
    cur = inp.is_checked()
    if cur != value:
        cb.locator("label").first.click()
        settle(pg, 2)
    now = cb.locator('input[type="checkbox"]').first.is_checked()
    log(f"set_checkbox {label!r} -> {value}; now {now}")
    assert now == value

def run_backtest(pg, timeout=1200000):
    btn = pg.locator("button", has_text="Run backtest").first
    btn.scroll_into_view_if_needed()
    btn.click()
    log("clicked Run backtest")
    time.sleep(3)
    settle(pg, 4, timeout=timeout)
    log("run finished (status widget detached)")

def main_area(pg):
    return pg.locator('[data-testid="stMain"]').first

def section_by_subheader(pg, text):
    """Return the heading element for a subheader"""
    return pg.locator('h3', has_text=text).first

def metrics_text(pg):
    ms = pg.locator('[data-testid="stMetric"]')
    return [ms.nth(i).inner_text().replace("\n", " | ") for i in range(ms.count())]

def sidebar_scroll_to(pg, locator):
    locator.scroll_into_view_if_needed()
    time.sleep(0.5)
