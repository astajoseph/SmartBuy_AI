-- Delivery / membership pricing, per-vendor price history, user-submitted vendor URLs.
set search_path to smartbuy, public;

-- 1. What the vendor page says about delivery charges and membership benefits (all NULL = not stated on the page).
alter table vendor_snapshots
    add column delivery_fee             numeric(12,2) check (delivery_fee is null or delivery_fee >= 0),
    add column free_delivery            boolean,
    add column free_delivery_min_order  numeric(12,2) check (free_delivery_min_order is null or free_delivery_min_order > 0),
    add column handling_fee             numeric(12,2) check (handling_fee is null or handling_fee >= 0),
    add column membership_program       text,
    add column membership_free_delivery boolean,
    add column member_price             numeric(12,2) check (member_price is null or member_price > 0),
    add column membership_text          text;

-- 2. One row per successful vendor check: the vendor's own price series (price, stock, delivery, effective price).
--    vendor_snapshots is a cache that may be pruned; this table is the history and is kept.
create table vendor_price_points (
    id                      bigserial primary key,
    product_id              text not null references products(id) on delete cascade,
    vendor_id               text not null references vendors(id) on delete cascade,
    vendor_source_id        bigint not null references product_vendor_sources(id) on delete cascade,
    observed_at             timestamptz not null,
    price                   numeric(12,2) check (price is null or price > 0),
    mrp                     numeric(12,2) check (mrp is null or mrp > 0),
    in_stock                boolean not null,
    availability            text,
    seller_name             text,
    delivery_fee            numeric(12,2),
    free_delivery           boolean,
    free_delivery_min_order numeric(12,2),
    handling_fee            numeric(12,2),
    member_price            numeric(12,2),
    effective_price         numeric(12,2),          -- price + delivery where the delivery cost is known; else = price
    delivery_known          boolean not null default false,
    unique (vendor_source_id, observed_at)
);
create index vendor_price_points_source_idx  on vendor_price_points (vendor_source_id, observed_at desc);
create index vendor_price_points_product_idx on vendor_price_points (product_id, observed_at desc);
alter table vendor_price_points enable row level security;

insert into vendor_price_points (product_id, vendor_id, vendor_source_id, observed_at, price, mrp, in_stock,
                                 availability, seller_name, effective_price)
select product_id, vendor_id, vendor_source_id, fetched_at, price, mrp,
       price is not null and coalesce(availability, '') !~* '(out.?of.?stock|sold.?out|discontinued|pre.?order|back.?order)',
       availability, seller_name, price
from vendor_snapshots
where fetch_status in ('ok', 'partial') and (price is not null or availability is not null)
on conflict do nothing;

-- 3. Pages that people submitted themselves (checked like web-search finds).
alter table discovery_candidates add column submitted_by text;
