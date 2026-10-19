#!/usr/bin/env python3
"""Sync config/products.yaml (and with --events, config/sale_events.yaml) into the database.

    python scripts/register_products.py [--events] [--file other.yaml]
"""
import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from app.config import get_settings
from app.database.client import get_database
from app.database.repositories import Repository
from app.services.event_service import sync_events
from app.services.registry_service import sync_registry

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--file", type=Path)
    ap.add_argument("--events", action="store_true", help="also sync sale events")
    a = ap.parse_args()
    s = get_settings()
    repo = Repository(get_database())
    print(json.dumps(sync_registry(repo, a.file or s.products_config), indent=2))
    if a.events:
        print(f"sale events inserted: {sync_events(repo, s.sale_events_config)}")
