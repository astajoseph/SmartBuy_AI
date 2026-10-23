-- Restore smartbuy.historical_price_points and the two views that depend on it.
-- On 2026-10-24 the table was dropped in the Supabase project with DROP TABLE ... CASCADE (which also removed
-- v_price_points and v_daily_price); the statement was not issued by this code base. Definitions are copied
-- unchanged from 20261016000001_init.sql. Idempotent: on a database that has them, nothing changes.
-- The rows are rebuilt afterwards by a collection run (full source chart) and the dashboard_ts re-import.
set search_path to smartbuy, public;

create table if not exists historical_price_points (
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
create index if not exists historical_price_points_product_time_idx on historical_price_points (product_id, price_type, historical_timestamp);
alter table historical_price_points enable row level security;

create or replace view v_price_points as
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
create or replace view v_daily_price as
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
