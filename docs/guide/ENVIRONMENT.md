# ENVIRONMENT

Phase 0 record. Every line here was produced by a command run in this session on 2026-09-06 (UTC).
Raw outputs: `docs/guide/raw/selftest.txt`, `docs/guide/raw/selftest.json`, `docs/guide/raw/schema_probe.json`.

## Repository

| Item | Value |
|---|---|
| `git rev-parse HEAD` | `e1bb6e786aef0ba025b260b288943c4b01c04d1a` |
| `git remote -v` | `origin https://github.com/noemonf1/backtest-lp` (fetch and push) |
| `git status --porcelain \| wc -l` | 0 |
| Action taken | None. HEAD was already the deployed commit. No fetch from upstream was needed. |
| Branch | `claude/tradfi-guide-crypto-lp-9xyb62`. The task text names `guide/tradfi-real-data`; this session is pinned by its harness to the branch above and may not push elsewhere. All work is on that branch. |

## File hashes at HEAD (sha256sum)

| File | sha256 | Matches known fact 1 |
|---|---|---|
| `app.py` | `d91f39fe9d40c00b2f164db4c324d97734492e1335661b03197a32d41d4eae87` | yes |
| `backtest.py` | `7c73f16897d204fb3e98be34d23117b78f2b0222a324c573429591265a8a2481` | yes |
| `run_claude.py` | `f9de629088fdca918a9414af45e942c5b5a200668062e43e822e15bca3b9595f` | yes |
| `run_kimi.py` | `9293f2e937ec8f7114a76bb025afd87d34b1024b53c63a756b1256be1fdfebea` | yes |

## Python and packages

`python3 --version`: Python 3.11.15 (`.python-version` in the repo says 3.13; the container ships 3.11). 4 CPUs, 15 GB RAM.

Install line that works today:

```
pip install "streamlit==1.61.1" "plotly==6.9.0" "pyarrow==24.0.0" "pandas==3.0.2" "numpy==2.4.4" pytest requests matplotlib python-docx playwright
```

Installed versions (printed by `import x; x.__version__`):

| Package | Version | Note |
|---|---|---|
| streamlit | 1.61.1 | as pinned |
| plotly | 6.9.0 | as pinned |
| pyarrow | 24.0.0 | as pinned |
| pandas | 3.0.2 | `requirements.txt` pins `3.0.5`, which does not exist on PyPI |
| numpy | 2.4.4 | `requirements.txt` pins `2.5.1`, which does not exist on PyPI |
| requests | 2.33.1 | `requirements.txt` pins `2.34.2`; 2.33.1 was already present and satisfies every call in the code |
| pytest | 9.1.1 | |
| matplotlib | 3.11.1 | |
| python-docx | installed | used for GUIDE.docx |
| playwright (pip) | 1.62.0 | drives the pre-installed Chromium |
| pandoc | not installed (`which pandoc` printed nothing) | GUIDE.docx is built with python-docx |

## Tests

`python3 -m pytest tests/ -q`: **39 passed in 8.02s**.

## Network (status codes, from the shell)

| Request | Result |
|---|---|
| HEAD `https://data.binance.vision/data/futures/um/monthly/klines/ETHUSDT/1m/ETHUSDT-1m-2026-08.zip` | 200 |
| HEAD `https://data.binance.vision/data/futures/um/monthly/fundingRate/ETHUSDT/ETHUSDT-fundingRate-2026-08.zip` | 200 |
| GET `https://api.binance.com/api/v3/ping` | 451 |
| GET `https://fapi.binance.com/fapi/v1/ping` | 451 |
| GET `https://backtest-lp-production.up.railway.app/_stcore/health` | 200, body `ok` |
| POST `{_meta{block{number}}}` to `https://api.thegraph.com/subgraphs/name/uniswap/uniswap-v3` (legacy) | 301 Moved Permanently in 206 ms (curl, no redirect follow). With redirect follow the target `error.thegraph.com` is refused by the egress proxy (CONNECT 403). From Python `requests` (which follows redirects) the call raises `ProxyError` after 732 ms. |
| POST the same to the gateway `https://gateway.thegraph.com/api/<key>/subgraphs/id/5zvR82QoaXYFyDEKLZ9t6v9adgnptxYpKpSbxtgVENFV` | 200, `_meta.block.number` = 25918130, 227 ms |
| `THEGRAPH_API_KEY` length | 32 |
| `backtest._query_uniswap_subgraph("{_meta{block{number}}}", {})` end to end (legacy attempt then gateway) | returned block 25918133 in 1261 ms |

