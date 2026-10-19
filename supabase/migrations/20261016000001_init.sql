-- SmartBuy AI core schema.
-- All application tables live in the "smartbuy" schema so they never collide with
-- other data in the same Supabase project (e.g. the existing "dashboard_ts" schema
-- written by data/extract_dashboard_timeseries.py).
--
-- Timestamp conventions (do not mix them up):
--   historical_timestamp  the date/time a price was valid according to the source's history
--   observed_at           the moment our collector saw a price on the price-history source
--   fetched_at            the moment vendor information (EMI, reviews, vendor price) was retrieved
-- All technical timestamps are timestamptz (stored UTC).

create schema if not exists smartbuy;
set search_path to smartbuy, public;

-- ---------------------------------------------------------------- registry
create table products (
    id                  text primary key check (id ~ '^[a-z0-9][a-z0-9_-]{2,63}$'),
    name                text not null,
    brand               text,
    model               text,
    category            text,
    currency            char(3) not null default 'INR' check (currency ~ '^[A-Z]{3}$'),
    description         text,
    image_url           text,
    expected_attributes jsonb not null default '{}'::jsonb,
    is_active           boolean not null default true,
    created_at          timestamptz not null default now(),
    updated_at          timestamptz not null default now()
);

create table vendors (
    id             text primary key check (id ~ '^[a-z0-9][a-z0-9_-]{2,63}$'),
    name           text not null,
    website_domain text not null,
    created_at     timestamptz not null default now()
);

-- Recurring sources. The scheduled collector reads ONLY rows with source_type = 'price_history'.
create table product_sources (
    id                          bigserial primary key,
    product_id                  text not null references products(id) on delete cascade,
    source_type                 text not null check (source_type in ('price_history', 'official_product_information')),
    source_name                 text,
    source_url                  text not null,
    adapter_name                text not null default 'auto',
    adapter_config              jsonb not null default '{}'::jsonb,
    collection_interval_minutes int  not null default 360 check (collection_interval_minutes >= 30),
    is_active                   boolean not null default true,
    last_success_at             timestamptz,
    last_attempt_at             timestamptz,
    last_error                  text,
    consecutive_failures        int not null default 0,
    created_at                  timestamptz not null default now(),
    unique (product_id, source_url)
);

-- Vendor pages. Never collected on a schedule; only fetched on demand (see services/vendor_service.py).
create table product_vendor_sources (
    id                         bigserial primary key,
    product_id                 text not null references products(id) on delete cascade,
    vendor_id                  text not null references vendors(id) on delete cascade,
    source_url                 text not null,
    adapter_name               text not null default 'vendor_generic',
    expected_vendor_product_id text,
    is_active                  boolean not null default true,
    created_at                 timestamptz not null default now(),
    unique (product_id, vendor_id, source_url)
);

-- Result of the last "inspect / extraction test" run for any URL.
create table source_extraction_tests (
    id              bigserial primary key,
    source_kind     text not null check (source_kind in ('price_history', 'vendor')),
    source_ref      bigint,
    source_url      text not null,
    adapter_name    text not null,
    adapter_version text not null,
    tested_at       timestamptz not null default now(),
    success         boolean not null,
    requires_js     boolean,
    fields_found    jsonb not null default '[]'::jsonb,
    fields_missing  jsonb not null default '[]'::jsonb,
    warnings        jsonb not null default '[]'::jsonb,
    sample          jsonb not null default '{}'::jsonb
);

-- ---------------------------------------------------------------- prices
create table price_observations (
    id                 bigserial primary key,
    product_id         text not null references products(id) on delete cascade,
    source_id          bigint not null references product_sources(id) on delete cascade,
    observed_at        timestamptz not null,
    price              numeric(12,2) not null check (price > 0),
    price_type         text not null default 'selling_price'
                       check (price_type in ('selling_price', 'offer_price', 'mrp')),
    currency           char(3) not null,
    availability       text,
    seller_name        text,
    discount_percent   numeric(5,2) check (discount_percent is null or (discount_percent >= 0 and discount_percent < 100)),
    data_origin        text not null default 'live_extraction',
    extraction_version text,
    created_at         timestamptz not null default now(),
    unique (source_id, price_type, observed_at)
);
create index price_observations_product_time_idx on price_observations (product_id, price_type, observed_at desc);

