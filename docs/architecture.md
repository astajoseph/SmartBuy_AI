# Architecture

## Components

| Component | Tech | Responsibility |
|---|---|---|
| Web app | Next.js 15, TypeScript, Tailwind, Recharts | Dashboard, product detail, advisor + chat, vendors/EMI, reviews, sales, settings, admin. Talks only to the public API URL. |
| API | FastAPI, Pydantic | Read endpoints, recommendation and chat endpoints, admin endpoints (bearer token). OpenAPI at `/docs`. |
| Collector worker | `app.workers.scheduler` | Scheduled price-history collection, forecast refresh, forecast scoring, alert evaluation. |
| Discovery worker | `app.workers.discovery` | Every 6 h: marketplace sub-sellers, re-check of discovered shops, web search for new shops, 30-day deactivation. |
| Database | Supabase PostgreSQL, schema `smartbuy` | All state. RLS on every table. |
| LLM | Gemini via `google-genai` (swappable) | Explains structured results. Never decides. |

## Separation of the two data paths

| | Price-history path | Vendor path |
|---|---|---|
| Trigger | schedule (`due_price_sources`) or admin "collect now" | a user asking for EMI / vendor / review analysis |
| Code | `workers/price_collector.py` | `services/vendor_service.py` |
| Tables read for URLs | `product_sources` (`source_type = 'price_history'`) | `product_vendor_sources` |
| Caching | interval per source (default 6 h, adapted — see below) | TTL per data kind + negative cache after failures |
| Guarantee | never reads `product_vendor_sources` | only called from vendor/recommendation/assistant routes when requested |

Both are enforced by tests in `backend/tests/test_vendor_on_demand.py`.

### Third path: seller discovery (`services/discovery_service.py`, every 6 hours)

The price-history collector still never touches vendor pages. A separate worker does, on a fixed schedule:

1. **Marketplace sub-sellers.** For each registered vendor page whose adapter can list sellers
   (`amazon_in`: the rendered "Other sellers" panel; `myntra`: `pdpData.sellers`), every seller and its offer
   is stored (`vendor_sellers`, `vendor_seller_offers`).
2. **Discovered shops** (`product_vendor_sources.origin` = `web_search` / `admin_approved`) are re-fetched through
   `vendor_service.refresh_vendor_source`, which also refreshes their cached snapshot.
3. **Web search** (Brave, only with `BRAVE_SEARCH_API_KEY`): two queries per product; non-shops and hosts already
   tracked are skipped; at most 8 new pages per product per run are fetched with an allowlist of just that host
   and checked by `services/product_match.py`:
   * **strong** (registered product code on the page, or brand + pack size + ≥ 60 % of the name words + a price
     between 60 % of the lowest seen and 150 % of the MRP) and in stock → added as a vendor source automatically;
   * **partial** (nothing contradicts it, proof incomplete) → `pending` in the admin console. When the missing
     piece is the pack size, up to 3 product images listed in the page's structured data are read with an
     open-source OCR model (`extraction/image_text.py`, RapidOCR / PaddleOCR models in ONNX, CPU) and the match is
     decided again with the size printed on the pack — a different size rejects the page;
   * **none** (other size / brand / product, implausible price, no product data) → `rejected`, not re-fetched for 14 days.
4. **Lifecycle.** Each check that sees a seller / shop in stock moves `last_in_stock_at`. Discovered sellers and
   shops not in stock for `SELLER_INACTIVE_DAYS` (30) are deactivated — never deleted — and re-activated when seen
   in stock again. Registry vendor pages (`config/products.yaml`) are never deactivated automatically.

### Review intelligence (`services/review_service.py`, `services/review_integrity.py`)

* **Sentiment:** VADER (rule-based, MIT) with product words added ("greasy", "breakout", "counterfeit" …), per
  sentence; aspect themes (texture, skin reaction, delivery, value …) from the same scores.
* **Sample:** every fetch of a vendor page is pooled and re-fetched copies are merged (vendor review id, else
  text + rating + date), so the sample grows over time.
