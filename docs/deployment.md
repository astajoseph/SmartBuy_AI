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

### Option B — persistent worker

Any container host that keeps a process running (Railway, Render background worker, Fly.io machine,
a small VM): deploy `backend/Dockerfile` with command
`python -m app.workers.scheduler --loop 300`. Free tiers of several hosts sleep idle services or limit
monthly hours — check the provider's current terms; a sleeping worker does not collect.

Running both A and B is safe (advisory locks + unique keys).

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
