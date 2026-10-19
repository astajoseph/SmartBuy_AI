"""Sale-event registry: config/sale_events.yaml -> sale_events table, plus queries."""
from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml
from pydantic import BaseModel, field_validator, model_validator

from app.database.repositories import Repository
from app.extraction.validation import retailer_key

STATUSES = {"confirmed_official", "reported_by_media", "observed_on_source_chart", "calendar_date",
            "recurring_unconfirmed", "hypothetical"}


class SaleEventConfig(BaseModel):
    event_name: str
    retailer: str = "any"
    category: str = "any"
    region: str = "IN"
    event_type: str
    start_date: date | None = None
    end_date: date | None = None
    source_url: str | None = None
    verification_status: str
    notes: str | None = None
    typical_months: list[int] | None = None   # for recurring_unconfirmed entries (documentation only)

    @field_validator("verification_status")
    @classmethod
    def _status(cls, v: str) -> str:
        if v not in STATUSES:
            raise ValueError(f"verification_status must be one of {sorted(STATUSES)}")
        return v

    @model_validator(mode="after")
    def _consistency(self):
        if self.verification_status in ("confirmed_official", "reported_by_media") and not self.source_url:
            raise ValueError(f"{self.event_name}: {self.verification_status} events need a source_url")
        if self.verification_status == "recurring_unconfirmed" and self.start_date:
            raise ValueError(f"{self.event_name}: recurring_unconfirmed events must not carry exact dates")
        return self


def load_events(path: Path) -> list[SaleEventConfig]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return [SaleEventConfig.model_validate(e) for e in data.get("events", [])]


def sync_events(repo: Repository, path: Path) -> int:
    n = 0
    for e in load_events(path):
        d = e.model_dump(exclude={"typical_months"})
        d["retailer"] = retailer_key(d["retailer"])
        if e.typical_months:
            d["notes"] = ((d.get("notes") or "") + f" Typical months: {e.typical_months}.").strip()
        n += repo.upsert_sale_event(d)
    return n


def events_for_product(repo: Repository, product: dict, retailer: str | None) -> list[dict]:
    rows = repo.list_sale_events()
    cat = (product.get("category") or "any").lower()
    out = []
    for r in rows:
        if retailer and r["retailer"] not in ("any", retailer_key(retailer)):
            continue
        if r["category"] not in ("any", cat):
            continue
        out.append(r)
    return out
