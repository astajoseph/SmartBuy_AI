# Deployment

## Pieces to run

| Piece | Command | Needs |
|---|---|---|
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | `DATABASE_URL`, `GEMINI_API_KEY`, `ADMIN_API_TOKEN`, `CORS_ORIGINS` |
| Collector | `python -m app.workers.scheduler --once` (cron) or `--loop 300` (persistent) | `DATABASE_URL`, Playwright Chromium |
| Web | `next build && next start` (or the Docker image) | `NEXT_PUBLIC_API_URL` at **build** time |

Run the migrations once per database: `python scripts/migrate.py`, then
`python scripts/register_products.py --events`.

## Docker

```bash
cd SmartBuy_AI
docker compose up --build        # api (127.0.0.1:8001), web (127.0.0.1:3100), worker (--loop 300)
```

`backend/Dockerfile` installs Chromium with system dependencies and runs as a non-root user.
`frontend/Dockerfile` builds a standalone Next.js server. `.env` is excluded from build contexts.

## Scheduled collection without your laptop

### Option A — GitHub Actions (free, simplest)

`.github/workflows/smartbuy-collect.yml` runs `scheduler --once --grace-minutes 30` every 3 hours
(cron `17 */3 * * *`, UTC). The grace window collects a source that becomes due within 30 minutes, so a run that
fires slightly early does not skip a cycle. Budget: ~3 min per run × 8 runs/day ≈ 720 of the 2,000 free
Actions minutes per month on a private repository. pricehistory.app itself updates about once a day, so more
frequent collection would add load without adding information. Manual run: Actions → smartbuy-collect → Run workflow.

1. Push the repository to GitHub.
2. Repository → Settings → Secrets and variables → Actions → New secret
   `SMARTBUY_DATABASE_URL` = your Supabase session-pooler URI.
3. Actions tab → *smartbuy-collect* → *Run workflow* once to verify.

Limits to know: GitHub cron is best-effort (runs may start late or occasionally be dropped under load);
scheduled workflows in a repository with no activity for 60 days are disabled automatically; each run
reinstalls Chromium (~1–2 min). The collector is idempotent, so late or duplicate runs are harmless,
and `concurrency` prevents overlapping runs.

### Seller discovery workflow

`.github/workflows/smartbuy-discover.yml` runs `python -m app.workers.discovery` every 6 hours (cron `43 */6 * * *`).
It needs the same `SMARTBUY_DATABASE_URL` secret, plus **`BRAVE_SEARCH_API_KEY`** for finding new shops
(without it, only marketplace sub-sellers and already discovered shops are checked). Budget: ~4–6 min per run
× 4 runs/day ≈ 600–700 Actions minutes per month; 2 Brave queries per product per run ≈ 240 per product per month.
Apply migration `20261024000001_seller_discovery.sql` (`python scripts/migrate.py`) before the first run.

The workflow runs with `--no-marketplace`: Amazon and Myntra block GitHub's data-centre addresses, so their seller
lists are read from a home machine instead — `scripts/run_discovery_local.sh` in crontab
(`13 */6 * * * /path/to/SmartBuy_AI/scripts/run_discovery_local.sh`), which uses `--no-search` (no Brave queries).
Sellers are deactivated only after their page has been read again, so the home machine being off removes nobody.

### Option B — persistent worker

Any container host that keeps a process running (Railway, Render background worker, Fly.io machine,
a small VM): deploy `backend/Dockerfile` with command
`python -m app.workers.scheduler --loop 300`. Free tiers of several hosts sleep idle services or limit
monthly hours — check the provider's current terms; a sleeping worker does not collect.

Running both A and B is safe (advisory locks + unique keys).

## Current setup: Vercel (web) + Railway (API) + the owner's PC (daily store check)

