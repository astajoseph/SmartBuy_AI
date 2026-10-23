-- Review integrity: the details needed to tell organic reviews from suspicious ones.
-- Filled by the vendor adapters where the page shows them; NULL = the page did not show it (never guessed).
set search_path to smartbuy, public;

alter table vendor_reviews
    add column external_review_id  text,      -- the vendor's own review id (Amazon R..., Myntra uuid) - same review across fetches
    add column reviewer_name       text,
    add column review_title        text,
    add column helpful_votes       int check (helpful_votes is null or helpful_votes >= 0),
    add column unhelpful_votes     int check (unhelpful_votes is null or unhelpful_votes >= 0),
    add column has_media           boolean,   -- photos / video attached to the review
    add column variant_text        text,      -- variant the reviewer bought ("Size: 50 g (Pack of 1)")
    add column incentivized        boolean,   -- marked by the vendor as written for a free product (e.g. Amazon Vine)
    add column rating_distribution jsonb;     -- aggregates: {"5": 8437, "4": 2056, ...} counts or {"5": 61, ...} percent

create index vendor_reviews_history_idx on vendor_reviews (vendor_source_id, review_kind, fetched_at desc);
