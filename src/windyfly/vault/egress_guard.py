"""Where the gateway may connect (strand gene D3.2; Vault plan rule 1).

Exact hostnames only (no wildcards, no IP literals), https only, port 443 unless the catalog
says otherwise, no userinfo, public addresses only (a name that resolves to a private,
loopback, link-local, metadata or carrier-grade-NAT address is refused), the resolved address
is PINNED for the call (DNS rebinding), and a redirect may only stay on the same host.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass

_HOST_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


class EgressDenied(Exception):
    """A refused destination. ``code`` is a closed enum; ``say`` is one plain sentence."""

    def __init__(self, code: str, say: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.say = say or "I can't reach that address from here."


@dataclass(frozen=True)
class Target:
    host: str
    port: int
    ip: str          # the pinned, public address
    path_query: str  # "/path?query"


def valid_hostname(host: str) -> bool:
    h = (host or "").strip().lower()
    if not h or h.startswith("*") or "*" in h or h != host.strip():
        return False
    try:
        ipaddress.ip_address(h.strip("[]"))
        return False                       # IP literals are never allowed
    except ValueError:
        pass
    return bool(_HOST_RE.match(h))


def is_public_ip(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped is not None:
        a = a.ipv4_mapped
    return a.is_global and not a.is_multicast


def check_url(url: str, allowed_hosts: tuple[str, ...] | list[str], *, port: int = 443) -> urllib.parse.SplitResult:
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https":
        raise EgressDenied("https_only", "I only talk to accounts over a secure connection.")
    if p.username or p.password or "@" in p.netloc:
        raise EgressDenied("userinfo_refused")
    host = (p.hostname or "").lower()
    if not valid_hostname(host) or host not in {h.lower() for h in allowed_hosts}:
        raise EgressDenied("host_not_allowed", "That site isn't one of the accounts you connected.")
    if (p.port or 443) != port:
        raise EgressDenied("port_not_allowed")
    return p


def resolve_public(host: str, port: int = 443,
                   resolver: Callable[..., list] = socket.getaddrinfo) -> str:
    """Resolve once and return a public address to pin; refuse if ANY answer is not public."""
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise EgressDenied("dns_failed", "I couldn't look that address up just now.") from exc
    ips = [i[4][0] for i in infos]
    if not ips:
        raise EgressDenied("dns_failed")
    if not all(is_public_ip(ip) for ip in ips):
        raise EgressDenied("private_address")
    return ips[0]


def target_for(url: str, allowed_hosts: tuple[str, ...] | list[str], *, port: int = 443,
               resolver: Callable[..., list] = socket.getaddrinfo) -> Target:
    p = check_url(url, allowed_hosts, port=port)
    ip = resolve_public(p.hostname or "", port, resolver)
    pq = (p.path or "/") + (f"?{p.query}" if p.query else "")
    return Target(host=(p.hostname or "").lower(), port=port, ip=ip, path_query=pq)


def check_redirect(from_url: str, to_url: str) -> None:
    """Redirects are never followed to another host; the caller strips credentials either way."""
    a, b = urllib.parse.urlsplit(from_url), urllib.parse.urlsplit(to_url)
    if b.scheme != "https" or (b.hostname or "").lower() != (a.hostname or "").lower() \
            or (b.port or 443) != (a.port or 443):
        raise EgressDenied("redirect_refused", "That account sent me somewhere else, so I stopped.")
