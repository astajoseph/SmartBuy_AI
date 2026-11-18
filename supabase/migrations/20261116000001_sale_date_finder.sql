-- Sale-date finder (services/sale_date_finder.py): a weekly web search for announced dates of recurring Indian sales.
-- Each find is only a candidate until an admin approves it; approval creates a sale_events row with
-- verification_status = 'reported_by_media' and the page as source_url.
set search_path to smartbuy, public;

create table sale_event_candidates (
    id             bigserial primary key,
    event_name     text not null,
    retailer       text not null default 'any',
    event_type     text not null,
    start_date     date not null,
    end_date       date,
    source_url     text not null,
    source_title   text,
    evidence       text not null,          -- sentence copied verbatim from the page; contains the dates
    method         text not null check (method in ('llm', 'pattern')),
    status         text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
    found_at       timestamptz not null default now(),
    decided_at     timestamptz,
    decided_by     text,
    sale_event_id  bigint references sale_events(id) on delete set null,
    check (end_date is null or end_date >= start_date),
    unique (event_name, retailer, start_date, source_url)
);
create index sale_event_candidates_status_idx on sale_event_candidates (status, found_at desc);
alter table sale_event_candidates enable row level security;

-- One row per finder run, so the weekly job can tell when it last ran.
create table sale_date_search_runs (
    id          bigserial primary key,
    ran_at      timestamptz not null default now(),
    events      int not null default 0,
    queries     int not null default 0,
    pages       int not null default 0,
    candidates  int not null default 0,
    note        text
);
alter table sale_date_search_runs enable row level security;
