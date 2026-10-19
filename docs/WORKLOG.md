# SmartBuy AI — Work log

Chronological record of what was built, changed and verified, newest entries at the bottom.
Every future change is appended here (date · what · why · files · tests · open items).

---

## 2026-10-16 · Phase 1 — Inspect and plan

**Found in the repository**
* `AG_AI/` root is an unrelated Gemini CoT/ToT/ReAct assignment (left untouched; its uncommitted edits to
  `cot_agent.py` / `tot_agent.py` were not modified).
* New `data/` folder:
  * `extract_dashboard_timeseries.py` — Playwright tool that reads time series from chart libraries
    (Highcharts, Chart.js, Plotly, ECharts, ApexCharts) and JSON responses into Postgres schema `dashboard_ts`.
  * `schema.sql` — that schema (dashboards, series, points, point_revisions, chart_markers, price_observations, views).
  * `daily_prices.csv` — one row per day for "Price History Chart / Prices", 2023-09-16 → 2026-10-16:
    89 *recorded* rows and ~1,038 *carried forward* rows (₹1,350 → ₹1,050).
* Your Supabase project already contained `dashboard_ts` with data from **pricehistory.app** for
  *Beauty of Joseon Sunscreen SPF 50 (50 ml)*: series "Prices" and "Offer Price" (89 points each) and four
  "Big Billion Days" sale bands.
* Environment: Python 3.12.3, Node 24.20, Docker 29.7, Playwright Chromium (build 1248) already installed.
  Ports 3000 (Grafana) and 8000/5432 (your `~/PBL_2/price-monitor` containers) are in use, so SmartBuy
  uses 3100 / 8001 locally and a separate test Postgres on 54329.

**Decisions**
* Build in a new folder `AG_AI/smartbuy-ai/`; application tables in a separate Postgres schema `smartbuy`
  so they never collide with `dashboard_ts`.
* LLM = Gemini (`GEMINI_API_KEY` reused from the existing `.env`, copied into the git-ignored `smartbuy-ai/.env`).
* Reuse the chart-detection code from `extract_dashboard_timeseries.py` verbatim (vendored) so both tools
  read charts identically.
* Your Supabase connection string was stored only in `smartbuy-ai/.env` (mode 600, git-ignored).

## 2026-10-16 · Source inspection: pricehistory.app

* Static HTML has schema.org `Product` JSON-LD: name, brand, Flipkart product code, offers "Price" (₹1,050)
  and "Offer Price" (₹945), currency INR, availability, retailer `flipkart.com`, and a description stating
  Lowest ₹1,035 / Average ₹1,234 / Highest ₹1,500 / MRP ₹1,500. Visible text: "Lowest Offer ₹945".
* The history chart is Chart.js filled by page JavaScript from `POST /api/price/<id>`.
  **robots.txt disallows `/api/`.** The adapter never calls that endpoint itself; it renders the page in a
  headless browser (as your script did) only when `respect_robots_for_subrequests: false` is set for the
  source. This is set in `config/products.yaml` with an explanatory comment — your decision to keep or change.
* Live test result: 89 selling-price points (2023-09-16 → 2026-10-16), 35 offer-price points, 4 Big Billion
  Days bands, all expected fields found, nothing missing.

## 2026-10-16 · Phase 2–3 — Foundation and price collection

* Migrations `supabase/migrations/20261016000001_init.sql` (all tables, constraints, indexes, views
  `v_price_points`, `v_daily_price`) and `..._rls.sql` (RLS on every table; owner policies on user tables).
* Product registry (`config/products.yaml`, Pydantic-validated), sale-event registry (`config/sale_events.yaml`).
* SSRF-safe fetcher (scheme/port/credential checks, allowlist, DNS answers must be public, IP pinning,
  redirects re-validated, size/time limits, robots.txt), per-domain rate limit, retries with backoff.
