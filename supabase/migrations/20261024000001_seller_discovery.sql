-- Seller discovery: marketplace sub-sellers, vendor websites found by web search, and their lifecycle.
--
-- * vendor_sellers        - the sellers listed on a registered marketplace page (Amazon "other sellers",
--                           Myntra's seller list). One row per (marketplace page, seller id).
-- * vendor_seller_offers  - what each seller offered at each discovery check (price, stock, rating).
-- * discovery_candidates  - pages found by web search, with the verification evidence; verified ones
--                           become product_vendor_sources (origin = 'web_search'), unclear ones wait
--                           for an admin, mismatches are kept as 'rejected' so they are not re-checked.
-- Lifecycle: every check that sees a seller / page in stock moves last_in_stock_at. Discovered sellers
-- and sources not in stock for SELLER_INACTIVE_DAYS are deactivated (never deleted, history stays) and
-- re-activated when they come back. Sources from config/products.yaml (origin = 'registry') are never
-- deactivated automatically.
set search_path to smartbuy, public;

alter table product_vendor_sources
    add column origin              text not null default 'registry'
                                   check (origin in ('registry', 'web_search', 'admin_approved')),
    add column first_seen_at       timestamptz not null default now(),
    add column last_checked_at     timestamptz,
    add column last_in_stock_at    timestamptz,
    add column deactivated_at      timestamptz,
    add column deactivation_reason text;

create table vendor_sellers (
    id                  bigserial primary key,
    vendor_source_id    bigint not null references product_vendor_sources(id) on delete cascade,
    product_id          text not null references products(id) on delete cascade,
    seller_key          text not null,             -- marketplace's own seller id (Amazon merchant id, Myntra partner id)
    seller_name         text,
    seller_url          text,
    is_active           boolean not null default true,
    first_seen_at       timestamptz not null default now(),
    last_seen_at        timestamptz not null default now(),   -- last check that listed this seller at all
    last_in_stock_at    timestamptz,                           -- last check where its offer was purchasable
    deactivated_at      timestamptz,
    deactivation_reason text,
    unique (vendor_source_id, seller_key)
);
create index vendor_sellers_product_idx on vendor_sellers (product_id, is_active);

create table vendor_seller_offers (
    id                 bigserial primary key,
    seller_id          bigint not null references vendor_sellers(id) on delete cascade,
    observed_at        timestamptz not null,
    price              numeric(12,2) check (price is null or price > 0),
    mrp                numeric(12,2) check (mrp is null or mrp > 0),
    currency           char(3),
    in_stock           boolean not null,
    condition          text,
    fulfilled_by       text,                       -- e.g. 'Amazon' when the marketplace ships it
    min_quantity       int,
    seller_rating      numeric(3,2),
    seller_rating_count int,
    seller_positive_pct numeric(5,2),
    delivery_text      text,
    unique (seller_id, observed_at)
);
create index vendor_seller_offers_lookup_idx on vendor_seller_offers (seller_id, observed_at desc);

create table discovery_candidates (
    id                 bigserial primary key,
    product_id         text not null references products(id) on delete cascade,
    url                text not null,
    host               text not null,
    search_query       text,
    search_title       text,
    status             text not null default 'new'       -- new = found, not verified yet
                       check (status in ('new', 'pending', 'auto_added', 'approved', 'rejected', 'error')),
    match_level        text check (match_level in ('strong', 'partial', 'none')),
    match_evidence     jsonb not null default '{}'::jsonb,
    observed_price     numeric(12,2),
    vendor_source_id   bigint references product_vendor_sources(id) on delete set null,
    first_found_at     timestamptz not null default now(),
    last_found_at      timestamptz not null default now(),
    last_checked_at    timestamptz,
    decided_at         timestamptz,
    decided_by         text,
    error_message      text,
    unique (product_id, url)
);
create index discovery_candidates_status_idx on discovery_candidates (status, last_found_at desc);

-- Paid search API usage per calendar month (UTC). Discovery reserves queries here BEFORE calling the API and
-- stops at BRAVE_MONTHLY_QUERY_LIMIT, so usage cannot run past the provider's free monthly credit.
create table search_api_usage (
    provider   text not null,
    month      date not null,                    -- first day of the month
    queries    int  not null default 0 check (queries >= 0),
    updated_at timestamptz not null default now(),
    primary key (provider, month)
);

alter table collection_jobs drop constraint collection_jobs_job_type_check;
alter table collection_jobs add constraint collection_jobs_job_type_check
    check (job_type in ('scheduled_price', 'manual_price', 'vendor_on_demand', 'source_test', 'import', 'discovery'));

alter table vendor_sellers enable row level security;
alter table vendor_seller_offers enable row level security;
alter table discovery_candidates enable row level security;
alter table search_api_usage enable row level security;
