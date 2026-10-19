"""Import price history collected earlier by data/extract_dashboard_timeseries.py.

* import_dashboard_ts: reads the `dashboard_ts` schema (same database) - chart points for the
  dashboard URL - and stores them as historical_price_points (data_origin='imported_dashboard_ts').
  Chart sale bands become sale_events (observed_on_source_chart).
* import_daily_csv: reads a CSV written by `--export-daily` and imports ONLY rows marked
  'recorded'. 'carried forward' rows are not observations and are skipped.
Both are idempotent (unique key on product, source, price type, timestamp).
"""
from __future__ import annotations

import csv
import re
from pathlib import Path

from app.database.repositories import Repository
from app.extraction.base import PricePoint, SaleBand
from app.extraction.validation import InvalidValue, parse_price, parse_source_timestamp, validate_timestamp

DEFAULT_SERIES_MAP = {r"/ Prices$": "selling_price", r"/ Offer Price$": "offer_price"}


def _source_for(repo: Repository, product_id: str, url: str) -> int:
    for s in repo.list_price_sources(product_id):
        if s["source_url"].rstrip("/") == url.rstrip("/"):
            return s["id"]
    raise LookupError(f"{url} is not registered as a price-history source of {product_id}; register it first")


def import_dashboard_ts(repo: Repository, product_id: str, dashboard_url: str,
                        series_map: dict[str, str] | None = None, currency: str = "INR") -> dict:
    source_id = _source_for(repo, product_id, dashboard_url)
    series_map = series_map or DEFAULT_SERIES_MAP
    compiled = [(re.compile(k, re.I), v) for k, v in series_map.items()]
    with repo.db.connection() as conn:
        if conn.execute("select to_regclass('dashboard_ts.points') as t").fetchone()["t"] is None:
            return {"imported": 0, "message": "dashboard_ts schema not found in this database"}
        rows = conn.execute(
            """select s.name, p.x_raw, p.ts, p.value from dashboard_ts.points p
               join dashboard_ts.series s on s.id = p.series_id
               join dashboard_ts.dashboards d on d.id = s.dashboard_id
               where d.url = %s and p.ts is not null and p.value is not null order by s.name, p.ts""",
            (dashboard_url,)).fetchall()
        marker_rows = conn.execute(
            """select m.label, m.ts_from, m.ts_to from dashboard_ts.chart_markers m
               join dashboard_ts.dashboards d on d.id = m.dashboard_id
               where d.url = %s and m.kind = 'band' and m.ts_from is not null and m.label <> ''""",
            (dashboard_url,)).fetchall()

    points, skipped = [], 0
    for r in rows:
        ptype = next((pt for rx, pt in compiled if rx.search(r["name"])), None)
        if ptype is None:
            continue
        try:
            ts = parse_source_timestamp(r["x_raw"]) or r["ts"]
            validate_timestamp(ts)
            price = parse_price(r["value"])
        except InvalidValue:
            skipped += 1
            continue
        if price:
            points.append(PricePoint(timestamp=ts, price=price, price_type=ptype, currency=currency,  # type: ignore[arg-type]
                                     source_label=r["x_raw"]))
    inserted = repo.insert_history_points(product_id, source_id, points, "imported_dashboard_ts")
    # Sale bands are retailer-specific (e.g. Big Billion Days = Flipkart). dashboard_ts does not record the
    # retailer, so bands are only imported once a collection has identified it; the collector adds them anyway.
    obs = repo.latest_observation(product_id)
    product_retailer = obs["seller_name"] if obs else None
    new_events = 0
    if product_retailer:
        bands = [SaleBand(label=m["label"], start=m["ts_from"].date(), end=m["ts_to"].date(), retailer=product_retailer)
                 for m in marker_rows]
        new_events = repo.upsert_sale_bands(bands, dashboard_url)
    return {"product_id": product_id, "source_id": source_id, "candidate_points": len(points), "inserted": inserted,
            "already_present": len(points) - inserted, "rejected": skipped, "sale_events_added": new_events}


def import_daily_csv(repo: Repository, product_id: str, source_url: str, path: Path,
                     price_type: str = "selling_price", currency: str = "INR") -> dict:
    source_id = _source_for(repo, product_id, source_url)
    points, skipped_cf, rejected = [], 0, 0
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("source") != "recorded":
                skipped_cf += 1
                continue
            try:
                ts = parse_source_timestamp(row["date_ist"])
                validate_timestamp(ts)
                price = parse_price(row["price"])
            except (InvalidValue, KeyError):
                rejected += 1
                continue
            if price:
                points.append(PricePoint(timestamp=ts, price=price, price_type=price_type,  # type: ignore[arg-type]
                                         currency=currency, source_label=row["date_ist"]))
    inserted = repo.insert_history_points(product_id, source_id, points, "imported_csv")
    return {"product_id": product_id, "recorded_rows": len(points), "inserted": inserted,
            "carried_forward_rows_skipped": skipped_cf, "rejected": rejected}


def export_daily_csv(repo: Repository, product_id: str, path: Path, price_type: str = "selling_price") -> int:
    rows = repo.daily_prices(product_id, price_type)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["product_id", "price_type", "date_ist", "price", "source", "days_since_recorded"])
        for r in rows:
            w.writerow([product_id, price_type, r["date_ist"].isoformat(), r["price"],
                        "recorded" if r["is_recorded"] else "carried forward", r["days_since_recorded"]])
    return len(rows)

