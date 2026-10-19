"""Parsing and validation of prices, currencies and timestamps coming from web pages."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MIN_VALID_TS = datetime(2000, 1, 1, tzinfo=timezone.utc)
MAX_PRICE = Decimal("100000000")  # ₹10 crore - anything above is treated as a parse error

_CURRENCY_SYMBOLS = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "$": "USD", "usd": "USD", "€": "EUR", "£": "GBP"}
_DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class InvalidValue(ValueError):
    pass


def parse_price(value) -> Decimal | None:
    """'₹1,050.00' / '1050' / 1050 -> Decimal('1050.00'). Returns None when absent; raises on garbage."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            raise InvalidValue("non-finite price")
        d = Decimal(str(value))
    else:
        s = str(value).strip()
        if not s or s in {"-", "--", "N/A", "NA"}:
            return None
        s = re.sub(r"(?i)(₹|rs\.?|inr|mrp:?)", "", s).replace(",", "").replace(" ", "").strip()
        m = re.fullmatch(r"\d+(?:\.\d+)?", s)
        if not m:
            raise InvalidValue(f"not a price: {value!r}")
        try:
            d = Decimal(s)
        except InvalidOperation as e:
            raise InvalidValue(f"not a price: {value!r}") from e
    if d <= 0:
        raise InvalidValue(f"price must be positive, got {d}")
    if d > MAX_PRICE:
        raise InvalidValue(f"implausible price {d}")
    return d.quantize(Decimal("0.01"))


def normalize_currency(value: str | None, default: str | None = None) -> str | None:
    if not value:
        return default
    v = value.strip()
    if re.fullmatch(r"[A-Z]{3}", v):
        return v
    return _CURRENCY_SYMBOLS.get(v.lower(), default)


def parse_source_timestamp(raw) -> datetime | None:
    """Timestamp from a chart / feed. Date-only values are calendar dates in India and become
    00:00 IST. Epoch numbers and ISO strings without zone are UTC (as in the vendored detector)."""
    from app.extraction.timeseries_detect import to_iso

    if raw is None:
        return None
    if isinstance(raw, str) and _DATE_ONLY.match(raw.strip()):
        d = date.fromisoformat(raw.strip())
        return datetime.combine(d, time(0, 0), IST).astimezone(timezone.utc)
    iso = to_iso(raw)
    if iso is None:
        return None
    return datetime.fromisoformat(iso)


def validate_timestamp(ts: datetime, now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    if ts.tzinfo is None:
        raise InvalidValue("timestamp without timezone")
    if ts < MIN_VALID_TS:
        raise InvalidValue(f"timestamp before 2000: {ts}")
    if ts > now + timedelta(days=1):
        raise InvalidValue(f"timestamp in the future: {ts}")
    return ts


def ist_date(ts: datetime) -> date:
    return ts.astimezone(IST).date()


def retailer_key(name: str | None) -> str:
    """'www.Flipkart.com' / 'flipkart.com' / 'Flipkart' -> 'flipkart'. Empty -> 'any'."""
    if not name:
        return "any"
    n = name.strip().lower()
    n = re.sub(r"^https?://", "", n)
    n = re.sub(r"^www\.", "", n)
    n = re.sub(r"\.(com|in|co\.in)(/.*)?$", "", n)
    return n or "any"