The 451 codes are Binance refusing US IP addresses on its live trading API. They block only the `hourly` engine's "Live fetch" mode (`kimi/uniswap_delta_hedge_backtest.py`, `BinanceDataFetcher.BASE_URL`). Monthly archives on `data.binance.vision` work.

Because the legacy endpoint fails in under 3 seconds, `_query_uniswap_subgraph` was **not** patched (Phase 1 item 1 condition not met).

## Subgraph schema probe (gateway)

Query: `{ swaps(first: 1, where: {pool: "0x88e6a0c2ddd26feeb64f039a2c41296fcb3f5640"}) { timestamp liquidity sqrtPriceX96 tick amount0 amount1 logIndex transaction { blockNumber } } }`

Response, verbatim (`docs/guide/raw/schema_probe.json`):

```
{"errors":[{"locations":[{"column":92,"line":1}],"message":"Type `Swap` has no field `liquidity`"}]}
```

Consequence: `backtest.fetch_pool_swaps` (backtest.py:708) requests `liquidity` on every `Swap` and therefore fails against the live subgraph. Confirmed by calling it directly for a one-minute window:

```
Uniswap subgraph unreachable (RuntimeError([{'locations': [{'column': 9, 'line': 20}], 'message': 'Type `Swap` has no field `liquidity`'}])). Set THEGRAPH_API_KEY in your env to use the gateway.
```

The app's sidebar button `Fetch swap events` calls this function (app.py:329) and shows that error. The fallback used for the guide is in DATA.md.

## Headless browser

- `which chromium chromium-browser google-chrome`: none on PATH.
- `$PLAYWRIGHT_BROWSERS_PATH` = `/opt/pw-browsers`, containing `chromium`, `chromium-1194`, `chromium_headless_shell-1194`.
- `pip install playwright` gave 1.62.0. Its default launch looks for `chromium_headless_shell-1234`, which is absent, so every launch uses `executable_path="/opt/pw-browsers/chromium"`.
- Outbound HTTPS from this container goes through a TLS-re-terminating proxy. Chromium's default TLS 1.3 ClientHello was reset by that proxy (`net::ERR_CONNECTION_RESET`; proxy log: tunnel closed after 6 s with 1779 B sent, 39 B received). The launch arguments that work are `--proxy-server=$HTTPS_PROXY --ssl-version-max=tls1.2`. Local `http://127.0.0.1:8501` needs no proxy.
- Proof: `docs/guide/screenshots/00_live_home.png`, 1600×1000, taken after `wait_until="networkidle"` plus 5 s. Page title `ETH/USDC Hedged LP Backtest`. Time spent on the browser: 9 minutes.

## Local app

`streamlit run app.py --server.headless true --server.port 8501` started in the background (log `docs/guide/raw/streamlit_local.log`). `curl http://127.0.0.1:8501/_stcore/health` returned `ok`.

## Engine self-test (known fact 9)

`bt.make_demo_tape(days=25, s0=3000.0, vol_annual=0.65, seed=7)` then `bt.run_swap_level(bt.Assumptions(), swaps, cex, funding)`:

| Field | Expected | Got |
|---|---|---|
| `net_usd` | 18308 | 18308 |
| `fees_usd` | 68532 | 68532 |
| `lvr_usd` | 46454 | 46454 |
| `n_hedge_trades` | 297 | 297 |
| swaps in tape | 173718 | 173718 |

**SELFTEST PASS** (3.2 s). The code is the deployed code. This check does not appear in the guide.

## Disk

`df -h .` at Phase 1 start: 30 GB available, so the aggTrades download was attempted (result in DATA.md).