* Adapters: `pricehistory_app`, `generic_jsonld`, `chart_page`, `vendor_generic`; registry with `auto`.
* Collector with Postgres advisory locks, `collection_jobs` logging, idempotent inserts, failure isolation.
* Scheduler CLI (`--once` for cron/GitHub Actions, `--loop N` for a persistent worker).
* Applied to Supabase: migrations, registry sync (15 sale events), import of 124 `dashboard_ts` points,
  live collection (+7 new rows: 2 observations, 5 stated statistics; chart points were already present —
  confirms de-duplication).
* Per your request, a daily view `smartbuy.v_daily_price` and `scripts/export_daily.py` give one row per
  IST day with `is_recorded` / `days_since_recorded`; `scripts/import_legacy.py csv` imports only the
  *recorded* rows of a `--export-daily` CSV.

## 2026-10-16 · Phase 4–7 — Analysis, EMI, seasonality, forecasting, recommendations

* History statistics, period changes, percentile vs last year, data-quality indicators.
* Finance: reducing-balance / flat / zero-interest EMI, down payment, processing fee + GST (18 % default,
  labelled), cashback, no-double-counting of principal, No-Cost-EMI verification, implied APR, tenure trade-off.
* On-demand vendor service with TTL cache, negative cache after failures, stale-cache labelling, offer
  validity enforcement; EMI comparison by objective with max-monthly-EMI exclusion; lexicon aspect sentiment
  with product vs seller separation.
* Baseline forecasters (naive, rolling median, EWMA, damped trend) chosen by rolling-origin backtest;
  must beat naive by ≥ 2 % MAE; empirical 80 % interval with hold-out coverage; empirical target probability
  rounded to 5 % with Brier-score validation; outcome scoring.
* Deterministic buy/wait policy (`recommendations/policies.py`).

**Correction made after inspecting real output:** the first run carried ₹1,350 forward across the
777-day gap in the source history, making 92 % of "daily" prices assumed. That inflated a "Big Billion
Days unusually large drop" flag, produced a spurious monthly-seasonality result (p = 0.003) and HIGH
confidence. Fixes: carry-forward capped at 30 days (beyond → unknown), backtests skip unknown days,
duplicate event records merged, events without end dates skipped, windows need ≥ 2 recorded prices,
month effects need data from ≥ 2 years per month, and confidence drops when the probability method
shows no skill vs a base-rate guess (it currently does not: Brier 0.203 vs 0.194 at 7 days).

## 2026-10-16 · Phase 8 — Gemini assistant

* Provider abstraction (`gemini` | `none`), deterministic intent parsing (product, EMI/vendor, reviews,
  target, deadline, monthly limit), evidence building, untrusted-text fencing, numeric guard, template fallback.
* Live check: "buy now or wait for < ₹1,000 within 3 weeks?" → `BUY_IF_TARGET_REACHED`, vendor sites not
  contacted, guard passed. Two presentation issues found and fixed: the probability-weighted saving was
  described as a guaranteed saving, and an exact "45 %" was quoted — the evidence now carries
  `savings_explanation` and the prompt requires band wording.

## 2026-10-16 · Phase 9 — Frontend, API polish, deployment

* Next.js 15 pages: Dashboard, Product detail, Purchase advisor (form + chat), Vendors & EMI, Reviews,
  Sale intelligence, Settings (browser-local preferences), Admin (token-gated).
* Every figure is tagged Observed / Source-stated / Estimate / Forecast / Unknown / Vendor page.
* Chart shows recorded points with original dates, steps between records, and **breaks across gaps
  longer than 30 days** (initial version drew a misleading flat line across the 777-day gap — fixed).
* Performance: Supabase is in `ap-northeast-2` (~240 ms per round trip from here). Pool switched to
  autocommit (removes a ROLLBACK round trip per query) → endpoints went from ~2.5 s to ~0.9 s. Deploying
  the API in the same region as the database removes most of the remaining latency.
