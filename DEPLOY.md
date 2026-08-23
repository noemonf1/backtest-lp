# Deploying to Railway

The app is set up to deploy on Railway using Nixpacks (Railway's default
Python builder). No Dockerfile required.

## What's in this repo for deploy

| File | Purpose |
|---|---|
| `requirements.txt` | Pinned Python dependencies (streamlit, pandas, plotly, requests, pyarrow, numpy) |
| `.python-version` | Nixpacks reads this to pick Python 3.13 |
| `nixpacks.toml` | Explicit build/install/start plan (redundant with Procfile, kept for clarity in build logs) |
| `Procfile` | Start command — binds Streamlit to `$PORT` on `0.0.0.0` |
| `railway.toml` | Healthcheck config + restart policy |
| `.streamlit/config.toml` | Production Streamlit settings (telemetry off, 5 MB upload cap) |
| `.env.example` | Template for environment variables — copy to `.env` locally |
| `.gitignore` / `.dockerignore` | Keep `data/`, `.env`, `.venv/`, caches, and large artefacts out of the deploy context |

## First-time setup

### Option A — via GitHub

1. `git init && git add . && git commit -m "initial commit"`
2. Push to a GitHub repo.
3. In Railway: **New Project → Deploy from GitHub** → pick the repo.
4. Add environment variables (see below) in the **Variables** tab.
5. Deploy — Railway will build with Nixpacks and start Streamlit.

### Option B — via Railway CLI

```bash
npm install -g @railway/cli   # or: brew install railway
railway login
railway init                   # creates a new project
railway up                     # uploads current directory + builds
```

Then set env vars either via `railway variables set KEY=VALUE` or the dashboard.

## Environment variables

Set these in the Railway dashboard under **Variables**:

- **`THEGRAPH_API_KEY`** — optional. Free key at
  [thegraph.com/studio](https://thegraph.com/studio/). Needed only if you
  want the "Fetch swap events" button to work. Without it, users can still
  run the hourly engine and use auto-fetched Binance data.

Railway auto-injects `PORT` — the Procfile already reads it.

## Data persistence

**We chose "fetch on demand, no persistence"** — the `data/` directory is
excluded from the deploy image and lives on the container's ephemeral
filesystem. That means:

- **Every restart wipes cached klines and swap parquets.** Users have to
  re-download from the sidebar's **Fetch data** expander.
- Binance klines fetches are cheap (~5 MB/month, seconds to download).
- Swap-event fetches from the subgraph take **many minutes per month** and
  will need to be re-run after each redeploy.

If you outgrow this — attach a Railway volume:

```toml
# railway.toml addition
[[deploy.volumes]]
mountPath = "/app/data"
name = "backtest-data"
```

Then create the volume in the dashboard and it'll survive restarts.

## Resource sizing

The app is memory-hungry when handling large swap parquets:

- Baseline (idle): ~200 MB RAM.
- Running the swap_level engine on one month of ETH/USDC swaps
  (~2-3 M rows): expect 1-2 GB peak.
- Sweep mode multiplies runtime by grid size but not much peak memory.

Railway's default 512 MB / 1 vCPU is enough for the hourly engine and
demo mode. For real swap-level backtests, upgrade to at least the 2 GB /
2 vCPU tier under **Settings → Resources**.

## Healthchecks & restarts

Streamlit exposes `/_stcore/health` (200 OK once ready). Railway's
healthcheck hits it with a 60 s timeout after each deploy. Restarts are
capped at 3 retries — if the app crashlooped (bad env var, missing
dependency), you'll see it in the deploy logs quickly.

## Local sanity check before deploying

```bash
# In a clean venv
python -m venv /tmp/deploy-check
/tmp/deploy-check/bin/pip install -r requirements.txt
/tmp/deploy-check/bin/streamlit run app.py --server.port 8888
```

If that boots cleanly locally, it'll boot on Railway.

## Common gotchas

- **Build fails with `pip: command not found`** — Nixpacks' base
  `python313` package ships the interpreter without `pip` on `$PATH`.
  Fixed in `nixpacks.toml` by (a) adding `python313Packages.pip` to
  `nixPkgs` and (b) installing into an explicit venv at `/opt/venv`.
  The Procfile / start command then invokes `/opt/venv/bin/streamlit`
  directly.
- **Build fails at pyarrow install** — some Nixpacks images ship without
  a C compiler; if that happens, add `gcc` to `nixPkgs` in
  `nixpacks.toml` (already done).
- **App loads then dies immediately** — usually a missing env var
  referenced at module-import time. `THEGRAPH_API_KEY` is only checked
  when the fetch button is clicked, so this shouldn't happen with our
  code, but double-check any additions.
- **CORS/XSRF blocking widgets** — the Procfile disables both because
  Railway serves the app on its own HTTPS domain. Only re-enable if you
  put the app behind a custom auth proxy.