create table historical_price_points (
    id                    bigserial primary key,
    product_id            text not null references products(id) on delete cascade,
    source_id             bigint not null references product_sources(id) on delete cascade,
    historical_timestamp  timestamptz not null,
    price                 numeric(12,2) not null check (price > 0),
    price_type            text not null default 'selling_price'
                          check (price_type in ('selling_price', 'offer_price', 'mrp')),
    currency              char(3) not null,
    data_origin           text not null,           -- source_chart_data / imported_dashboard_ts / imported_csv
    confidence_or_quality text not null default 'source_reported'
                          check (confidence_or_quality in ('source_reported', 'estimated_from_chart')),
    source_label          text,                    -- raw x value as the source showed it
    created_at            timestamptz not null default now(),
    unique (product_id, source_id, price_type, historical_timestamp)
);
create index historical_price_points_product_time_idx on historical_price_points (product_id, price_type, historical_timestamp);

-- Summary statistics the source states explicitly (e.g. "Lowest Price 1035").
create table source_price_stats (
    id          bigserial primary key,
    product_id  text not null references products(id) on delete cascade,
    source_id   bigint not null references product_sources(id) on delete cascade,
    observed_at timestamptz not null,
    stat_name   text not null check (stat_name in ('lowest', 'average', 'highest', 'mrp', 'lowest_offer')),
    value       numeric(12,2) not null check (value > 0),
    currency    char(3) not null,
    unique (source_id, stat_name, observed_at)
);

-- ---------------------------------------------------------------- events
create table sale_events (
    id                  bigserial primary key,
    event_name          text not null,
    retailer            text not null default 'any',
    category            text not null default 'any',
    region              text not null default 'IN',
    start_date          date,
    end_date            date,
    event_type          text not null check (event_type in ('retailer_sale', 'festival', 'shopping_day', 'product_launch', 'flash_sale', 'season_sale')),
    source_url          text,
    verification_status text not null check (verification_status in
                        ('confirmed_official', 'reported_by_media', 'observed_on_source_chart',
                         'calendar_date', 'recurring_unconfirmed', 'hypothetical')),
    notes               text,
    created_at          timestamptz not null default now(),
    check (end_date is null or start_date is null or end_date >= start_date)
);
create unique index sale_events_uniq on sale_events (event_name, retailer, coalesce(start_date, '1900-01-01'::date));

-- ---------------------------------------------------------------- vendor cache (on-demand only)
create table vendor_snapshots (
    id                bigserial primary key,
    product_id        text not null references products(id) on delete cascade,
    vendor_id         text not null references vendors(id) on delete cascade,
    vendor_source_id  bigint not null references product_vendor_sources(id) on delete cascade,
    fetched_at        timestamptz not null,
    expires_at        timestamptz not null,
    fetch_status      text not null check (fetch_status in ('ok', 'partial', 'failed', 'blocked')),
    price             numeric(12,2) check (price is null or price > 0),
    mrp               numeric(12,2) check (mrp is null or mrp > 0),
    currency          char(3),
    availability      text,
    seller_name       text,
    delivery_text     text,
    return_policy     text,
    warranty          text,
    error_message     text,
    extraction_report jsonb not null default '{}'::jsonb
);
create index vendor_snapshots_lookup_idx on vendor_snapshots (vendor_source_id, fetched_at desc);

