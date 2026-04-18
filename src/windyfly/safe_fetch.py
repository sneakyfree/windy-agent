"""SSRF-safe HTTP fetcher for user-supplied URLs.

Policy:
  - http:// and https:// only
  - resolve hostname; every resolved A/AAAA must be a public unicast
    address. Loopback, RFC1918, link-local, CGNAT, IPv6 ULA all blocked.
  - redirects are NOT auto-followed. If the caller opts into one
    redirect, the Location URL is re-validated through the same rules.
  - default timeout is short; the caller can raise it but not disable.

Used by /web slash command and any other site that fetches URLs
supplied by a remote caller.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


# Never resolve or reach these ranges for user-supplied URLs. The
# rationale for each is in the comment — when changing this list, say
# why.
_FORBIDDEN_V4 = [
    ipaddress.ip_network("0.0.0.0/8"),        # "this network" (source-only)
    ipaddress.ip_network("10.0.0.0/8"),       # RFC1918 private
    ipaddress.ip_network("100.64.0.0/10"),    # CGNAT — shared with ISP
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("169.254.0.0/16"),   # link-local, EC2 IMDS lives here
    ipaddress.ip_network("172.16.0.0/12"),    # RFC1918 private
    ipaddress.ip_network("192.0.0.0/24"),     # IANA reserved
    ipaddress.ip_network("192.168.0.0/16"),   # RFC1918 private
    ipaddress.ip_network("198.18.0.0/15"),    # benchmark
    ipaddress.ip_network("224.0.0.0/4"),      # multicast
    ipaddress.ip_network("240.0.0.0/4"),      # reserved
    ipaddress.ip_network("255.255.255.255/32"),  # broadcast
]
_FORBIDDEN_V6 = [
    ipaddress.ip_network("::1/128"),          # loopback
    ipaddress.ip_network("::/128"),           # unspecified
    ipaddress.ip_network("::ffff:0:0/96"),    # IPv4-mapped (catches v4 loopback via v6)
    ipaddress.ip_network("fc00::/7"),         # ULA (site-local)
    ipaddress.ip_network("fe80::/10"),        # link-local
    ipaddress.ip_network("ff00::/8"),         # multicast
]


class SSRFBlocked(Exception):
    """Raised when safe_fetch refuses a URL."""


@dataclass
class FetchResult:
    status_code: int
    url: str
    text: str
    length: int


def _is_blocked_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # unparseable → refuse
    forbid = _FORBIDDEN_V6 if isinstance(ip, ipaddress.IPv6Address) else _FORBIDDEN_V4
    for net in forbid:
        if ip in net:
            return True
    # Also refuse globally-unreachable unicast addresses that don't
    # fit the explicit lists above.
    return not ip.is_global


def _validate_url(url: str) -> tuple[str, str]:
    """Return (scheme, host) or raise SSRFBlocked."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlocked(f"scheme {parsed.scheme!r} not allowed (http/https only)")
    if not parsed.hostname:
        raise SSRFBlocked("URL has no hostname")

    host = parsed.hostname
    # Resolve ALL A/AAAA — not just one. A DNS rebinding attack
    # points one record at a public IP and another at 127.0.0.1; we
    # refuse if any record is on the blocklist.
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise SSRFBlocked(f"DNS resolution failed: {exc}") from exc

    for family, _type, _proto, _canon, sockaddr in infos:
        # sockaddr is (host_str, port) for AF_INET, (host, port, flow, scope)
        # for AF_INET6 — first element is always the IP string in both cases.
        # getaddrinfo's signature types this as tuple[Any, ...] which mypy
        # narrows to str | int; the runtime contract is tighter than the stub.
        ip_raw = sockaddr[0]
        if not isinstance(ip_raw, str):
            # Defensive — shouldn't happen given the tuple layouts above.
            continue
        if _is_blocked_ip(ip_raw):
            raise SSRFBlocked(f"host {host} resolves to blocked address {ip_raw}")

    return parsed.scheme, host


def safe_fetch(
    url: str,
    *,
    timeout: float = 10.0,
    max_bytes: int = 2_000_000,
    allow_one_redirect: bool = False,
) -> FetchResult:
    """SSRF-safe GET.

    Raises SSRFBlocked when the URL (or any redirect target) points at
    a non-public address. Never auto-follows redirects — the caller
    opts in with `allow_one_redirect=True`, and the redirect target
    goes through the same validation before the second fetch.
    """
    if timeout <= 0 or timeout > 60:
        raise ValueError("timeout must be in (0, 60]")

    _validate_url(url)
    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        resp = client.get(url)
        if resp.status_code in (301, 302, 303, 307, 308) and allow_one_redirect:
            loc = resp.headers.get("Location", "")
            if not loc:
                raise SSRFBlocked("redirect without Location header")
            _validate_url(loc)
            resp = client.get(loc)

    text = resp.text
    if len(text) > max_bytes:
        text = text[:max_bytes]
    return FetchResult(
        status_code=resp.status_code,
        url=str(resp.url),
        text=text,
        length=len(resp.text),
    )
