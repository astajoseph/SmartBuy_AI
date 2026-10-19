"""Product registry: config/products.yaml -> database.

The YAML file is the documented way to add products, price-history URLs and vendor URLs without
code changes (the admin API offers the same through HTTP). Syncing is idempotent.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, Field, field_validator

from app.database.repositories import Repository
from app.extraction.registry import ADAPTERS
from app.security.url_validation import validate_url_syntax

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,63}$")


class PriceSourceConfig(BaseModel):
    url: str
    adapter: str = "auto"
    name: str | None = None
    collection_interval_minutes: int = Field(default=360, ge=30)
    enabled: bool = True
    adapter_config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        validate_url_syntax(v)
        return v.strip()

    @field_validator("adapter")
    @classmethod
    def _adapter(cls, v: str) -> str:
        if v != "auto" and (v not in ADAPTERS or ADAPTERS[v].kind != "price_history"):
            raise ValueError(f"unknown price_history adapter {v!r}")
        return v


class VendorConfig(BaseModel):
    vendor_id: str
    name: str
    url: str
    adapter: str = "vendor_generic"
    expected_vendor_product_id: str | None = None
    enabled: bool = True

    @field_validator("vendor_id")
    @classmethod
    def _vid(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("vendor_id must be 3-64 chars of a-z, 0-9, _ or -")
        return v

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        validate_url_syntax(v)
        return v.strip()

    @field_validator("adapter")
    @classmethod
    def _adapter(cls, v: str) -> str:
        if v not in ADAPTERS or ADAPTERS[v].kind != "vendor":
            raise ValueError(f"unknown vendor adapter {v!r}")
        return v


class ProductConfig(BaseModel):
    product_id: str
    name: str
    brand: str | None = None
    model: str | None = None
    category: str | None = None
    currency: str = "INR"
    description: str | None = None
    enabled: bool = True
    expected_attributes: dict[str, Any] = Field(default_factory=dict)
    price_source: PriceSourceConfig
    vendors: list[VendorConfig] = Field(default_factory=list)

    @field_validator("product_id")
    @classmethod
    def _pid(cls, v: str) -> str:
        if not _ID.match(v):
            raise ValueError("product_id must be 3-64 chars of a-z, 0-9, _ or -")
        return v


class RegistryFile(BaseModel):
    products: list[ProductConfig]


def load_registry(path: Path) -> RegistryFile:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    reg = RegistryFile.model_validate(data)
    ids = [p.product_id for p in reg.products]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate product_id in registry")
    return reg


def sync_product(repo: Repository, p: ProductConfig) -> dict:
    repo.upsert_product({"id": p.product_id, "name": p.name, "brand": p.brand, "model": p.model,
                         "category": p.category, "currency": p.currency, "description": p.description,
                         "expected_attributes": p.expected_attributes, "is_active": p.enabled})
    sid = repo.upsert_price_source(p.product_id, p.price_source.url, adapter_name=p.price_source.adapter,
                                   adapter_config=p.price_source.adapter_config,
                                   interval_minutes=p.price_source.collection_interval_minutes,
                                   source_name=p.price_source.name or urlsplit(p.price_source.url).hostname,
                                   is_active=p.price_source.enabled)
    vendor_ids = []
    for v in p.vendors:
        repo.upsert_vendor(v.vendor_id, v.name, (urlsplit(v.url).hostname or "").lower())
        vendor_ids.append(repo.upsert_vendor_source(p.product_id, v.vendor_id, v.url, adapter_name=v.adapter,
                                                    expected_vendor_product_id=v.expected_vendor_product_id,
                                                    is_active=v.enabled))
    return {"product_id": p.product_id, "price_source_id": sid, "vendor_source_ids": vendor_ids}


def sync_registry(repo: Repository, path: Path) -> list[dict]:
    return [sync_product(repo, p) for p in load_registry(path).products]