create table emi_offers (
    id                         bigserial primary key,
    product_id                 text not null references products(id) on delete cascade,
    vendor_id                  text not null references vendors(id) on delete cascade,
    vendor_source_id           bigint references product_vendor_sources(id) on delete cascade,
    product_price              numeric(12,2),
    principal_amount           numeric(12,2),
    down_payment               numeric(12,2),
    financed_amount            numeric(12,2),
    interest_rate              numeric(7,4),          -- percent; NULL = unknown (never 0 by default)
    interest_rate_type         text check (interest_rate_type in ('annual_reducing', 'annual_flat', 'monthly_reducing', 'monthly_flat', 'unknown')),
    tenure_months              int check (tenure_months is null or tenure_months > 0),
    monthly_emi                numeric(12,2),
    processing_fee             numeric(12,2),
    processing_fee_gst         numeric(12,2),
    other_fees                 numeric(12,2),
    upfront_discount           numeric(12,2),
    cashback                   numeric(12,2),
    total_interest             numeric(12,2),
    total_repayment            numeric(12,2),
    total_cost                 numeric(12,2),
    no_cost_emi_claim          boolean not null default false,
    bank_name                  text,
    card_type                  text,
    payment_provider           text,
    eligibility_requirements   text,
    offer_valid_until          timestamptz,
    raw_text                   text,
    assumptions                jsonb not null default '[]'::jsonb,
    source_url                 text,
    fetched_at                 timestamptz not null,
    expires_at                 timestamptz not null,
    verification_status        text not null check (verification_status in
                               ('extracted_from_page', 'parsed_heuristically', 'user_supplied', 'calculated'))
);
create index emi_offers_lookup_idx on emi_offers (product_id, vendor_id, fetched_at desc);

create table vendor_reviews (
    id                 bigserial primary key,
    product_id         text not null references products(id) on delete cascade,
    vendor_id          text not null references vendors(id) on delete cascade,
    vendor_source_id   bigint references product_vendor_sources(id) on delete cascade,
    review_kind        text not null check (review_kind in ('product_aggregate', 'product_review', 'seller_aggregate', 'seller_review')),
    review_source_url  text,
    rating             numeric(4,2),
    rating_scale       numeric(4,2),
    review_count       int,
    review_text        text,
    review_date        date,
    verified_purchase  boolean,
    sentiment          numeric(4,3),
    aspect_labels      jsonb not null default '{}'::jsonb,
    fetched_at         timestamptz not null,
    expires_at         timestamptz not null,
    verification_status text not null default 'extracted_from_page'
);
create index vendor_reviews_lookup_idx on vendor_reviews (product_id, vendor_id, fetched_at desc);

-- ---------------------------------------------------------------- analytics
create table forecasts (
    id                       bigserial primary key,
    product_id               text not null references products(id) on delete cascade,
    generated_at             timestamptz not null default now(),
    horizon_days             int not null check (horizon_days > 0),
    forecast_method          text not null,
    predicted_price          numeric(12,2),
    lower_price_bound        numeric(12,2),
    upper_price_bound        numeric(12,2),
    probability_below_target numeric(4,3),
    target_price             numeric(12,2),
    data_points_used         int not null,
    model_version            text not null,
    validation_metrics       jsonb not null default '{}'::jsonb,
    limitations              jsonb not null default '[]'::jsonb,
    base_price               numeric(12,2),
    base_timestamp           timestamptz
);
create index forecasts_product_idx on forecasts (product_id, generated_at desc);

create table purchase_recommendations (
    id                      bigserial primary key,
    product_id              text not null references products(id) on delete cascade,
    user_id                 uuid,
    generated_at            timestamptz not null default now(),
    current_price           numeric(12,2),
    target_price            numeric(12,2),
    recommended_action      text not null check (recommended_action in
                            ('BUY_NOW', 'WAIT', 'MONITOR', 'BUY_IF_TARGET_REACHED', 'COMPARE_VENDORS', 'INSUFFICIENT_DATA')),
    recommended_wait_days   int,
    estimated_savings       numeric(12,2),
    estimated_savings_range jsonb,
    confidence_level        text not null check (confidence_level in ('LOW', 'MEDIUM', 'HIGH')),
    reasoning_summary       jsonb not null default '[]'::jsonb,
    risk_factors            jsonb not null default '[]'::jsonb,
    forecast_id             bigint references forecasts(id) on delete set null,
    emi_offer_ids           bigint[] not null default '{}',
    recommendation_version  text not null,
    request                 jsonb not null default '{}'::jsonb
);
create index purchase_recommendations_product_idx on purchase_recommendations (product_id, generated_at desc);

