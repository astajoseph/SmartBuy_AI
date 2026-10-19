"""Helpers shared by adapters: turning rendered charts into validated price points and sale bands."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.extraction.base import ExtractionResult, PricePoint, SaleBand
from app.extraction.validation import InvalidValue, parse_price, parse_source_timestamp, validate_timestamp


def history_from_charts(result: ExtractionResult, charts: list[dict], series_map: dict[str, str],
                        currency: str) -> None:
    """series_map: {regex on 'chart / series name': price_type}. First matching regex wins.
    Null y values are chart gaps and are skipped (never stored as 0)."""
    compiled = [(re.compile(rx, re.I), pt) for rx, pt in series_map.items()]
    seen: set[tuple[str, datetime]] = set()
    rejected = 0
    for c in charts:
        full_name = f"{c.get('chart')} / {c.get('name')}"
        price_type = next((pt for rx, pt in compiled if rx.search(full_name)), None)
        if price_type is None:
            continue
        for x, y in zip(c.get("x") or [], c.get("y") or []):
            if y is None:
                continue
            try:
                ts = parse_source_timestamp(x)
                if ts is None:
                    rejected += 1
                    continue
                validate_timestamp(ts)
                price = parse_price(y)
            except InvalidValue:
                rejected += 1
                continue
            if price is None or (price_type, ts) in seen:
                continue
            seen.add((price_type, ts))
            result.history.append(PricePoint(timestamp=ts, price=price, price_type=price_type,  # type: ignore[arg-type]
                                             currency=currency, source_label=str(x)))
    if rejected:
        result.warnings.append(f"{rejected} chart values were rejected by validation (unparseable date or price)")


def sale_bands_from_markers(result: ExtractionResult, markers: list[dict], retailer: str | None) -> None:
    """Shaded x-axis bands with a label (e.g. 'Big Billion Days') drawn by the source chart."""
    seen = set()
    for m in markers:
        if m.get("kind") != "band" or not str(m.get("axis", "")).lower().startswith("x"):
            continue
        label = re.sub(r"<[^>]+>", "", str(m.get("label") or "")).strip()
        if not label:
            continue
        start = _marker_dt(m.get("from"))
        end = _marker_dt(m.get("to"))
        if not start or not end:
            continue
        # bands are drawn as 00:00:01 .. 23:59:59 UTC of the sale days
        key = (label, start.date(), end.date())
        if key in seen:
            continue
        seen.add(key)
        result.sale_bands.append(SaleBand(label=label, start=start.date(), end=end.date(), retailer=retailer))


def _marker_dt(v) -> datetime | None:
    if isinstance(v, (int, float)):
        x = float(v)
        if 1e12 <= x < 4.2e12:
            return datetime.fromtimestamp(x / 1000, tz=timezone.utc)
        if 1e9 <= x < 4.2e9:
            return datetime.fromtimestamp(x, tz=timezone.utc)
        return None
    ts = parse_source_timestamp(v)
    return ts


def looks_blocked(status: int | None, text: str) -> bool:
    if status in (401, 403, 429, 503):
        return True
    low = text[:20000].lower()
    return any(s in low for s in ("captcha", "robot check", "are you a human", "access denied", "unusual traffic"))
