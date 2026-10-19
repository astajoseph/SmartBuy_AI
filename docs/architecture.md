# Architecture

## Components

| Component | Tech | Responsibility |
|---|---|---|
| Web app | Next.js 15, TypeScript, Tailwind, Recharts | Dashboard, product detail, advisor + chat, vendors/EMI, reviews, sales, settings, admin. Talks only to the public API URL. |
| API | FastAPI, Pydantic | Read endpoints, recommendation and chat endpoints, admin endpoints (bearer token). OpenAPI at `/docs`. |
| Collector worker | `app.workers.scheduler` | Scheduled price-history collection, forecast refresh, forecast scoring, alert evaluation. |
| Database | Supabase PostgreSQL, schema `smartbuy` | All state. RLS on every table. |
| LLM | Gemini via `google-genai` (swappable) | Explains structured results. Never decides. |

## Separation of the two data paths

| | Price-history path | Vendor path |
|---|---|---|
| Trigger | schedule (`due_price_sources`) or admin "collect now" | a user asking for EMI / vendor / review analysis |
| Code | `workers/price_collector.py` | `services/vendor_service.py` |
| Tables read for URLs | `product_sources` (`source_type = 'price_history'`) | `product_vendor_sources` |
| Caching | interval per source (default 6 h) | TTL per data kind + negative cache after failures |
| Guarantee | never reads `product_vendor_sources` | only called from vendor/recommendation/assistant routes when requested |

Both are enforced by tests in `backend/tests/test_vendor_on_demand.py`.

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
* The assistant is read-only: it has no tools that write, fetch arbitrary URLs or transact.