create table prediction_outcomes (
    id              bigserial primary key,
    forecast_id     bigint not null references forecasts(id) on delete cascade,
    evaluation_date timestamptz not null default now(),
    actual_price    numeric(12,2),
    forecast_error  numeric(12,2),
    within_interval boolean,
    target_reached  boolean,
    outcome_notes   text,
    unique (forecast_id)
);

-- ---------------------------------------------------------------- operations
create table collection_jobs (
    id                bigserial primary key,
    source_id         bigint references product_sources(id) on delete cascade,
    vendor_source_id  bigint references product_vendor_sources(id) on delete cascade,
    job_type          text not null check (job_type in ('scheduled_price', 'manual_price', 'vendor_on_demand', 'source_test', 'import')),
    started_at        timestamptz not null default now(),
    completed_at      timestamptz,
    status            text not null default 'running' check (status in ('running', 'success', 'partial', 'failed', 'skipped')),
    records_extracted int not null default 0,
    records_inserted  int not null default 0,
    error_message     text,
    retry_count       int not null default 0,
    details           jsonb not null default '{}'::jsonb
);
create index collection_jobs_started_idx on collection_jobs (started_at desc);

-- ---------------------------------------------------------------- user data (RLS, see next migration)
create table user_targets (
    id            bigserial primary key,
    user_id       uuid not null,
    product_id    text not null references products(id) on delete cascade,
    target_price  numeric(12,2) check (target_price is null or target_price > 0),
    max_budget    numeric(12,2) check (max_budget is null or max_budget > 0),
    alerts_enabled boolean not null default true,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now(),
    unique (user_id, product_id)
);

create table notifications (
    id          bigserial primary key,
    user_id     uuid,                        -- NULL = broadcast to all viewers of the product
    product_id  text references products(id) on delete cascade,
    kind        text not null check (kind in ('target_reached', 'price_drop', 'back_in_stock', 'forecast_window',
                                              'sale_upcoming', 'emi_available', 'emi_expiring', 'recommendation_changed')),
    category    text not null check (category in ('observed', 'forecast', 'vendor_offer', 'recommendation', 'event')),
    message     text not null,
    observed_at timestamptz not null,
    dedupe_key  text not null unique,
    payload     jsonb not null default '{}'::jsonb,
    created_at  timestamptz not null default now(),
    read_at     timestamptz
);
create index notifications_user_idx on notifications (user_id, created_at desc);

-- ---------------------------------------------------------------- views
-- Every actually-recorded price point: chart history + our own observations.
create view v_price_points as
select product_id, source_id, price_type, historical_timestamp as ts, price, currency,
       data_origin, 'historical'::text as point_kind
from historical_price_points
union all
select product_id, source_id, price_type, observed_at as ts, price, currency,
       data_origin, 'observation'::text as point_kind
from price_observations;

-- One row per calendar day (IST) per product and price type, from first to last recorded day.
-- Days without a recorded price carry the previous recorded value forward and are flagged
-- (is_recorded = false). Carried-forward rows are an analysis convenience, NOT observed prices.
create view v_daily_price as
with pts as (
    select product_id, price_type, (ts at time zone 'Asia/Kolkata')::date as day,
           ts, price, currency
    from v_price_points
),
last_per_day as (
    select distinct on (product_id, price_type, day) product_id, price_type, day, price, currency
    from pts order by product_id, price_type, day, ts desc
),
bounds as (
    select product_id, price_type, min(day) as lo, max(day) as hi from last_per_day group by 1, 2
)
select b.product_id, b.price_type, g.day::date as date_ist, lp.price, lp.currency,
       (lp.day = g.day::date) as is_recorded,
       (g.day::date - lp.day) as days_since_recorded
from bounds b
cross join lateral generate_series(b.lo, b.hi, interval '1 day') as g(day)
cross join lateral (
    select l.day, l.price, l.currency from last_per_day l
    where l.product_id = b.product_id and l.price_type = b.price_type and l.day <= g.day::date
    order by l.day desc limit 1
) lp;

create table schema_migrations (
    version    text primary key,
    applied_at timestamptz not null default now()
);
