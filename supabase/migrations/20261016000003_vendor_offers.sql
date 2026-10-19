-- Bank / cashback / partner offers shown on vendor pages, and extra vendor-snapshot facts.
set search_path to smartbuy, public;

alter table vendor_snapshots
    add column emi_status text check (emi_status in ('available', 'not_offered', 'not_shown', 'unknown')),
    add column details jsonb not null default '{}'::jsonb;   -- stock units, discount end time, COD, product expiry ...

create table vendor_bank_offers (
    id                bigserial primary key,
    product_id        text not null references products(id) on delete cascade,
    vendor_id         text not null references vendors(id) on delete cascade,
    vendor_source_id  bigint not null references product_vendor_sources(id) on delete cascade,
    category          text,                     -- as labelled by the vendor (Bank Offer, Cashback, BANK ...)
    kind              text not null check (kind in ('instant_discount', 'cashback', 'partner_offer', 'other')),
    title             text not null,
    description       text,
    bank              text,
    card_type         text,
    percent           numeric(5,2),
    flat_amount       numeric(12,2),
    max_discount      numeric(12,2),
    min_spend         numeric(12,2),
    applies_to_emi    boolean not null default false,
    terms_complete    boolean not null default false,
    fetched_at        timestamptz not null,
    expires_at        timestamptz not null,
    verification_status text not null default 'extracted_from_page'
);
create index vendor_bank_offers_lookup_idx on vendor_bank_offers (vendor_source_id, fetched_at desc);
alter table vendor_bank_offers enable row level security;
