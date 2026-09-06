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

def content_height(pg):
    return pg.evaluate("""()=>{
      const m=document.querySelector('[data-testid="stMain"]')||document.querySelector('section.main');
      const sb=document.querySelector('[data-testid="stSidebar"]');
      const sbc=sb? (sb.querySelector('[data-testid="stSidebarContent"]')||sb):null;
      const hm=m? m.scrollHeight:0;
      const hs=sbc? sbc.scrollHeight:0;
      return [hm,hs];
    }""")

def full_shot(pg, name, include_sidebar=True, max_h=14000):
    """Streamlit scrolls inside [data-testid=stMain], so full_page=True only
    captures one viewport. Resize the viewport to the content height instead."""
    hm, hs = content_height(pg)
    h = hm if not include_sidebar else max(hm, hs)
    h = int(min(max(h + 40, 1000), max_h))
    pg.set_viewport_size({"width": 1600, "height": h})
    time.sleep(1.5)
    # re-measure once (charts can re-layout on resize)
    hm2, hs2 = content_height(pg)
    h2 = int(min(max((hm2 if not include_sidebar else max(hm2, hs2)) + 40, 1000), max_h))
    if abs(h2 - h) > 30:
        pg.set_viewport_size({"width": 1600, "height": h2}); time.sleep(1.5); h = h2
    path = f"{SHOTS}/{name}"
    pg.screenshot(path=path, full_page=False)
    log(f"full_shot {name} viewport 1600x{h} main={hm2} sidebar={hs2} ({os.path.getsize(path)} bytes)")
    pg.set_viewport_size({"width": 1600, "height": 1000})
    time.sleep(1.0)
    return path

# ---- tall-viewport clip helpers ------------------------------------------
class Tall:
    """Context manager: resize viewport to content height so every element is
    on-screen and clip screenshots work (Streamlit scrolls inside stMain)."""
    def __init__(self, pg, max_h=16000):
        self.pg = pg; self.max_h = max_h
    def __enter__(self):
        hm, hs = content_height(self.pg)
        h = int(min(max(max(hm, hs) + 40, 1000), self.max_h))
        self.pg.set_viewport_size({"width": 1600, "height": h}); time.sleep(1.5)
        hm2, hs2 = content_height(self.pg)
        h2 = int(min(max(max(hm2, hs2) + 40, 1000), self.max_h))
        if abs(h2 - h) > 30:
            self.pg.set_viewport_size({"width": 1600, "height": h2}); time.sleep(1.5)
        return self
    def __exit__(self, *a):
        self.pg.set_viewport_size({"width": 1600, "height": 1000}); time.sleep(1.0)

def main_h3(pg, text):
    return pg.locator('[data-testid="stMain"] h3', has_text=text).first

def bbox(loc):
    return loc.bounding_box()

def clip_shot(pg, name, top_loc, bottom_loc=None, bottom_y=None, pad=12, x=None, w=None):
    """Clip from the top of top_loc to the bottom of bottom_loc (or bottom_y).
    Must be called inside Tall()."""
    tb = bbox(top_loc)
    if bottom_loc is not None:
        bb = bbox(bottom_loc); by = bb["y"] + bb["height"]
    else:
        by = bottom_y
    main = bbox(pg.locator('[data-testid="stMain"] [data-testid="stMainBlockContainer"]').first) \
        if pg.locator('[data-testid="stMainBlockContainer"]').count() else bbox(pg.locator('[data-testid="stMain"]').first)
    cx = main["x"] if x is None else x
    cw = main["width"] if w is None else w
    clip = {"x": max(cx - pad, 0), "y": max(tb["y"] - pad, 0), "width": cw + 2 * pad, "height": (by - tb["y"]) + 2 * pad}
    path = f"{SHOTS}/{name}"
    pg.screenshot(path=path, clip=clip)
    log(f"clip_shot {name} clip={ {k: int(v) for k, v in clip.items()} } ({os.path.getsize(path)} bytes)")
    return path

def next_h3_after(pg, h3_loc):
    """Return the next h3 in main after h3_loc, or None."""
    y0 = bbox(h3_loc)["y"]
    hs = pg.locator('[data-testid="stMain"] h3')
    best = None; besty = None
    for i in range(hs.count()):
        b = hs.nth(i).bounding_box()
        if b and b["y"] > y0 + 5 and (besty is None or b["y"] < besty):
            best = hs.nth(i); besty = b["y"]
    return best

def main_bottom_y(pg):
    b = bbox(pg.locator('[data-testid="stMainBlockContainer"]').first)
    return b["y"] + b["height"]

# ---- crop-from-one-screenshot helpers (avoid repeated renderer work) -------
from PIL import Image
SCRATCH = "/tmp/claude-0/-home-user-backtest-lp/6729deee-6da0-540c-97ce-3c2bb3ec6cde/scratchpad"
os.makedirs(SCRATCH, exist_ok=True)

def tall_once(pg, max_h=16000):
    hm, hs = content_height(pg)
    h = int(min(max(max(hm, hs) + 40, 1000), max_h))
    cur = pg.viewport_size["height"]
    if abs(cur - h) > 30:
        pg.set_viewport_size({"width": 1600, "height": h}); time.sleep(2.0)
    return h

def page_png(pg, tmpname):
    path = f"{SCRATCH}/{tmpname}"
    pg.screenshot(path=path, full_page=False)
    return path

def main_x_range(pg):
    b = bbox(pg.locator('[data-testid="stMainBlockContainer"]').first)
    return b["x"], b["width"]

def crop(src, name, y0, y1, pg=None, x0=None, w=None, pad=12):
    im = Image.open(src)
    if x0 is None or w is None:
        x0, w = main_x_range(pg)
    box = (int(max(x0 - pad, 0)), int(max(y0 - pad, 0)), int(min(x0 + w + pad, im.width)), int(min(y1 + pad, im.height)))
    out = f"{SHOTS}/{name}"
    im.crop(box).save(out)
    log(f"crop {name} box={box} ({os.path.getsize(out)} bytes)")
    return out
