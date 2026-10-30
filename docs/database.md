# Database (Supabase PostgreSQL, schema `smartbuy`)

Migrations: `supabase/migrations/*.sql`, applied by `scripts/migrate.py` (records versions in
`smartbuy.schema_migrations`) or by `supabase db push`.

## Timestamp semantics

| Column | Meaning |
|---|---|
| `historical_price_points.historical_timestamp` | when the price was valid according to the source's history (date-only values = 00:00 IST of that date) |
| `price_observations.observed_at` | when **our collector** saw the price on the price-history source |
| `vendor_snapshots / emi_offers / vendor_reviews.fetched_at` | when vendor information was retrieved |
| `*.expires_at` | end of the cache window for that vendor data |
| `emi_offers.offer_valid_until` | validity stated by the vendor (checked independently of the cache) |

All are `timestamptz` (UTC). Money is `numeric(12,2)` with `CHECK (price > 0)`; currency is `char(3)`.

## Tables

| Table | Purpose / key constraints |
|---|---|
| `products` | catalogue; `id` slug |
| `vendors` | vendor names + domain |
| `product_sources` | recurring sources (`price_history`); interval, adapter, `adapter_config`, health fields; unique `(product_id, source_url)` |
| `product_vendor_sources` | vendor URLs per product; unique `(product_id, vendor_id, source_url)`; `origin` = `registry` (products.yaml) / `web_search` / `admin_approved`; lifecycle `first_seen_at`, `last_checked_at`, `last_in_stock_at`, `deactivated_at`, `deactivation_reason` |
| `vendor_sellers` | sellers listed on a marketplace vendor page; unique `(vendor_source_id, seller_key)` (Amazon merchant id, Myntra partner id); `is_active`, `last_seen_at`, `last_in_stock_at`, deactivation fields |
| `vendor_seller_offers` | each seller's offer at each discovery check: price, MRP, in stock, condition, fulfilled by, minimum quantity, seller rating; unique `(seller_id, observed_at)` |
| `discovery_candidates` | pages found by web search or submitted by a person (`submitted_by`); `status` = `new` / `pending` / `auto_added` / `approved` / `rejected` / `error`, `match_level`, `match_evidence` (reasons, sizes, brand, name-word share, price check), linked `vendor_source_id` |
| `source_extraction_tests` | results of URL inspections (fields found / missing) |
| `price_observations` | our observations; unique `(source_id, price_type, observed_at)` |
| `historical_price_points` | source history; unique `(product_id, source_id, price_type, historical_timestamp)`; `data_origin` = `source_chart_data` / `imported_dashboard_ts` / `imported_csv`; `confidence_or_quality` = `source_reported` / `estimated_from_chart` |
| `source_price_stats` | lowest / average / highest / MRP / lowest_offer as stated by the source |
| `sale_events` | event registry with `verification_status` (`confirmed_official`, `reported_by_media`, `observed_on_source_chart`, `calendar_date`, `recurring_unconfirmed`, `hypothetical`) |
| `vendor_snapshots` | one row per vendor fetch (ok / partial / failed / blocked) incl. per-kind expiry map; delivery and membership terms as stated on the page: `delivery_fee`, `free_delivery`, `free_delivery_min_order`, `handling_fee`, `membership_program`, `membership_free_delivery`, `member_price`, `membership_text` (NULL = not stated) |
| `vendor_price_points` | **the vendor's own price history**: one row per successful vendor check — price, MRP, `in_stock`, delivery terms, `effective_price` (price + delivery where known), `delivery_known`; unique `(vendor_source_id, observed_at)`. Kept (unlike the `vendor_snapshots` cache). Backfilled from existing snapshots |
| `emi_offers` | parsed EMI terms + calculated totals, assumptions, raw text, verification status |
| `vendor_reviews` | aggregates and individual reviews, product vs seller (`review_kind`), sentiment, aspects; integrity inputs `external_review_id`, `reviewer_name`, `review_title`, `helpful_votes`, `unhelpful_votes`, `has_media`, `variant_text`, `incentivized`; aggregates `rating_distribution` (`{"unit": "count"/"percent", "stars": {...}}`) |
| `forecasts` | method, point, interval, probability, validation metrics, limitations, base price/time |
| `prediction_outcomes` | actual vs forecast once the horizon has passed |
| `purchase_recommendations` | every recommendation with reasons, risks, request, linked forecast / EMI offers |
| `collection_jobs` | every scheduled/manual/vendor/test job with counts, retries, errors, extraction report |
| `user_targets` | per-user target price / budget (RLS: owner only) |
| `notifications` | in-app alerts with category and dedupe key (RLS: owner or broadcast) |

`price_type` distinguishes `selling_price`, `offer_price` (after bank/coupon offers) and `mrp`.

## Views

* `v_price_points` — every recorded price (history + observations) with `point_kind`.
* `v_daily_price` — one row per IST day per product and price type, carrying the last recorded price
  forward; `is_recorded` and `days_since_recorded` flag assumed rows. Exported by `scripts/export_daily.py`.
  (Analysis code additionally treats > 30 days since a record as unknown.)

## Row Level Security

RLS is enabled on every table. The backend connects as a privileged role (bypasses RLS) and filters
user rows by the verified JWT subject itself. Policies (created only when Supabase's `auth` schema
exists): `user_targets` — owner full access; `notifications` — owner or broadcast read, owner update.
With no other policies, the anon/authenticated API roles cannot read application tables if the schema
is ever exposed through PostgREST.

## Retention

Nothing is deleted automatically (discovered sellers and shops are deactivated, not deleted). Suggested policy (not yet scheduled): keep all price data; prune
`vendor_snapshots`, `emi_offers` and `vendor_reviews` older than 90 days, and `collection_jobs.details`
older than 30 days.

## Relationship to `dashboard_ts`

`dashboard_ts` belongs to `data/extract_dashboard_timeseries.py` and is only read by
`scripts/import_legacy.py dashboard-ts`. SmartBuy never writes to it.