* Security fix found by tests: registering a URL with a literal private IP was accepted (it would still have
  been blocked at fetch time); now rejected at validation.
* Dockerfiles, docker-compose, `.env.example`; GitHub Actions: `smartbuy-collect.yml` (cron every 6 h) and
  `smartbuy-tests.yml` (backend tests with Postgres service + frontend build), placed in `AG_AI/.github/workflows/`.

**Tests:** 134 backend tests pass (extraction, EMI, database, vendor on-demand behaviour, security,
forecasting, seasonality, recommendations, LLM guardrails, API). Frontend type-check and production build pass.

**Open items / waiting on you**
1. URLs for products 2 and 3, and vendor URLs (EMI / reviews) for all products.
2. Choose whether to keep `respect_robots_for_subrequests: false` for pricehistory.app.
3. Set `ADMIN_API_TOKEN` in `.env` to enable the admin API/page.
4. Add the GitHub secret `SMARTBUY_DATABASE_URL` and push, to enable cloud collection.
5. Optional: Supabase Auth sign-in UI (the backend already verifies Supabase JWTs for saved target prices).
6. Rotate the Supabase database password at some point — it was shared in chat.

## 2026-10-16 · Verification of deployment artefacts

* `docker build` of `backend/Dockerfile` (with Chromium) and `frontend/Dockerfile` both succeed; the API image
  imports the app and all four adapters.
* `python -m app.workers.scheduler --once` against Supabase exits 0 (nothing due yet — product 1 was collected
  less than 6 h earlier).
* `scripts/export_daily.py` reproduces the original `data/daily_prices.csv` row counts exactly
  (1,127 days: 89 recorded, 1,038 carried forward), now with explicit labels.
* Not yet verified: the GitHub Actions workflows (they need the repo pushed and the secret set).

## 2026-10-16 · Product 1 replaced: Olay Total Effects Day Cream (Amazon)

**Why:** the Beauty of Joseon source history has a 777-day hole (4 Feb 2024 → 22 Mar 2026). Running
`extract_dashboard_timeseries.py --export-daily` confirmed all 776 days in between are *carried forward*
copies of ₹1,350, not observations, and the source's 1Y/6M chart views contain no extra points. You chose to
switch to a product with continuous history.

**Source check first** (`scripts/test_source.py`): https://pricehistory.app/p/olay-total-effects-day-cream-vitamin-b5-u4L6fhce
→ 1,110 selling-price points (Nov 2016 → Oct 2026), 30 offer-price points, 9 Prime Day + 11 Great Indian Festival
bands, retailer amazon.in, ASIN B00ENZT4M8, stated lowest ₹288 / average ₹642 / highest ₹5,723 / MRP ₹999.

**Changes**
* Adapter fix: Amazon products on pricehistory.app publish one *unnamed* JSON-LD offer (the selling price);
  it was being skipped. `offer_price` is now optional (only listed while an offer exists). Test added.
* `config/products.yaml`: `olay-total-effects-50g` added; `boj-sunscreen-50ml` set `enabled: false`
  (collection stopped, hidden from the dashboard, data kept).
* Collected live into Supabase: 1,145 records, no missing fields.
* Bug fix: the earlier `dashboard_ts` import stored 4 Big Billion Days bands with retailer `any` (retailer was
  unknown at import time), so a Flipkart sale was applied to this Amazon product. Bands without a retailer are no
  longer stored; the 4 wrong duplicate rows (ids 16–19) were deleted — correct `flipkart` copies remain. Test added.
* "Unusually large sale drop" now compares the *median* event change with non-sale windows (one odd 2017 window
  had skewed the mean to +2.6 % vs a median of −3.8 %).
* Stale offer price: the last offer (₹489, 20 Jul 2026) was being shown as the current offer. Offers older than
  2 days relative to the latest selling price are now reported as "last seen", not current. Test added.
