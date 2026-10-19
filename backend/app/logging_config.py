"""Structured logging with secret redaction."""
import json
import logging
import re
import sys
from datetime import datetime, timezone

_SECRET_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+@"), r"\1***@"),                      # DSN passwords
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password)[\"']?\s*[=:]\s*[\"']?)[^\s\"',}]+"), r"\1***"),
    (re.compile(r"AIza[0-9A-Za-z_\-]{20,}"), "***"),                                           # Google API keys
]


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ("product_id", "source_id", "job_id", "url", "adapter"):
            if hasattr(record, key):
                payload[key] = redact(str(getattr(record, key)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for noisy in ("httpx", "httpcore", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
