"""All SQL used by the application. Every method takes plain Python values and returns dict rows.

Writes are idempotent: unique constraints + ON CONFLICT DO NOTHING mean a retried or concurrent
job cannot create duplicate observations, and an existing valid price is never overwritten by a
later NULL / zero / invalid value (invalid values are rejected before they reach this layer and
the price columns have CHECK (price > 0)).
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import urlsplit

from psycopg import Connection
from psycopg.types.json import Jsonb

from app.database.client import Database
from app.extraction.base import CurrentPrice, PricePoint, SaleBand
from app.extraction.validation import retailer_key

LOCK_NAMESPACE_PRICE = 4201
LOCK_NAMESPACE_VENDOR = 4202
LOCK_NAMESPACE_DISCOVERY = 4203


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json(v: Any) -> Jsonb:
    return Jsonb(json.loads(json.dumps(v, default=str)))


class Repository:
    def __init__(self, db: Database):
        self.db = db

    # ------------------------------------------------------------------ helpers
    def _all(self, sql: str, params: Iterable | dict | None = None) -> list[dict]:
        with self.db.connection() as conn:
            return conn.execute(sql, params).fetchall()

    def _one(self, sql: str, params: Iterable | dict | None = None) -> dict | None:
        with self.db.connection() as conn:
            return conn.execute(sql, params).fetchone()

    # ------------------------------------------------------------------ registry
    def upsert_product(self, p: dict) -> None:
        self._one(
            """insert into products (id, name, brand, model, category, currency, description, image_url,
                                     expected_attributes, is_active)
               values (%(id)s, %(name)s, %(brand)s, %(model)s, %(category)s, %(currency)s, %(description)s,
                       %(image_url)s, %(expected_attributes)s, %(is_active)s)
               on conflict (id) do update set name = excluded.name, brand = excluded.brand, model = excluded.model,
                   category = excluded.category, currency = excluded.currency, description = excluded.description,
                   image_url = coalesce(excluded.image_url, products.image_url),
                   expected_attributes = excluded.expected_attributes, is_active = excluded.is_active,
                   updated_at = now()
               returning id""",
            {"brand": None, "model": None, "category": None, "currency": "INR", "description": None,
             "image_url": None, "is_active": True, **p,
             "expected_attributes": _json(p.get("expected_attributes") or {})})

    def set_product_image_if_missing(self, product_id: str, image_url: str | None) -> None:
        if image_url:
            self._one("update products set image_url = %s where id = %s and image_url is null returning id",
                      (image_url, product_id))

    def upsert_vendor(self, vendor_id: str, name: str, domain: str) -> None:
        self._one("""insert into vendors (id, name, website_domain) values (%s, %s, %s)
                     on conflict (id) do update set name = excluded.name, website_domain = excluded.website_domain
                     returning id""", (vendor_id, name, domain))

    def upsert_price_source(self, product_id: str, url: str, *, adapter_name: str = "auto",
                            adapter_config: dict | None = None, interval_minutes: int = 360,
                            source_name: str | None = None, source_type: str = "price_history",
                            is_active: bool = True) -> int:
        row = self._one(
            """insert into product_sources (product_id, source_type, source_name, source_url, adapter_name,
                                            adapter_config, collection_interval_minutes, is_active)
               values (%s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (product_id, source_url) do update set source_type = excluded.source_type,
                   source_name = excluded.source_name, adapter_name = excluded.adapter_name,
                   adapter_config = excluded.adapter_config,
                   collection_interval_minutes = excluded.collection_interval_minutes, is_active = excluded.is_active
               returning id""",
            (product_id, source_type, source_name, url, adapter_name, _json(adapter_config or {}),
             interval_minutes, is_active))
        return row["id"]

    def upsert_vendor_source(self, product_id: str, vendor_id: str, url: str, *, adapter_name: str = "vendor_generic",
                             expected_vendor_product_id: str | None = None, is_active: bool = True) -> int:
        row = self._one(
            """insert into product_vendor_sources (product_id, vendor_id, source_url, adapter_name,
                                                   expected_vendor_product_id, is_active)
               values (%s, %s, %s, %s, %s, %s)
               on conflict (product_id, vendor_id, source_url) do update set adapter_name = excluded.adapter_name,
                   expected_vendor_product_id = excluded.expected_vendor_product_id, is_active = excluded.is_active,
                   origin = 'registry', deactivated_at = null, deactivation_reason = null
               returning id""",
            (product_id, vendor_id, url, adapter_name, expected_vendor_product_id, is_active))
        return row["id"]

    def list_products(self, include_inactive: bool = False) -> list[dict]:
        return self._all(
            f"""select p.*,
                   (select max(ts) from v_price_points v where v.product_id = p.id) as last_price_ts,
                   (select max(s.last_success_at) from product_sources s where s.product_id = p.id) as last_collection_at
                from products p {'' if include_inactive else 'where p.is_active'} order by p.created_at, p.id""")

    def get_product(self, product_id: str) -> dict | None:
        return self._one("select * from products where id = %s", (product_id,))

    def list_price_sources(self, product_id: str | None = None) -> list[dict]:
        return self._all(
            """select * from product_sources where source_type = 'price_history'
               and (%(pid)s::text is null or product_id = %(pid)s) order by id""", {"pid": product_id})

    def due_price_sources(self, now: datetime | None = None, failure_retry_minutes: int = 30) -> list[dict]:
        """Enabled price-history sources whose interval has elapsed. Vendor sources are never returned."""
        return self._all(
            """select s.* from product_sources s join products p on p.id = s.product_id
               where s.source_type = 'price_history' and s.is_active and p.is_active
                 and (s.last_success_at is null
                      or s.last_success_at + make_interval(mins => s.collection_interval_minutes) <= %(now)s)
                 and (s.last_attempt_at is null
                      or s.last_attempt_at + make_interval(mins => %(retry)s) <= %(now)s
                      or s.last_success_at >= s.last_attempt_at)
               order by s.last_success_at nulls first, s.id""",
            {"now": now or _now(), "retry": failure_retry_minutes})

    def get_price_source(self, source_id: int) -> dict | None:
        return self._one("select * from product_sources where id = %s", (source_id,))

    def list_vendor_sources(self, product_id: str) -> list[dict]:
        return self._all(
            """select vs.*, v.name as vendor_name, v.website_domain from product_vendor_sources vs
               join vendors v on v.id = vs.vendor_id where vs.product_id = %s and vs.is_active order by vs.id""",
            (product_id,))

    def get_vendor_source(self, vendor_source_id: int) -> dict | None:
        return self._one(
            """select vs.*, v.name as vendor_name from product_vendor_sources vs join vendors v on v.id = vs.vendor_id
               where vs.id = %s""", (vendor_source_id,))

    def registered_hosts(self) -> set[str]:
        rows = self._all("select source_url from product_sources union select source_url from product_vendor_sources")
        return {(urlsplit(r["source_url"]).hostname or "").lower() for r in rows} - {""}

    # ------------------------------------------------------------------ locking & jobs
    @staticmethod
    def try_lock(conn: Connection, namespace: int, key: int) -> bool:
        return conn.execute("select pg_try_advisory_lock(%s, %s) as ok", (namespace, key)).fetchone()["ok"]

    @staticmethod
    def unlock(conn: Connection, namespace: int, key: int) -> None:
        conn.execute("select pg_advisory_unlock(%s, %s)", (namespace, key))

    def start_job(self, job_type: str, *, source_id: int | None = None, vendor_source_id: int | None = None) -> int:
        return self._one(
            "insert into collection_jobs (job_type, source_id, vendor_source_id) values (%s, %s, %s) returning id",
            (job_type, source_id, vendor_source_id))["id"]

    def finish_job(self, job_id: int, status: str, *, extracted: int = 0, inserted: int = 0,
                   error: str | None = None, retries: int = 0, details: dict | None = None) -> None:
        self._one(
            """update collection_jobs set completed_at = now(), status = %s, records_extracted = %s,
                   records_inserted = %s, error_message = %s, retry_count = %s, details = %s
               where id = %s returning id""",
            (status, extracted, inserted, (error or "")[:2000] or None, retries, _json(details or {}), job_id))

    def list_jobs(self, limit: int = 50, status: str | None = None) -> list[dict]:
        return self._all(
            """select j.*, coalesce(s.product_id, vs.product_id) as product_id,
                      coalesce(s.source_url, vs.source_url) as source_url
               from collection_jobs j
               left join product_sources s on s.id = j.source_id
               left join product_vendor_sources vs on vs.id = j.vendor_source_id
               where (%(st)s::text is null or j.status = %(st)s)
               order by j.started_at desc limit %(lim)s""", {"st": status, "lim": limit})

    def mark_source_attempt(self, source_id: int, *, success: bool, error: str | None = None) -> None:
        if success:
            self._one("""update product_sources set last_attempt_at = now(), last_success_at = now(),
                         last_error = null, consecutive_failures = 0 where id = %s returning id""", (source_id,))
        else:
            self._one("""update product_sources set last_attempt_at = now(), last_error = %s,
                         consecutive_failures = consecutive_failures + 1 where id = %s returning id""",
                      ((error or "")[:1000], source_id))

    def source_health(self) -> list[dict]:
        return self._all(
            """select s.id, s.product_id, s.source_url, s.adapter_name, s.is_active, s.collection_interval_minutes,
                      s.last_attempt_at, s.last_success_at, s.last_error, s.consecutive_failures,
                      case when not s.is_active then 'disabled'
                           when s.last_success_at is null and s.last_attempt_at is null then 'never_run'
                           when s.consecutive_failures >= 3 then 'failing'
                           when s.consecutive_failures > 0 then 'degraded'
                           when s.last_success_at < now() - make_interval(mins => 3 * s.collection_interval_minutes) then 'stale'
                           else 'healthy' end as health,
                      (select count(*) from historical_price_points h where h.source_id = s.id) as history_points,
                      (select count(*) from price_observations o where o.source_id = s.id) as observations
               from product_sources s order by s.id""")

    def record_source_test(self, kind: str, ref: int | None, report: dict, success: bool) -> None:
        self._one(
            """insert into source_extraction_tests (source_kind, source_ref, source_url, adapter_name, adapter_version,
                   success, requires_js, fields_found, fields_missing, warnings, sample)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (kind, ref, report["url"], report["adapter"], report["adapter_version"], success, report.get("requires_js"),
             _json(report["fields_found"]), _json(report["fields_missing"]), _json(report["warnings"]), _json(report)))

    # ------------------------------------------------------------------ prices
    def insert_history_points(self, product_id: str, source_id: int, points: list[PricePoint], data_origin: str) -> int:
        if not points:
            return 0
        with self.db.connection() as conn:
            cur = conn.execute(
                """insert into historical_price_points (product_id, source_id, historical_timestamp, price, price_type,
                                                        currency, data_origin, confidence_or_quality, source_label)
                   select %s, %s, t.ts, t.price, t.ptype, t.cur, %s, t.q, t.label
                   from unnest(%s::timestamptz[], %s::numeric[], %s::text[], %s::text[], %s::text[], %s::text[])
                        as t(ts, price, ptype, cur, q, label)
                   on conflict (product_id, source_id, price_type, historical_timestamp) do nothing""",
                (product_id, source_id, data_origin,
                 [p.timestamp for p in points], [p.price for p in points], [p.price_type for p in points],
                 [p.currency for p in points], [p.quality for p in points], [p.source_label for p in points]))
            return cur.rowcount

    def insert_observations(self, product_id: str, source_id: int, observed_at: datetime,
                            prices: list[CurrentPrice], extraction_version: str) -> int:
        n = 0
        with self.db.connection() as conn, conn.transaction():
            for c in prices:
                cur = conn.execute(
                    """insert into price_observations (product_id, source_id, observed_at, price, price_type, currency,
                           availability, seller_name, discount_percent, data_origin, extraction_version)
                       values (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'live_extraction', %s)
                       on conflict (source_id, price_type, observed_at) do nothing""",
                    (product_id, source_id, observed_at, c.price, c.price_type, c.currency, c.availability,
                     c.seller_name, c.discount_percent, extraction_version))
                n += cur.rowcount
        return n

    def insert_source_stats(self, product_id: str, source_id: int, observed_at: datetime,
                            stats: dict[str, Decimal], currency: str) -> int:
        n = 0
        with self.db.connection() as conn, conn.transaction():
            for name, value in stats.items():
                n += conn.execute(
                    """insert into source_price_stats (product_id, source_id, observed_at, stat_name, value, currency)
                       values (%s, %s, %s, %s, %s, %s) on conflict do nothing""",
                    (product_id, source_id, observed_at, name, value, currency)).rowcount
        return n

    def price_points(self, product_id: str, price_type: str | None = "selling_price",
                     since: datetime | None = None) -> list[dict]:
        return self._all(
            """select ts, price, price_type, currency, data_origin, point_kind, source_id from v_price_points
               where product_id = %(pid)s and (%(pt)s::text is null or price_type = %(pt)s)
                 and (%(since)s::timestamptz is null or ts >= %(since)s)
               order by ts, point_kind""", {"pid": product_id, "pt": price_type, "since": since})

    def daily_prices(self, product_id: str, price_type: str = "selling_price") -> list[dict]:
        return self._all(
            """select date_ist, price, currency, is_recorded, days_since_recorded from v_daily_price
               where product_id = %s and price_type = %s order by date_ist""", (product_id, price_type))

    def latest_price(self, product_id: str, price_type: str = "selling_price") -> dict | None:
        return self._one(
            """select ts, price, currency, point_kind, data_origin from v_price_points
               where product_id = %s and price_type = %s order by ts desc,
               case point_kind when 'observation' then 0 else 1 end limit 1""", (product_id, price_type))

    def latest_observation(self, product_id: str) -> dict | None:
        return self._one(
            """select * from price_observations where product_id = %s and price_type = 'selling_price'
               order by observed_at desc limit 1""", (product_id,))

    def latest_source_stats(self, product_id: str) -> dict[str, dict]:
        rows = self._all(
            """select distinct on (stat_name) stat_name, value, currency, observed_at from source_price_stats
               where product_id = %s order by stat_name, observed_at desc""", (product_id,))
        return {r["stat_name"]: r for r in rows}

    # ------------------------------------------------------------------ events
    def upsert_sale_event(self, e: dict) -> bool:
        row = self._one(
            """insert into sale_events (event_name, retailer, category, region, start_date, end_date, event_type,
                                        source_url, verification_status, notes)
               values (%(event_name)s, %(retailer)s, %(category)s, %(region)s, %(start_date)s, %(end_date)s,
                       %(event_type)s, %(source_url)s, %(verification_status)s, %(notes)s)
               on conflict (event_name, retailer, coalesce(start_date, '1900-01-01'::date)) do update set
                   end_date = coalesce(excluded.end_date, sale_events.end_date),
                   source_url = coalesce(excluded.source_url, sale_events.source_url),
                   verification_status = excluded.verification_status, notes = excluded.notes,
                   category = excluded.category, event_type = excluded.event_type
               returning (xmax = 0) as inserted""",
            {"retailer": "any", "category": "any", "region": "IN", "start_date": None, "end_date": None,
             "source_url": None, "notes": None, **e})
        return bool(row and row["inserted"])

    def upsert_sale_bands(self, bands: list[SaleBand], source_url: str) -> int:
        n = 0
        for b in bands:
            if not b.retailer:
                continue          # a chart band without a known retailer must not apply to every retailer
            n += self.upsert_sale_event({
                "event_name": b.label, "retailer": retailer_key(b.retailer), "start_date": b.start,
                "end_date": b.end, "event_type": "retailer_sale", "source_url": source_url,
                "verification_status": "observed_on_source_chart",
                "notes": "Sale window drawn as a shaded band on the price-history source's chart."})
        return n

    def list_sale_events(self, *, retailer: str | None = None, from_date: date | None = None) -> list[dict]:
        return self._all(
            """select * from sale_events
               where (%(r)s::text is null or retailer in (%(r)s, 'any'))
                 and (%(f)s::date is null or coalesce(end_date, start_date) >= %(f)s or start_date is null)
               order by start_date nulls last, event_name""", {"r": retailer, "f": from_date})

    # ------------------------------------------------------------------ vendor cache
    def latest_vendor_snapshot(self, vendor_source_id: int) -> dict | None:
        return self._one(
            "select * from vendor_snapshots where vendor_source_id = %s order by fetched_at desc limit 1",
            (vendor_source_id,))

    def latest_ok_vendor_snapshot(self, vendor_source_id: int) -> dict | None:
        return self._one(
            """select * from vendor_snapshots where vendor_source_id = %s and fetch_status in ('ok', 'partial')
               order by fetched_at desc limit 1""", (vendor_source_id,))

    def insert_vendor_snapshot(self, s: dict) -> int:
        return self._one(
            """insert into vendor_snapshots (product_id, vendor_id, vendor_source_id, fetched_at, expires_at,
                   fetch_status, price, mrp, currency, availability, seller_name, delivery_text, return_policy,
                   warranty, error_message, extraction_report, emi_status, details)
               values (%(product_id)s, %(vendor_id)s, %(vendor_source_id)s, %(fetched_at)s, %(expires_at)s,
                   %(fetch_status)s, %(price)s, %(mrp)s, %(currency)s, %(availability)s, %(seller_name)s,
                   %(delivery_text)s, %(return_policy)s, %(warranty)s, %(error_message)s, %(extraction_report)s,
                   %(emi_status)s, %(details)s)
               returning id""",
            {"price": None, "mrp": None, "currency": None, "availability": None, "seller_name": None,
             "delivery_text": None, "return_policy": None, "warranty": None, "error_message": None,
             "emi_status": None, **s,
             "extraction_report": _json(s.get("extraction_report") or {}), "details": _json(s.get("details") or {})})["id"]

    def insert_bank_offers(self, offers: list[dict]) -> int:
        cols = ["product_id", "vendor_id", "vendor_source_id", "category", "kind", "title", "description", "bank",
                "card_type", "percent", "flat_amount", "max_discount", "min_spend", "applies_to_emi", "terms_complete",
                "fetched_at", "expires_at"]
        with self.db.connection() as conn, conn.transaction():
            for o in offers:
                conn.execute(f"insert into vendor_bank_offers ({', '.join(cols)}) values "
                             f"({', '.join('%(' + c + ')s' for c in cols)})", {c: o.get(c) for c in cols})
        return len(offers)

    def bank_offers_at(self, vendor_source_id: int, fetched_at: datetime) -> list[dict]:
        return self._all("select * from vendor_bank_offers where vendor_source_id = %s and fetched_at = %s order by id",
                         (vendor_source_id, fetched_at))

    def insert_emi_offers(self, offers: list[dict]) -> list[int]:
        ids = []
        cols = ["product_id", "vendor_id", "vendor_source_id", "product_price", "principal_amount", "down_payment",
                "financed_amount", "interest_rate", "interest_rate_type", "tenure_months", "monthly_emi",
                "processing_fee", "processing_fee_gst", "other_fees", "upfront_discount", "cashback", "total_interest",
                "total_repayment", "total_cost", "no_cost_emi_claim", "bank_name", "card_type", "payment_provider",
                "eligibility_requirements", "offer_valid_until", "raw_text", "assumptions", "source_url",
                "fetched_at", "expires_at", "verification_status"]
        with self.db.connection() as conn, conn.transaction():
            for o in offers:
                vals = {c: o.get(c) for c in cols}
                vals["assumptions"] = _json(o.get("assumptions") or [])
                vals["no_cost_emi_claim"] = bool(o.get("no_cost_emi_claim"))
                row = conn.execute(
                    f"insert into emi_offers ({', '.join(cols)}) values ({', '.join('%(' + c + ')s' for c in cols)}) returning id",
                    vals).fetchone()
                ids.append(row["id"])
        return ids

    def emi_offers_at(self, vendor_source_id: int, fetched_at: datetime) -> list[dict]:
        return self._all("select * from emi_offers where vendor_source_id = %s and fetched_at = %s order by id",
                         (vendor_source_id, fetched_at))

    def insert_reviews(self, rows: list[dict]) -> int:
        cols = ["product_id", "vendor_id", "vendor_source_id", "review_kind", "review_source_url", "rating",
                "rating_scale", "review_count", "review_text", "review_date", "verified_purchase", "sentiment",
                "aspect_labels", "fetched_at", "expires_at", "verification_status"]
        with self.db.connection() as conn, conn.transaction():
            for r in rows:
                vals = {c: r.get(c) for c in cols}
                vals["aspect_labels"] = _json(r.get("aspect_labels") or {})
                vals["verification_status"] = r.get("verification_status") or "extracted_from_page"
                conn.execute(f"insert into vendor_reviews ({', '.join(cols)}) values "
                             f"({', '.join('%(' + c + ')s' for c in cols)})", vals)
        return len(rows)

    def reviews_at(self, vendor_source_id: int, fetched_at: datetime) -> list[dict]:
        return self._all("select * from vendor_reviews where vendor_source_id = %s and fetched_at = %s order by id",
                         (vendor_source_id, fetched_at))

    # ------------------------------------------------------------------ seller discovery
    def vendor_sources_for_discovery(self, product_id: str, *, origins: tuple[str, ...]) -> list[dict]:
        return self._all(
            """select vs.*, v.name as vendor_name, v.website_domain from product_vendor_sources vs
               join vendors v on v.id = vs.vendor_id
               where vs.product_id = %s and vs.is_active and vs.origin = any(%s) order by vs.id""",
            (product_id, list(origins)))

    def vendor_source_by_url(self, product_id: str, url: str) -> dict | None:
        return self._one("select * from product_vendor_sources where product_id = %s and source_url = %s order by id limit 1",
                         (product_id, url))

    def add_discovered_vendor_source(self, product_id: str, vendor_id: str, url: str, *, adapter_name: str, origin: str,
                                     in_stock_at: datetime | None) -> int:
        """Insert (or re-activate) a vendor source found by discovery. A URL that is already in the registry keeps
        origin 'registry'."""
        return self._one(
            """insert into product_vendor_sources (product_id, vendor_id, source_url, adapter_name, origin,
                                                   last_checked_at, last_in_stock_at)
               values (%(p)s, %(v)s, %(u)s, %(a)s, %(o)s, now(), %(st)s)
               on conflict (product_id, vendor_id, source_url) do update set is_active = true,
                   deactivated_at = null, deactivation_reason = null, last_checked_at = now(),
                   last_in_stock_at = coalesce(excluded.last_in_stock_at, product_vendor_sources.last_in_stock_at),
                   origin = case when product_vendor_sources.origin = 'registry' then 'registry' else excluded.origin end
               returning id""", {"p": product_id, "v": vendor_id, "u": url, "a": adapter_name, "o": origin, "st": in_stock_at})["id"]

    def mark_vendor_source_checked(self, vendor_source_id: int, *, in_stock: bool, at: datetime | None = None) -> None:
        self._one(
            """update product_vendor_sources set last_checked_at = %(at)s,
                   last_in_stock_at = case when %(st)s then %(at)s else last_in_stock_at end
               where id = %(id)s returning id""", {"at": at or _now(), "st": in_stock, "id": vendor_source_id})

    def upsert_seller(self, vendor_source_id: int, product_id: str, s: dict, observed_at: datetime) -> int:
        """Seen on this check. Being in stock moves last_in_stock_at and re-activates a deactivated seller."""
        return self._one(
            """insert into vendor_sellers (vendor_source_id, product_id, seller_key, seller_name, seller_url,
                                           first_seen_at, last_seen_at, last_in_stock_at)
               values (%(vs)s, %(p)s, %(k)s, %(n)s, %(u)s, %(t)s, %(t)s, case when %(st)s then %(t)s end)
               on conflict (vendor_source_id, seller_key) do update set
                   seller_name = coalesce(excluded.seller_name, vendor_sellers.seller_name),
                   seller_url = coalesce(excluded.seller_url, vendor_sellers.seller_url),
                   last_seen_at = greatest(vendor_sellers.last_seen_at, excluded.last_seen_at),
                   last_in_stock_at = case when %(st)s then excluded.last_seen_at else vendor_sellers.last_in_stock_at end,
                   is_active = vendor_sellers.is_active or %(st)s,
                   deactivated_at = case when %(st)s then null else vendor_sellers.deactivated_at end,
                   deactivation_reason = case when %(st)s then null else vendor_sellers.deactivation_reason end
               returning id""",
            {"vs": vendor_source_id, "p": product_id, "k": s["seller_key"], "n": s.get("seller_name"),
             "u": s.get("seller_url"), "t": observed_at, "st": bool(s.get("in_stock"))})["id"]

    def insert_seller_offer(self, seller_id: int, observed_at: datetime, s: dict) -> bool:
        row = self._one(
            """insert into vendor_seller_offers (seller_id, observed_at, price, mrp, currency, in_stock, condition,
                   fulfilled_by, min_quantity, seller_rating, seller_rating_count, seller_positive_pct, delivery_text)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               on conflict (seller_id, observed_at) do nothing returning id""",
            (seller_id, observed_at, s.get("price"), s.get("mrp"), s.get("currency"), bool(s.get("in_stock")),
             s.get("condition"), s.get("fulfilled_by"), s.get("min_quantity"), s.get("seller_rating"),
             s.get("seller_rating_count"), s.get("seller_positive_pct"), (s.get("delivery_text") or "")[:300] or None))
        return row is not None

    def list_sellers(self, product_id: str, *, include_inactive: bool = False) -> list[dict]:
        return self._all(
            f"""select s.*, v.name as marketplace, vs.source_url as marketplace_url,
                       o.observed_at, o.price, o.mrp, o.currency, o.in_stock, o.condition, o.fulfilled_by, o.min_quantity,
                       o.seller_rating, o.seller_rating_count, o.seller_positive_pct, o.delivery_text
                from vendor_sellers s
                join product_vendor_sources vs on vs.id = s.vendor_source_id join vendors v on v.id = vs.vendor_id
                left join lateral (select * from vendor_seller_offers o where o.seller_id = s.id
                                   order by o.observed_at desc limit 1) o on true
                where s.product_id = %s {'' if include_inactive else 'and s.is_active'}
                order by s.is_active desc, o.in_stock desc nulls last, o.price nulls last, s.id""", (product_id,))

    def seller_offer_history(self, seller_id: int, limit: int = 200) -> list[dict]:
        return self._all("select * from vendor_seller_offers where seller_id = %s order by observed_at desc limit %s",
                         (seller_id, limit))

    def deactivate_not_selling(self, days: float, now: datetime | None = None) -> dict:
        """Discovered sellers / sources with nothing in stock for `days` are switched off (kept, never deleted)."""
        params = {"cut": (now or _now()) - timedelta(days=days), "why": f"not in stock for {days:g} days"}
        with self.db.connection() as conn:
            sellers = conn.execute(
                """update vendor_sellers set is_active = false, deactivated_at = now(), deactivation_reason = %(why)s
                   where is_active and coalesce(last_in_stock_at, first_seen_at) < %(cut)s returning id, product_id""",
                params).fetchall()
            sources = conn.execute(
                """update product_vendor_sources set is_active = false, deactivated_at = now(), deactivation_reason = %(why)s
                   where is_active and origin <> 'registry' and coalesce(last_in_stock_at, first_seen_at) < %(cut)s
                   returning id, product_id, source_url""", params).fetchall()
        return {"sellers": sellers, "sources": sources}

    def reserve_search_queries(self, provider: str, n: int, limit: int, now: datetime | None = None) -> bool:
        """Atomically count n queries against this month's limit. False (nothing counted) if they would exceed it."""
        month = (now or _now()).date().replace(day=1)
        row = self._one(
            """insert into search_api_usage (provider, month, queries) select %(p)s, %(m)s, %(n)s where %(n)s <= %(lim)s
               on conflict (provider, month) do update set queries = search_api_usage.queries + excluded.queries,
                   updated_at = now()
                   where search_api_usage.queries + excluded.queries <= %(lim)s
               returning queries""", {"p": provider, "m": month, "n": n, "lim": limit})
        return row is not None

    def search_usage(self, provider: str, now: datetime | None = None) -> int:
        row = self._one("select queries from search_api_usage where provider = %s and month = %s",
                        (provider, (now or _now()).date().replace(day=1)))
        return row["queries"] if row else 0

    def note_candidate(self, product_id: str, url: str, host: str, query: str | None, title: str | None) -> dict:
        return self._one(
            """insert into discovery_candidates (product_id, url, host, search_query, search_title)
               values (%s, %s, %s, %s, %s)
               on conflict (product_id, url) do update set last_found_at = now(),
                   search_query = excluded.search_query, search_title = coalesce(excluded.search_title, discovery_candidates.search_title)
               returning *""", (product_id, url, host, query, (title or "")[:300] or None))

    def update_candidate(self, candidate_id: int, **fields) -> dict | None:
        allowed = {"status", "match_level", "match_evidence", "observed_price", "vendor_source_id", "last_checked_at",
                   "decided_at", "decided_by", "error_message"}
        if not fields or set(fields) - allowed:
            raise ValueError(f"unknown candidate fields {set(fields) - allowed}")
        sets = ", ".join(f"{k} = %({k})s" for k in fields)
        vals = {k: (_json(v) if k == "match_evidence" else v) for k, v in fields.items()}
        return self._one(f"update discovery_candidates set {sets} where id = %(id)s returning *", {**vals, "id": candidate_id})

    def get_candidate(self, candidate_id: int) -> dict | None:
        return self._one("select * from discovery_candidates where id = %s", (candidate_id,))

    def list_candidates(self, *, status: str | None = None, product_id: str | None = None, limit: int = 200) -> list[dict]:
        return self._all(
            """select c.*, vs.is_active as source_active from discovery_candidates c
               left join product_vendor_sources vs on vs.id = c.vendor_source_id
               where (%(st)s::text is null or c.status = %(st)s) and (%(p)s::text is null or c.product_id = %(p)s)
               order by c.last_found_at desc limit %(lim)s""", {"st": status, "p": product_id, "lim": limit})

    # ------------------------------------------------------------------ forecasts / recommendations
    def insert_forecast(self, f: dict) -> int:
        return self._one(
            """insert into forecasts (product_id, horizon_days, forecast_method, predicted_price, lower_price_bound,
                   upper_price_bound, probability_below_target, target_price, data_points_used, model_version,
                   validation_metrics, limitations, base_price, base_timestamp)
               values (%(product_id)s, %(horizon_days)s, %(forecast_method)s, %(predicted_price)s,
                   %(lower_price_bound)s, %(upper_price_bound)s, %(probability_below_target)s, %(target_price)s,
                   %(data_points_used)s, %(model_version)s, %(validation_metrics)s, %(limitations)s,
                   %(base_price)s, %(base_timestamp)s) returning id""",
            {**f, "validation_metrics": _json(f.get("validation_metrics") or {}),
             "limitations": _json(f.get("limitations") or [])})["id"]

    def latest_forecasts(self, product_id: str) -> list[dict]:
        return self._all(
            """select distinct on (horizon_days) * from forecasts where product_id = %s
               order by horizon_days, generated_at desc""", (product_id,))

    def forecasts_due_for_evaluation(self, now: datetime | None = None) -> list[dict]:
        return self._all(
            """select f.* from forecasts f left join prediction_outcomes o on o.forecast_id = f.id
               where o.id is null and f.predicted_price is not null
                 and coalesce(f.base_timestamp, f.generated_at) + make_interval(days => f.horizon_days) <= %s
               order by f.generated_at""",
            (now or _now(),))

    def price_at_or_before(self, product_id: str, ts: datetime) -> dict | None:
        return self._one(
            """select ts, price from v_price_points where product_id = %s and price_type = 'selling_price' and ts <= %s
               order by ts desc limit 1""", (product_id, ts))

    def min_price_between(self, product_id: str, start: datetime, end: datetime) -> Decimal | None:
        row = self._one(
            """select min(price) as m from v_price_points where product_id = %s and price_type = 'selling_price'
               and ts > %s and ts <= %s""", (product_id, start, end))
        return row["m"] if row else None

    def insert_outcome(self, o: dict) -> None:
        self._one(
            """insert into prediction_outcomes (forecast_id, actual_price, forecast_error, within_interval,
                   target_reached, outcome_notes)
               values (%(forecast_id)s, %(actual_price)s, %(forecast_error)s, %(within_interval)s,
                   %(target_reached)s, %(outcome_notes)s)
               on conflict (forecast_id) do nothing returning id""", o)

    def outcome_summary(self, product_id: str | None = None) -> list[dict]:
        return self._all(
            """select f.horizon_days, f.forecast_method, count(*) as n, avg(abs(o.forecast_error)) as mae,
                      avg(case when o.within_interval then 1.0 else 0.0 end) as interval_coverage
               from prediction_outcomes o join forecasts f on f.id = o.forecast_id
               where (%(pid)s::text is null or f.product_id = %(pid)s)
               group by 1, 2 order by 1, 2""", {"pid": product_id})

    def insert_recommendation(self, r: dict) -> int:
        return self._one(
            """insert into purchase_recommendations (product_id, user_id, current_price, target_price,
                   recommended_action, recommended_wait_days, estimated_savings, estimated_savings_range,
                   confidence_level, reasoning_summary, risk_factors, forecast_id, emi_offer_ids,
                   recommendation_version, request)
               values (%(product_id)s, %(user_id)s, %(current_price)s, %(target_price)s, %(recommended_action)s,
                   %(recommended_wait_days)s, %(estimated_savings)s, %(estimated_savings_range)s,
                   %(confidence_level)s, %(reasoning_summary)s, %(risk_factors)s, %(forecast_id)s,
                   %(emi_offer_ids)s, %(recommendation_version)s, %(request)s) returning id""",
            {**r, "estimated_savings_range": _json(r.get("estimated_savings_range")) if r.get("estimated_savings_range") else None,
             "reasoning_summary": _json(r.get("reasoning_summary") or []),
             "risk_factors": _json(r.get("risk_factors") or []), "request": _json(r.get("request") or {}),
             "emi_offer_ids": r.get("emi_offer_ids") or []})["id"]

    def list_recommendations(self, product_id: str, limit: int = 20) -> list[dict]:
        return self._all(
            """select id, generated_at, current_price, target_price, recommended_action, recommended_wait_days,
                      confidence_level, reasoning_summary, risk_factors, recommendation_version
               from purchase_recommendations where product_id = %s order by generated_at desc limit %s""",
            (product_id, limit))

    def latest_recommendation(self, product_id: str, user_id=None) -> dict | None:
        return self._one(
            """select * from purchase_recommendations where product_id = %s and user_id is not distinct from %s
               order by generated_at desc limit 1""", (product_id, user_id))

    # ------------------------------------------------------------------ users / alerts
    def upsert_target(self, user_id, product_id: str, target_price, max_budget, alerts_enabled: bool) -> dict:
        return self._one(
            """insert into user_targets (user_id, product_id, target_price, max_budget, alerts_enabled)
               values (%s, %s, %s, %s, %s)
               on conflict (user_id, product_id) do update set target_price = excluded.target_price,
                   max_budget = excluded.max_budget, alerts_enabled = excluded.alerts_enabled, updated_at = now()
               returning *""", (user_id, product_id, target_price, max_budget, alerts_enabled))

    def targets_for_user(self, user_id) -> list[dict]:
        return self._all("select * from user_targets where user_id = %s order by product_id", (user_id,))

    def targets_for_product(self, product_id: str) -> list[dict]:
        return self._all("select * from user_targets where product_id = %s and alerts_enabled", (product_id,))

    def insert_notification(self, n: dict) -> bool:
        row = self._one(
            """insert into notifications (user_id, product_id, kind, category, message, observed_at, dedupe_key, payload)
               values (%(user_id)s, %(product_id)s, %(kind)s, %(category)s, %(message)s, %(observed_at)s,
                       %(dedupe_key)s, %(payload)s)
               on conflict (dedupe_key) do nothing returning id""",
            {"user_id": None, **n, "payload": _json(n.get("payload") or {})})
        return row is not None

    def recent_notification_exists(self, product_id: str, kind: str, user_id, since: datetime) -> bool:
        row = self._one(
            """select 1 as x from notifications where product_id = %s and kind = %s
               and user_id is not distinct from %s and created_at >= %s limit 1""", (product_id, kind, user_id, since))
        return row is not None

    def list_notifications(self, user_id=None, limit: int = 50) -> list[dict]:
        return self._all(
            """select * from notifications where user_id is null or user_id = %s
               order by created_at desc limit %s""", (user_id, limit))

    # ------------------------------------------------------------------ accounts
    def ensure_profile(self, user_id, email: str | None, *, make_admin: bool = False) -> dict:
        """Create the profile on first sight; refresh email / last_seen_at at most every 5 minutes."""
        row = self._one("select * from profiles where user_id = %s", (user_id,))
        if row and row["email"] == email and (row["role"] == "admin" or not make_admin) \
                and row["last_seen_at"] > _now() - timedelta(minutes=5):
            return row
        return self._one(
            """insert into profiles (user_id, email, role) values (%s, %s, %s)
               on conflict (user_id) do update set email = excluded.email, last_seen_at = now(),
                   role = case when %s then 'admin' else profiles.role end
               returning *""", (user_id, email, "admin" if make_admin else "user", make_admin))

    def get_profile(self, user_id) -> dict | None:
        return self._one("select * from profiles where user_id = %s", (user_id,))

    def update_profile(self, user_id, *, display_name: str | None) -> dict | None:
        return self._one("update profiles set display_name = %s where user_id = %s returning *", (display_name, user_id))

    def set_profile_role(self, user_id, role: str) -> dict | None:
        return self._one("update profiles set role = %s where user_id = %s returning *", (role, user_id))

    def set_profile_disabled(self, user_id, disabled: bool) -> dict | None:
        return self._one("update profiles set is_disabled = %s where user_id = %s returning *", (disabled, user_id))

    def count_admins(self) -> int:
        return self._one("select count(*) as n from profiles where role = 'admin' and not is_disabled")["n"]

    def list_profiles(self, search: str | None = None, limit: int = 100) -> list[dict]:
        return self._all(
            """select p.*,
                      (select count(*) from chat_sessions s where s.user_id = p.user_id and s.deleted_at is null) as chats,
                      (select count(*) from activity_log a where a.user_id = p.user_id) as activities,
                      (select max(a.created_at) from activity_log a where a.user_id = p.user_id) as last_activity_at
               from profiles p
               where %(q)s::text is null or p.email ilike %(like)s or p.display_name ilike %(like)s
               order by p.last_seen_at desc limit %(limit)s""",
            {"q": search, "like": f"%{search}%" if search else None, "limit": limit})

    # ------------------------------------------------------------------ activity history
    def log_activity(self, user_id, kind: str, summary: str, *, product_id: str | None = None,
                     payload: dict | None = None, throttle_minutes: float = 0) -> bool:
        """Append to the user's history. With throttle_minutes, skip if the same kind/product was logged recently."""
        if throttle_minutes and self._one(
                """select 1 as x from activity_log where user_id = %s and kind = %s
                   and product_id is not distinct from %s and created_at >= %s limit 1""",
                (user_id, kind, product_id, _now() - timedelta(minutes=throttle_minutes))):
            return False
        self._one("""insert into activity_log (user_id, kind, product_id, summary, payload)
                     values (%s, %s, %s, %s, %s) returning id""",
                  (user_id, kind, product_id, summary[:500], _json(payload or {})))
        return True

    def list_activity(self, user_id=None, *, kind: str | None = None, limit: int = 50,
                      before_id: int | None = None) -> list[dict]:
        return self._all(
            """select a.*, p.email, p.display_name from activity_log a left join profiles p on p.user_id = a.user_id
               where (%(uid)s::uuid is null or a.user_id = %(uid)s) and (%(kind)s::text is null or a.kind = %(kind)s)
                 and (%(before)s::bigint is null or a.id < %(before)s)
               order by a.id desc limit %(limit)s""",
            {"uid": user_id, "kind": kind, "before": before_id, "limit": limit})

    def activity_stats(self) -> dict:
        row = self._one(
            """select (select count(*) from profiles) as users,
                      (select count(*) from profiles where role = 'admin') as admins,
                      (select count(*) from profiles where is_disabled) as disabled,
                      (select count(distinct user_id) from activity_log where created_at >= now() - interval '1 day') as active_24h,
                      (select count(distinct user_id) from activity_log where created_at >= now() - interval '7 days') as active_7d,
                      (select count(*) from chat_messages m join chat_sessions s on s.id = m.session_id
                        where m.role = 'user' and m.created_at >= now() - interval '7 days') as questions_7d,
                      (select count(*) from chat_messages where role = 'assistant' and status = 'failed') as failed_answers""")
        row["by_kind_7d"] = {r["kind"]: r["n"] for r in self._all(
            """select kind, count(*) as n from activity_log where created_at >= now() - interval '7 days'
               group by kind order by kind""")}
        return row

    # ------------------------------------------------------------------ assistant conversations
    def create_chat_session(self, session_id, user_id, title: str | None = None, product_id: str | None = None) -> dict:
        """Idempotent: re-creating an existing id (a retried request) returns the existing session."""
        self._one("""insert into chat_sessions (id, user_id, title, product_id) values (%s, %s, %s, %s)
                     on conflict (id) do nothing returning id""",
                  (session_id, user_id, (title or "New conversation")[:120], product_id))
        return self._one("select * from chat_sessions where id = %s", (session_id,))

    def get_chat_session(self, session_id) -> dict | None:
        return self._one("select * from chat_sessions where id = %s and deleted_at is null", (session_id,))

    def list_chat_sessions(self, user_id, limit: int = 50) -> list[dict]:
        return self._all(
            """select s.*, (select count(*) from chat_messages m where m.session_id = s.id and m.role = 'user') as questions,
                      (select left(m.content, 160) from chat_messages m where m.session_id = s.id and m.role = 'user'
                       order by m.id desc limit 1) as last_question
               from chat_sessions s where s.user_id = %s and s.deleted_at is null
               order by s.updated_at desc limit %s""", (user_id, limit))

    def rename_chat_session(self, session_id, title: str) -> dict | None:
        return self._one("update chat_sessions set title = %s where id = %s and deleted_at is null returning *",
                         (title[:120], session_id))

    def delete_chat_session(self, session_id) -> None:
        self._one("update chat_sessions set deleted_at = now() where id = %s returning id", (session_id,))

    def chat_messages(self, session_id, limit: int = 500) -> list[dict]:
        return self._all("""select * from (select * from chat_messages where session_id = %s order by id desc limit %s) m
                            order by id""", (session_id, limit))

    def chat_context(self, session_id, before_request_id, limit: int = 12) -> list[dict]:
        """Completed messages earlier in the conversation, oldest first: the assistant's memory of the chat."""
        return self._all(
            """select role, content from (
                   select m.* from chat_messages m where m.session_id = %(s)s and m.status = 'complete'
                   and m.request_id <> %(r)s order by m.id desc limit %(n)s) x order by id""",
            {"s": session_id, "r": before_request_id, "n": limit})

    def begin_chat_turn(self, session_id, request_id, question: str, *, stale_after_seconds: int = 180) -> tuple[dict, bool]:
        """Store the question and claim its answer slot. Returns (assistant_row, claimed).

        claimed=False means the answer already exists or another worker is producing it right now;
        a failed answer, or one left 'pending' longer than stale_after_seconds (crash, restart), is re-claimed."""
        with self.db.connection() as conn, conn.transaction():
            conn.execute("""insert into chat_messages (session_id, request_id, role, content)
                            values (%s, %s, 'user', %s) on conflict do nothing""", (session_id, request_id, question))
            row = conn.execute("""insert into chat_messages (session_id, request_id, role, status)
                                  values (%s, %s, 'assistant', 'pending') on conflict do nothing returning *""",
                               (session_id, request_id)).fetchone()
            if row:
                conn.execute("update chat_sessions set updated_at = now() where id = %s", (session_id,))
                return row, True
            row = conn.execute(
                """update chat_messages set status = 'pending', content = '', updated_at = now()
                   where session_id = %s and request_id = %s and role = 'assistant'
                     and (status = 'failed' or (status = 'pending' and updated_at < now() - make_interval(secs => %s)))
                   returning *""", (session_id, request_id, stale_after_seconds)).fetchone()
            if row:
                return row, True
            return conn.execute("""select * from chat_messages where session_id = %s and request_id = %s
                                   and role = 'assistant'""", (session_id, request_id)).fetchone(), False

    def finish_chat_turn(self, message_id: int, content: str, status: str, meta: dict,
                         product_id: str | None = None) -> dict:
        with self.db.connection() as conn, conn.transaction():
            row = conn.execute("""update chat_messages set content = %s, status = %s, meta = %s, updated_at = now()
                                  where id = %s returning *""", (content, status, _json(meta), message_id)).fetchone()
            conn.execute("""update chat_sessions set updated_at = now(), product_id = coalesce(%s, product_id)
                            where id = %s""", (product_id, row["session_id"]))
            return row