* **Integrity per review** (score 0–1, reasons shown): free/discounted-product reviews (vendor badge such as Amazon
  Vine, or wording), copied text (same page or another site), not a verified purchase where the vendor shows
  verification, short generic praise, stars contradicting the text, bursts of high ratings (samples ≥ 8),
  repeat named reviewers; boosts for helpful votes, photos, detailed text. Reviews of another pack size are not counted.
* **Per vendor:** integrity score and label (organic ≥ 75 / mixed ≥ 50 / doubtful), confidence (sample size, hand-picked
  pages), trust-weighted rating and sentiment beside the raw ones, star-distribution shape (polarized 5★/1★), page
  rating vs. genuine-sample rating, and **rating growth** from the stored rating-count history (ratings/day, spikes,
  rating jumps without new ratings). Themes and quotes come from reviews that are not suspicious.
* **Sub-seller reputation** (`GET /api/products/{id}/seller-reputation`): growth of each Amazon sub-seller's own rating
  count and % positive across the 6-hourly discovery checks — steady growth vs. spikes / falling % positive.
* The LLM receives the integrity block as evidence and must call flagged reviews "suspicious", never "fake".

Discovered shops join the normal vendor comparison (they are ordinary active vendor sources); marketplace
sub-sellers are shown separately (`GET /api/products/{id}/sellers`, database only).

## Request flow: "Should I buy?"

1. `POST /api/assistant/chat` → `llm/assistant.parse_question` identifies product and intent with
   deterministic rules (no LLM needed to decide what data to fetch).
2. `recommendations/engine.recommend`:
   * price points from Supabase → `services/price_service` (stats + data quality)
   * `forecasting/forecasting.build_forecast` (validated baseline, interval, probability)
   * `forecasting/seasonality` (event windows, upcoming events with verification status)
   * vendor data only if the intent needs it (`vendor_service.get_vendor_data`, cache first)
   * `recommendations/policies.decide` → action, confidence, reasons, risks
   * result stored in `purchase_recommendations` (+ forecast in `forecasts`)
3. `llm/prompts.build_user_prompt` puts trusted evidence and untrusted web text in separate fenced
   blocks; Gemini writes the explanation; `check_numbers` rejects answers containing ₹ amounts that are
   not in the evidence (falls back to a template).

## Key design decisions

* **Deterministic decisions, LLM explanations.** All money maths and the action come from tested code.
* **Never fabricate.** Missing values stay `None`/unknown; gaps > 30 days are unknown; chart nulls are
  skipped; carried-forward days are flagged; source-stated stats are labelled as such.
* **Idempotent writes.** Unique keys + `ON CONFLICT DO NOTHING`; advisory locks per source; a failed
  run never deletes or overwrites stored prices (prices have `CHECK (price > 0)`).
* **Pluggable adapters.** New sites = new adapter class + registry entry; configuration per source in
  `adapter_config` JSON.
* **Provider abstraction.** `LLM_PROVIDER=none` runs the whole product without any LLM.

## Security summary

* Outbound fetching: see `docs/extraction.md` → Security.
* Secrets only in environment variables; logs pass through a redaction filter; the frontend contains
  no server secrets (test-enforced).
* Admin API requires `ADMIN_API_TOKEN` (constant-time compare) and is disabled when unset.
* Ordinary users cannot trigger unrestricted scraping: vendor-triggering routes are rate limited per
  client and can only touch registered vendor URLs; cache TTLs bound the request rate per vendor.
* Web-search results are untrusted: a candidate page is fetched with an allowlist of only its own host
  (robots.txt, public-address and size checks as for every fetch); only verified or admin-approved pages
  join the allowlist of registered hosts. Discovery runs only from the worker and the admin API.
* The assistant is read-only: it has no tools that write, fetch arbitrary URLs or transact.


## Adaptive check frequency (`services/schedule_service.py`)

A source's configured interval is the baseline. It is halved for a product with a user price alert, a sale event on or
starting within 2 days, or a volatile price (≥ 3 changes in 14 days); doubled / tripled for a price unchanged for 30 / 90
days; tripled when out of stock; doubled / quadrupled after 3 / 6 failures in a row. Limits: never under 30 minutes,
never over a day unless the configured interval is longer. Discovered shops that were not selling are re-read every 24 h
instead of every run. `GET /api/admin/collection/schedule` shows each source's baseline, current interval and reasons.