| Piece | Where | How it is configured |
|---|---|---|
| Web (Next.js) | Vercel, project root `frontend/` | Framework preset Next.js, no extra config |
| API (FastAPI) | Railway, one service from this repo | `railway.json` (repo root): builds `backend/Dockerfile`, health check `/api/health`; the image listens on `$PORT` |
| History load for new products | GitHub Actions `smartbuy-collect` | unchanged |
| Daily store check, marketplace sellers, sale dates | Owner's PC, Windows task | `scripts/run_daily_local.ps1` (Amazon / Myntra block cloud servers) |

### Railway (API)
1. New project → *Deploy from GitHub repo* → this repository (root directory: repository root, so `railway.json` is used).
2. Region: the closest Railway region to Supabase Seoul (`ap-northeast-2`) is **Southeast Asia (Singapore)**; set it under
   Settings → Deploy → Region.
3. Variables (Settings → Variables):

   | Variable | Value |
   |---|---|
   | `DATABASE_URL` | Supabase **session pooler** URL (port 5432, `*.pooler.supabase.com`); the direct `db.<ref>` host is IPv6-only |
   | `DB_SCHEMA` | `smartbuy` |
   | `ENVIRONMENT` | `production` |
   | `LLM_PROVIDER`, `GEMINI_API_KEY`, `GEMINI_MODEL` | as in `.env` |
   | `SUPABASE_URL` | as in `.env` |
   | `ADMIN_EMAILS` | as in `.env` |
   | `CORS_ORIGINS` | the Vercel production URL, e.g. `https://smartbuy.vercel.app` (comma-separate several) |
   | `CORS_ORIGIN_REGEX` | optional, to allow preview deployments, e.g. `^https://smartbuy-[a-z0-9-]+\.vercel\.app$` |
   | `BRAVE_SEARCH_API_KEY` | optional (Admin → "Search now" / "Run discovery now") |
4. Settings → Networking → *Generate domain*. Check `https://<domain>/api/health`.

### Vercel (web)
1. *Add New → Project* → import this repository → **Root Directory `frontend`** (framework detected as Next.js).
2. Environment variables (Production and Preview), set **before** the first build because Next.js bakes them in:

   | Variable | Value |
   |---|---|
   | `NEXT_PUBLIC_API_URL` | the Railway domain, e.g. `https://smartbuy-api.up.railway.app` (no trailing slash) |
   | `NEXT_PUBLIC_SUPABASE_URL` | same as `SUPABASE_URL` |
   | `NEXT_PUBLIC_SUPABASE_ANON_KEY` | the public anon key (never the service-role key) |
3. Deploy, then put the Vercel URL into Railway's `CORS_ORIGINS` (Railway redeploys on variable changes).

### Supabase Auth
Authentication → URL Configuration: Site URL = the Vercel URL; redirect URLs = `<vercel-url>/login`,
`<vercel-url>/reset-password` (keep the `http://localhost:3100/...` ones for local development).

## Hosting the API and web

* Put the API in the **same region as the Supabase project** (`ap-northeast-2`, Seoul, for the current
  project). From India each query costs ~240 ms; in-region it is a few ms.
* Use the Supabase **session pooler (port 5432)** or a direct connection. The code disables prepared
  statements, so the transaction pooler also works, but session mode is recommended.
* Set `CORS_ORIGINS` to the web app's origin and build the web app with `NEXT_PUBLIC_API_URL` set to the
  API's public URL.
* Terminate TLS at the host / reverse proxy; the API trusts proxy headers (`--proxy-headers`).

## Secrets checklist

* `DATABASE_URL`, `GEMINI_API_KEY`, `ADMIN_API_TOKEN`, `SUPABASE_JWT_SECRET` — backend/worker only.
* Nothing secret is needed by the frontend; `NEXT_PUBLIC_API_URL` is public by design.
* Never commit `.env` (git-ignored) — rotate any credential that was ever pasted into chat or logs.

## Operations

* Health: `GET /api/health`; source health: `GET /api/admin/sources/health` (healthy / degraded / failing /
  stale / never_run); job log: `GET /api/admin/jobs`; cache: `GET /api/admin/cache-status`.
* Logs are JSON lines on stdout with secrets redacted.
* A source with ≥ 3 consecutive failures shows as *failing* in Admin; previously stored prices are kept.
