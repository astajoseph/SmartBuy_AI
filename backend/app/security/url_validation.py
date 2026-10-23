"""Safe outbound fetching for untrusted / user-registered URLs (SSRF protection).

Rules enforced for every request, including every redirect hop:
  * scheme must be http or https, no user:password@ in the URL, default ports only
  * hostname must be on the allowlist (registered source hosts + EXTRA_ALLOWED_HOSTS)
  * every DNS answer must be a public unicast address (blocks localhost, RFC1918, link-local,
    cloud metadata 169.254.169.254, CGNAT, multicast, reserved, IPv6 ULA ...)
  * the connection is pinned to the IP that was validated (prevents DNS-rebinding between
    the check and the connect); TLS still verifies the certificate for the real hostname
  * response size and total time are capped
  * robots.txt of the target host is honoured for direct fetches
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import random
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

ALLOWED_SCHEMES = {"http", "https"}
DEFAULT_PORTS = {"http": 80, "https": 443}
_METADATA_HOSTS = {"metadata.google.internal", "metadata", "instance-data"}
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")      # RFC 6052 well-known prefix (DNS64 / NAT64 networks)


class UnsafeURLError(ValueError):
    """The URL must not be fetched."""


class RobotsDisallowed(PermissionError):
    """robots.txt of the host disallows this path for our user agent."""


class FetchError(RuntimeError):
    def __init__(self, message: str, status: int | None = None, transient: bool = False):
        super().__init__(message)
        self.status = status
        self.transient = transient


def validate_url_syntax(url: str) -> httpx.URL:
    if not isinstance(url, str) or len(url) > 2048:
        raise UnsafeURLError("URL missing or too long")
    try:
        parts = urlsplit(url.strip())
    except ValueError as e:
        raise UnsafeURLError(f"Unparseable URL: {e}") from e
    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError("Only http and https URLs are allowed")
    if parts.username or parts.password:
        raise UnsafeURLError("Credentials in URLs are not allowed")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host:
        raise UnsafeURLError("URL has no hostname")
    try:
        port = parts.port
    except ValueError as e:
        raise UnsafeURLError("Invalid port") from e
    if port is not None and port != DEFAULT_PORTS[parts.scheme.lower()]:
        raise UnsafeURLError("Non-standard ports are not allowed")
    if host in _METADATA_HOSTS or host == "localhost" or host.endswith(".localhost") or host.endswith(".internal"):
        raise UnsafeURLError("Internal hostnames are not allowed")
    try:
        ipaddress.ip_address(host.strip("[]"))
        is_literal_ip = True
    except ValueError:
        is_literal_ip = False
    if is_literal_ip and not is_public_ip(host.strip("[]")):
        raise UnsafeURLError("Private, loopback and link-local addresses are not allowed")
    return httpx.URL(url.strip())


def is_public_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    elif isinstance(addr, ipaddress.IPv6Address) and addr in _NAT64:
        addr = ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)   # DNS64 answer: judge the IPv4 address it translates to
    return addr.is_global and not (
        addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_multicast
        or addr.is_reserved or addr.is_unspecified
    )


def host_matches_allowlist(host: str, allowlist: set[str]) -> bool:
    host = host.lower().rstrip(".")
    return any(host == a or host.endswith("." + a) for a in allowlist)


def resolve_public_ips(host: str) -> list[str]:
    """Resolve host and require every answer to be public. Literal IPs are checked directly."""
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchError(f"DNS resolution failed for {host}", transient=True) from e
        ips = sorted({info[4][0] for info in infos})
    if not ips:
        raise FetchError(f"No addresses for {host}", transient=True)
    bad = [ip for ip in ips if not is_public_ip(ip)]
    if bad:
        raise UnsafeURLError(f"{host} resolves to a non-public address")
    return ips


def check_url(url: str, allowlist: set[str]) -> tuple[httpx.URL, list[str]]:
    parsed = validate_url_syntax(url)
    if not host_matches_allowlist(parsed.host, allowlist):
        raise UnsafeURLError(f"Host {parsed.host} is not on the allowlist of registered sources")
    return parsed, resolve_public_ips(parsed.host)


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    text: str
    fetched_at: float = field(default_factory=time.time)
    content: bytes = b""             # raw body (images); `text` is the same body decoded
    elapsed_seconds: float = 0.0


class DomainRateLimiter:
    """At most one request per `min_interval` seconds per registrable host."""

    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, host: str) -> None:
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            delay = self._last.get(host, 0) + self.min_interval - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last[host] = time.monotonic()


class SafeFetcher:
    """httpx-based fetcher applying all checks above. One instance per worker / request scope."""

    def __init__(self, allowlist: set[str], *, respect_robots: bool = True,
                 rate_limiter: DomainRateLimiter | None = None, transport: httpx.AsyncBaseTransport | None = None,
                 resolver=resolve_public_ips, pin_ip: bool = True):
        s = get_settings()
        self.allowlist = {h.lower() for h in allowlist} | s.extra_allowed_host_set
        self.respect_robots = respect_robots
        self.rate_limiter = rate_limiter or DomainRateLimiter(s.per_domain_min_interval_seconds)
        self.timeout = s.http_timeout_seconds
        self.max_bytes = s.max_response_bytes
        self.max_redirects = s.max_redirects
        self.max_retries = s.collection_max_retries
        self.user_agent = s.user_agent
        self._transport = transport
        self._resolver = resolver
        self._pin_ip = pin_ip and transport is None
        self._robots: dict[str, RobotFileParser | None] = {}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=self.timeout, follow_redirects=False, transport=self._transport,
            headers={"User-Agent": self.user_agent, "Accept-Language": "en-IN,en;q=0.8"},
        )

    def _check(self, url: str) -> tuple[httpx.URL, list[str]]:
        parsed = validate_url_syntax(url)
        if not host_matches_allowlist(parsed.host, self.allowlist):
            raise UnsafeURLError(f"Host {parsed.host} is not on the allowlist of registered sources")
        return parsed, self._resolver(parsed.host)

    async def _send(self, client: httpx.AsyncClient, url: httpx.URL, ips: list[str]) -> httpx.Response:
        if self._pin_ip:
            ip = ips[0]
            host_for_url = f"[{ip}]" if ":" in ip else ip
            pinned = url.copy_with(host=host_for_url)
            req = client.build_request("GET", pinned, headers={"Host": url.netloc.decode()},
                                       extensions={"sni_hostname": url.host})
        else:
            req = client.build_request("GET", url)
        resp = await client.send(req, stream=True)
        try:
            chunks, total = [], 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > self.max_bytes:
                    raise FetchError(f"Response larger than {self.max_bytes} bytes")
                chunks.append(chunk)
            resp._content = b"".join(chunks)  # noqa: SLF001 - buffered body after size check
        finally:
            await resp.aclose()
        return resp

    def robots_for(self, url: str) -> RobotFileParser | None:
        """Parsed robots.txt for the URL's host, if robots_allowed() already loaded it."""
        p = httpx.URL(url)
        return self._robots.get(f"{p.scheme}://{p.host}")

    async def robots_allowed(self, url: httpx.URL) -> bool:
        if not self.respect_robots:
            return True
        key = f"{url.scheme}://{url.host}"
        if key not in self._robots:
            rp: RobotFileParser | None = RobotFileParser()
            try:
                res = await self._fetch_once(f"{key}/robots.txt", check_robots=False)
                if res.status == 200:
                    rp.parse(res.text.splitlines())
                elif res.status in (401, 403):
                    rp.parse(["User-agent: *", "Disallow: /"])
                else:
                    rp = None  # no robots.txt -> allowed
            except FetchError:
                rp = None
            self._robots[key] = rp
        rp = self._robots[key]
        return True if rp is None else rp.can_fetch(self.user_agent, str(url))

    async def _fetch_once(self, url: str, *, check_robots: bool = True) -> FetchResult:
        started = time.monotonic()
        current = url
        async with self._client() as client:
            for _hop in range(self.max_redirects + 1):
                parsed, ips = self._check(current)
                if check_robots and not await self.robots_allowed(parsed):
                    raise RobotsDisallowed(f"robots.txt disallows {parsed.path} on {parsed.host}")
                await self.rate_limiter.wait(parsed.host)
                try:
                    resp = await self._send(client, parsed, ips)
                except httpx.TimeoutException as e:
                    raise FetchError(f"Timeout fetching {parsed.host}", transient=True) from e
                except httpx.TransportError as e:
                    raise FetchError(f"Network error fetching {parsed.host}: {e.__class__.__name__}", transient=True) from e
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise FetchError("Redirect without Location header", status=resp.status_code)
                    current = str(parsed.join(location))   # re-validated at the top of the loop
                    continue
                if time.monotonic() - started > self.timeout * 3:
                    raise FetchError("Total fetch time exceeded", transient=True)
                return FetchResult(
                    url=url, final_url=str(parsed), status=resp.status_code,
                    headers={k.lower(): v for k, v in resp.headers.items()},
                    text=resp.content.decode(resp.encoding or "utf-8", errors="replace"), content=resp.content,
                    elapsed_seconds=time.monotonic() - started,
                )
        raise FetchError("Too many redirects")

    async def fetch(self, url: str) -> FetchResult:
        """GET with retries (exponential backoff + jitter) for transient errors, 429 and 5xx."""
        attempt = 0
        while True:
            try:
                res = await self._fetch_once(url)
                if res.status == 429 or res.status >= 500:
                    raise FetchError(f"HTTP {res.status}", status=res.status, transient=True)
                if res.status >= 400:
                    raise FetchError(f"HTTP {res.status}", status=res.status, transient=False)
                res.retries = attempt  # type: ignore[attr-defined]
                return res
            except FetchError as e:
                if not e.transient or attempt >= self.max_retries:
                    raise
                attempt += 1
                await asyncio.sleep(min(60.0, (2 ** attempt)) * (0.5 + random.random() / 2))


def browser_request_allowed(url: str) -> bool:
    """Guard for every sub-request a headless browser makes (scripts, XHR, images...).
    Third-party hosts are allowed (pages need their CDNs) but never non-public addresses."""
    try:
        parsed = validate_url_syntax(url)
        resolve_public_ips(parsed.host)
        return True
    except (UnsafeURLError, FetchError):
        return url.startswith(("data:", "blob:"))


def normalize_url(url: str) -> str:
    p = urlsplit(url.strip())
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path or "/", p.query, ""))
