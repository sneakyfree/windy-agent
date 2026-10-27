"""Where a leased key may go, and how the call is made (vault.call_api design rev 3, G1/G2).

- ``parse_target``: strict https URL parse. Userinfo, fragments, a path or query with characters outside a small
  set (Hub 2026-10-27: a path is shown as sent, percent-encoded and NEVER decoded, so it can't carry readable prose)
  and over-long URLs are refused. The host is lower-cased, IDN-encoded to punycode and its trailing dot stripped.
- ``origin_allowed``: exact origin compare against the item's owner-set ``targets`` (Vault V7.6 c2e; FillTarget
  form: lowercase host, :443 implied, no path, no subdomain wildcard). Empty = refused (fail closed).
- ``send``: resolve once, refuse any non-public address, then CONNECT to that pinned IP with SNI and Host still the
  name (defeats DNS rebinding). No redirects, no proxy env vars, only encodings we can decode (so the redactor
  always sees plain text).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import httpx

MAX_URL = 2048
MAX_BODY = 64 * 1024
TIMEOUT_S = 20.0
_PATH_RE = re.compile(r"/[A-Za-z0-9/._~%-]*")
_QUERY_RE = re.compile(r"[A-Za-z0-9._~%&=+,-]*")
_DECODABLE = frozenset({"", "identity", "gzip", "deflate"})
_EXTRA_BLOCKED = tuple(ipaddress.ip_network(n) for n in ("0.0.0.0/8", "100.64.0.0/10", "fc00::/7"))


class EgressError(Exception):
    """A refused or failed call. ``code`` is one of a fixed set; the message never holds a value."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Target:
    host: str      # punycode, lower-case, no trailing dot
    port: int
    path: str      # exactly as sent (percent-encoded, never decoded)
    query: str

    @property
    def origin(self) -> str:
        return f"https://{self.host}" + ("" if self.port == 443 else f":{self.port}")

    @property
    def host_header(self) -> str:
        return self.host if self.port == 443 else f"{self.host}:{self.port}"


def _norm_host(host: str) -> str:
    h = host.strip().rstrip(".").lower()
    if not h:
        raise EgressError("bad_url")
    try:
        h = h.encode("idna").decode("ascii")
    except UnicodeError:
        raise EgressError("bad_url") from None
    return h


def parse_target(url: str) -> Target:
    if not isinstance(url, str) or len(url) > MAX_URL or any(c.isspace() for c in url):
        raise EgressError("bad_url")
    try:
        parts = urlsplit(url)
        port = parts.port or 443
    except ValueError:
        raise EgressError("bad_url") from None
    if parts.scheme != "https":
        raise EgressError("https_only")
    if "@" in parts.netloc or parts.username is not None or parts.password is not None:
        raise EgressError("userinfo_refused")
    if parts.fragment or not parts.hostname:
        raise EgressError("bad_url")
    path = parts.path or "/"
    if not _PATH_RE.fullmatch(path) or not _QUERY_RE.fullmatch(parts.query):
        raise EgressError("path_characters")
    return Target(host=_norm_host(parts.hostname), port=port, path=path, query=parts.query)


def normalize_origin(origin: str) -> str | None:
    """A FillTarget-form origin, or None when it isn't one (a path, a wildcard, not https)."""
    try:
        parts = urlsplit(origin.strip())
        port = parts.port or 443
    except (ValueError, AttributeError):
        return None
    if (parts.scheme != "https" or not parts.hostname or parts.path not in ("", "/") or parts.query
            or parts.fragment or "@" in parts.netloc or "*" in parts.netloc):
        return None
    try:
        host = _norm_host(parts.hostname)
    except EgressError:
        return None
    return f"https://{host}" + ("" if port == 443 else f":{port}")


def origin_allowed(target: Target, targets: list[str] | None) -> bool:
    allowed = {o for o in (normalize_origin(t) for t in (targets or []) if isinstance(t, str)) if o}
    return target.origin in allowed


def is_public_ip(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return False  # IPv4-mapped IPv6: refused outright
    if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved
            or ip.is_unspecified or any(ip in n for n in _EXTRA_BLOCKED if n.version == ip.version)):
        return False
    return ip.is_global


def resolve_pinned(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise EgressError("dns_failed") from None
    addrs = list(dict.fromkeys(str(i[4][0]).split("%")[0] for i in infos))
    if not addrs:
        raise EgressError("dns_failed")
    if not all(is_public_ip(a) for a in addrs):
        raise EgressError("private_address")  # any non-public answer refuses the whole name
    return addrs[0]


def send(method: str, target: Target, headers: dict[str, str], body: bytes | None = None, *,
         resolver: Callable[[str, int], str] = resolve_pinned,
         transport: httpx.BaseTransport | None = None) -> httpx.Response:
    if body is not None and len(body) > MAX_BODY:
        raise EgressError("body_too_large")
    ip = resolver(target.host, target.port)
    if not is_public_ip(ip):
        raise EgressError("private_address")
    ip_host = f"[{ip}]" if ":" in ip else ip
    url = f"https://{ip_host}:{target.port}{target.path}" + (f"?{target.query}" if target.query else "")
    hdrs = {**headers, "Host": target.host_header, "Accept-Encoding": "gzip, deflate"}
    try:
        with httpx.Client(trust_env=False, follow_redirects=False, timeout=TIMEOUT_S, transport=transport) as c:
            req = c.build_request(method, url, headers=hdrs, content=body,
                                  extensions={"sni_hostname": target.host})
            resp = c.send(req)
            if resp.headers.get("content-encoding", "").strip().lower() not in _DECODABLE:
                resp.close()
                raise EgressError("undecodable_response")
            resp.read()
            return resp
    except httpx.HTTPError:
        raise EgressError("unreachable") from None