* Chart: y-scale uses the 1st–99th percentile and discloses the 5 off-scale records (max ₹5,723); only sale
  events relevant to the product's retailer are shaded (new `relevant_events` in the seasonality API); labels moved
  under the chart.
* Removed a numpy "Mean of empty slice" warning for baseline windows inside unknown periods.

**Results now:** current ₹449 (cheaper than today on only 5 % of last year's days) → recommendation BUY_NOW
(medium confidence). Great Indian Festival: price fell in 7/9 past sales, median −3.8 % (typical ₹47 saving at the
low), not unusual vs non-sale periods. Prime Day: no typical change. Month effect test is significant (p = 0.005)
but small (Oct median −1.9 %, Sep +1.8 % vs local level). Target-probability method still shows no backtest skill.

**Tests:** 137 passed. Frontend type-check and build pass; product page re-checked in a headless browser.

## 2026-10-16 · Vendors for product 1, EMI/offers, GitHub automation

**Vendor URLs received** (tracking parameters removed before registering):
Amazon.in `/dp/B00ENZT4M8`, Myntra `/…/13509836/buy`, Nykaa Man `/…/p/261613?skuId=261613`.

**Inspection** (robots.txt checked; product paths allowed): the generic JSON-LD adapter only worked for Nykaa Man,
so three site-specific adapters were written from the real page structure (`extraction/adapters/marketplaces.py`):
`amazon_in` (element ids / data-hook attributes), `myntra` (`window.__myx` JSON), `nykaa` (`__PRELOADED_STATE__` JSON).

| | Amazon.in | Myntra | Nykaa Man |
|---|---|---|---|
| Price / MRP | ₹449 / ₹999 | ₹669 / ₹999 | ₹799 / ₹999 |
| EMI | not shown | **not offered** (`emiEnabled: false`) | not shown |
| Offers | ICICI cashback (up to ₹13), SBI (up to ₹3,000, terms in pop-up), multi-buy | HDFC 10 % (min spend ₹3,500 → not applicable), Flipkart Axis/SBI 7.5 % cashback (≈ ₹50) | none |
| Rating | 4.3 (12,614) | 4.58 (11,415) | 4.4 (11,694) |
| Returns | — | exchange only, 14 days | defects/damage only, 15 days |

**New:** migration `20261016000003_vendor_offers.sql` (`vendor_bank_offers`; `vendor_snapshots.emi_status`, `details`),
bank-offer parser with saving estimate only when terms allow, EMI status per vendor and an "EMI summary", MRP /
discount / stock / discount-end / return policy in the comparison, verified-purchase flag on reviews; Vendors page shows
all of it. 11 new tests (synthetic fixtures shaped like the real pages).

**Live check:** Amazon and Myntra fetched on demand and then served from cache on the next call (no re-fetch). Nykaa Man
worked during inspection, then returned `403 Access Denied` to all requests from this network (CDN IP block) — recorded
as unavailable, negative-cached, not bypassed.

**Automation:** collection schedule changed to every 3 hours (`--grace-minutes 30`), product interval 180 min.
Code committed and pushed to `github.com/astajoseph/AG_AI` (main); secret `SMARTBUY_DATABASE_URL` set; workflow
triggered manually to verify (result recorded below).

**Tests:** 148 passed.

## 2026-10-16 · Fixes found during end-to-end verification

* **Bug (HTTP 422):** vendor-comparison, reviews and assistant-chat endpoints rejected every request: with
  `from __future__ import annotations`, FastAPI could not resolve `Request` on the rate-limiter instance and treated
  it as a required query parameter. Fixed in `app/dependencies.py`; HTTP-level regression tests added (incl. 429 check).
* **Gemini answers were cut off:** gemini-2.5-flash "thinking" tokens count against the output limit. Thinking is now
  disabled (the backend does the reasoning), the limit raised to 2,048 tokens, and a truncated answer
  (`finish_reason = MAX_TOKENS`) falls back to the template instead of being shown half-finished.
* Prompt rule added: name every vendor and say when one is unavailable/stale.
* Workflow: manual runs accept an optional `product` input to collect a product immediately.
* Cloud check: first manual `smartbuy-collect` run succeeded and connected to Supabase (nothing was due);
  `smartbuy-tests` CI passed on GitHub.

**Tests:** 150 passed locally.
* **Cloud collection verified:** `smartbuy-collect` run 38822275194 (manual, `product=olay-total-effects-50g`) ran on
  GitHub's servers, rendered pricehistory.app, extracted 1,145 records and inserted 5 new rows into Supabase
  (job 6: new ₹449 observation + stated stats; history already present → de-duplicated). `smartbuy-tests` CI passed.
  From now on the cron schedule (every 3 h) runs without any local machine.

## 2026-10-16 · Project overview document

* Added `docs/SmartBuy_AI_Project_Overview.txt`: project brief, architecture, end-to-end workflow, methodology
  (extraction, gap handling, statistics, forecasting, probabilities, seasonality, EMI/offers, reviews, policy,
  AI safeguards, caching), database summary, software and hardware requirements (sizes measured on the dev
  machine), run instructions, testing and known limitations.

## 2026-10-17 · Architecture diagram

* Added `docs/SmartBuy_AI_Architecture.png` (3920 × 2520 px) and its editable source
  `docs/SmartBuy_AI_Architecture.html` (render with headless Chromium at 1960 × 1260, scale 2).

## 2026-10-19 · Presentation added

* Added the project presentation `docs/smartbuy_ai.pptx` to the repository.
* Ignored LibreOffice/Office lock files (`.~lock.*#`, `~$*`).

## 2026-10-19 · Repository cleanup and progress tracker

* Removed `docs/smartbuy_ai.pptx` and `docs/SmartBuy_AI_Project_Overview.txt` from the repository (both kept
  locally); `.gitignore` now excludes `*.ppt`, `*.pptx`, `*.odp` and `docs/*.txt`.
* README: new *Progress tracker* (done / current status / pending), architecture diagram embedded, outdated
  details corrected (schedule every 3 h, 150 tests).
* Checked GitHub Actions: all 18 `smartbuy-collect` runs so far succeeded (latest 2026-10-19 13:33 UTC).

## 2026-10-20 · Moved to its own repository: github.com/astajoseph/SmartBuy_AI

* All files of `AG_AI/smartbuy-ai/` now live at the root of the new public repository `SmartBuy_AI`
  (fresh history; the earlier history stays in `AG_AI`). Presentations, the hand-out text and `.env` were not copied.
* GitHub Actions moved to `.github/workflows/` here (`smartbuy-collect` every 3 h, `smartbuy-tests` CI); paths changed
  from `smartbuy-ai/backend` → `backend`, `smartbuy-ai/frontend` → `frontend`. The repository secret
  `SMARTBUY_DATABASE_URL` must be set here as well for scheduled collection.
* Path references updated in README, deployment guide, `.env.example`, `config.py`, `scripts/_bootstrap.py`; architecture
  diagram footer now shows the new repository (PNG re-rendered).
* Note: entries above mention `AG_AI/smartbuy-ai/…` and `data/extract_dashboard_timeseries.py` — those refer to the
  original location and to the separate extractor tool in `AG_AI/data/`.
* Completed the move: `smartbuy-ai/` and the two SmartBuy workflows were removed from `AG_AI` (commit 65049c5), so
  collection now runs only from this repository. Local working copy: `/home/asta/SmartBuy_AI` (Python venv and
  node_modules moved here; 150 tests pass). The local presentation and overview text file were deleted on request.
* **Pending:** set the secret `SMARTBUY_DATABASE_URL` on this repository — until then the scheduled collection
  here cannot reach Supabase.
