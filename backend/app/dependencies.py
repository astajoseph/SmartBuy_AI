"""FastAPI dependencies: repository access and a small per-client rate limiter for endpoints that may
trigger on-demand vendor fetches (ordinary users must not be able to drive unrestricted scraping).

No `from __future__ import annotations` here: FastAPI must see the real `Request` type on RateLimiter.__call__."""

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request, status

from app.database.client import get_database
from app.database.repositories import Repository


def get_repo() -> Repository:
    return Repository(get_database())


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float):
        self.max_calls, self.per = max_calls, per_seconds
        self.calls: dict[str, deque] = defaultdict(deque)

    def __call__(self, request: Request) -> None:
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        q = self.calls[key]
        while q and now - q[0] > self.per:
            q.popleft()
        if len(q) >= self.max_calls:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many vendor-data requests; try again shortly.")
        q.append(now)


vendor_rate_limit = RateLimiter(max_calls=10, per_seconds=600)
chat_rate_limit = RateLimiter(max_calls=30, per_seconds=600)
submit_rate_limit = RateLimiter(max_calls=5, per_seconds=600)
