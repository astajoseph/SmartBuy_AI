import asyncio
import re
from pathlib import Path

import httpx
import pytest

from app.logging_config import redact
from app.security.url_validation import (SafeFetcher, UnsafeURLError, browser_request_allowed, check_url, is_public_ip,
                                         validate_url_syntax)

ALLOW = {"shop.example"}


@pytest.mark.parametrize("url", ["http://localhost/x", "http://127.0.0.1/", "http://[::1]/", "http://10.0.0.5/",
                                 "http://192.168.1.1/", "http://169.254.169.254/latest/meta-data/",
                                 "http://metadata.google.internal/", "file:///etc/passwd", "ftp://shop.example/",
                                 "http://user:pw@shop.example/", "http://shop.example:8080/", "gopher://shop.example/"])
def test_blocked_urls(url):
    with pytest.raises(UnsafeURLError):
        check_url(url, ALLOW | {"localhost", "127.0.0.1", "::1", "10.0.0.5", "192.168.1.1", "169.254.169.254",
                                "metadata.google.internal"})


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.1", "169.254.169.254", "100.64.0.1",
                                "0.0.0.0", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "224.0.0.1"])
def test_private_addresses_are_not_public(ip):
    assert not is_public_ip(ip)


def test_public_address_ok():
    assert is_public_ip("8.8.8.8")


def test_host_not_on_allowlist_is_rejected():
    with pytest.raises(UnsafeURLError):
        check_url("https://evil.example/", ALLOW)


def test_dns_answer_pointing_inside_is_rejected():
    def resolver(host):
        from app.security.url_validation import resolve_public_ips
        return resolve_public_ips("127.0.0.1")

    f = SafeFetcher(ALLOW, respect_robots=False, resolver=resolver,
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, text="ok")))
    with pytest.raises(UnsafeURLError):
        asyncio.run(f.fetch("https://shop.example/item"))


def test_redirect_to_private_address_is_blocked():
    def handler(request):
        if request.url.host == "shop.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
        return httpx.Response(200, text="SECRET")

    f = SafeFetcher(ALLOW | {"169.254.169.254"}, respect_robots=False, resolver=lambda h: ["93.184.216.34"] if h == "shop.example" else __import__("app.security.url_validation", fromlist=["x"]).resolve_public_ips(h),
                    transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeURLError):
        asyncio.run(f.fetch("https://shop.example/item"))


def test_redirect_to_unlisted_host_is_blocked():
    def handler(request):
        return httpx.Response(301, headers={"location": "https://elsewhere.example/"})

    f = SafeFetcher(ALLOW, respect_robots=False, resolver=lambda h: ["93.184.216.34"], transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeURLError):
        asyncio.run(f.fetch("https://shop.example/item"))


def test_robots_disallow_is_enforced():
    from app.security.url_validation import RobotsDisallowed

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api/\n")
        return httpx.Response(200, text="data")

    f = SafeFetcher(ALLOW, resolver=lambda h: ["93.184.216.34"], transport=httpx.MockTransport(handler))
    assert asyncio.run(f.fetch("https://shop.example/p/1")).text == "data"
    with pytest.raises(RobotsDisallowed):
        asyncio.run(f.fetch("https://shop.example/api/price/1"))


def test_response_size_limit():
    from app.security.url_validation import FetchError

    f = SafeFetcher(ALLOW, respect_robots=False, resolver=lambda h: ["93.184.216.34"],
                    transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 6_000_000)))
    with pytest.raises(FetchError):
        asyncio.run(f.fetch("https://shop.example/big"))


def test_browser_guard_blocks_internal_subrequests():
    assert not browser_request_allowed("http://127.0.0.1:8000/admin")
    assert not browser_request_allowed("file:///etc/passwd")
    assert browser_request_allowed("data:image/png;base64,AAAA")


def test_url_syntax_ok():
    assert validate_url_syntax("https://pricehistory.app/p/x").host == "pricehistory.app"


def test_secrets_are_redacted_from_logs():
    s = redact("connect postgresql://postgres.abc:p%40ss@host:5432/db with GEMINI_API_KEY=AIzaSyA12345678901234567890123")
    assert "p%40ss" not in s and "AIzaSy" not in s


def test_frontend_never_references_server_secrets():
    root = Path(__file__).resolve().parents[2] / "frontend"
    if not root.exists():
        pytest.skip("frontend not present")
    pattern = re.compile(r"SERVICE_ROLE|GEMINI_API_KEY|DATABASE_URL|ADMIN_API_TOKEN|SUPABASE_JWT_SECRET")
    offenders = [p for p in root.rglob("*") if p.is_file() and p.suffix in {".ts", ".tsx", ".js", ".mjs", ".json"}
                 and "node_modules" not in p.parts and ".next" not in p.parts and pattern.search(p.read_text(errors="ignore"))]
    assert offenders == []


def test_admin_endpoints_reject_missing_and_wrong_tokens():
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    for method, path in (("get", "/api/admin/jobs"), ("post", "/api/admin/collect/x"), ("post", "/api/sources/test"),
                         ("get", "/api/admin/sources/health"), ("post", "/api/admin/refresh-vendor-data/x")):
        assert getattr(c, method)(path).status_code == 401
        assert getattr(c, method)(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_admin_disabled_when_token_unset(monkeypatch):
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    monkeypatch.setattr(get_settings(), "admin_api_token", None)
    assert TestClient(app).get("/api/admin/jobs", headers={"Authorization": "Bearer "}).status_code in (401, 403)
    assert TestClient(app).get("/api/admin/jobs", headers={"Authorization": "Bearer anything"}).status_code == 403
