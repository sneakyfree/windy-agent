"""The egress gateway FRAME (strand gene D3.1; Vault plan V7.6, rules 1 and L3). DARK.

A generic HTTP tool names a connection and a path; the gateway makes the upstream HTTPS call
itself and adds the credential from a Vault lease it holds in memory. No MITM, no CA: the
tool never holds a token, and a lease never leaves this object.

This is the frame only. It is not registered as a tool and nothing calls it unless
``WINDY_EGRESS=1``; the Vault lease route (``LeaseProvider``) and the catalog files are wired
later (after the Vault plan's contracts merge and Hub's go-live gate), so tests use fakes.

What it enforces by construction: provider entry must exist and be enabled; exact catalog
hostnames only; https and port 443 only; path shape checks; public addresses only with the
resolved address pinned (SNI and Host stay the real name, so TLS verification is on); no
redirect is ever followed; caller headers are a small allow-list; the lease is registered with
the by-value redactor; a response that contains a lease value or a secret shape is blocked;
a 401 on a leased credential re-leases once; every call passes the provider limiter.
"""

from __future__ import annotations

import base64
import os
import re
import socket
import urllib.parse
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from windyfly.vault import egress_guard, redactor
from windyfly.vault.limiter import Limited, ProviderLimiter

ENV_FLAG = "WINDY_EGRESS"
METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
SAFE_REQUEST_HEADERS = frozenset({"accept", "content-type", "if-none-match", "if-match", "user-agent"})
SAFE_RESPONSE_HEADERS = frozenset({"content-type", "etag", "retry-after", "x-ratelimit-remaining",
                                   "x-ratelimit-reset", "link", "location"})
MAX_RESPONSE_BYTES = 1_048_576
# High-confidence secret shapes ONLY (a broad scan would block READMEs and docs that merely mention auth).
_SECRET_SHAPE_RE = re.compile(
    r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}|\bsk-[A-Za-z0-9_-]{24,}"
    r"|\bxox[abprs]-[A-Za-z0-9-]{20,}|\bAKIA[0-9A-Z]{16}\b|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/\-]*$")


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "").strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class ProviderEntry:
    """The slice of a Vault catalog entry the gateway needs (the catalog file is the source of truth)."""

    id: str
    hosts: tuple[str, ...]
    auth_style: str = "bearer"          # bearer | header | basic | query
    auth_name: str = ""                 # header name or query parameter name
    enabled: bool = False
    private_source: bool = False


@dataclass
class Lease:
    value: str
    expires_at: float = 0.0
    host: str = ""                      # the lease is bound to one host


class LeaseProvider(Protocol):
    def get(self, connection_id: str, *, force: bool = False) -> Lease: ...


class GatewayDenied(Exception):
    def __init__(self, code: str, say: str) -> None:
        super().__init__(code)
        self.code, self.say = code, say


@dataclass
class Gateway:
    entries: dict[str, ProviderEntry]
    leases: LeaseProvider
    resolver: Callable[..., list] | None = None
    transport: httpx.BaseTransport | None = None
    limiters: dict[str, ProviderLimiter] = field(default_factory=dict)
    clock: Callable[[], float] | None = None
    max_bytes: int = MAX_RESPONSE_BYTES

    # ── validation ──────────────────────────────────────────────
    def _entry(self, provider_id: str) -> ProviderEntry:
        e = self.entries.get(provider_id)
        if e is None or not e.enabled:
            raise GatewayDenied("provider_unavailable", "That account isn't set up for me yet.")
        return e

    @staticmethod
    def _path(path: str) -> str:
        if not isinstance(path, str) or not path.startswith("/") or ".." in path.split("/") \
                or "//" in path or len(path) > 2048 or not _PATH_RE.match(path):
            raise GatewayDenied("bad_path", "I can't use that address.")
        return path

    @staticmethod
    def _query(query: dict[str, Any] | None) -> str:
        if not query:
            return ""
        return urllib.parse.urlencode({str(k): str(v) for k, v in query.items()}, doseq=False)

    # ── the call ────────────────────────────────────────────────
    def call(self, connection_id: str, provider_id: str, method: str, path: str, *,
             host: str | None = None, query: dict[str, Any] | None = None,
             body: bytes | str | None = None, headers: dict[str, str] | None = None,
             timeout_s: float = 20.0) -> dict[str, Any]:
        """One upstream call. Returns {ok, status, headers, body, private_source} or raises
        GatewayDenied / egress_guard.EgressDenied / Limited (each with a plain sentence)."""
        if not enabled():
            raise GatewayDenied("egress_off", "I can't reach your accounts from here yet.")
        entry = self._entry(provider_id)
        m = method.upper()
        if m not in METHODS:
            raise GatewayDenied("bad_method", "I can't make that kind of request.")
        use_host = (host or entry.hosts[0]).lower()
        if use_host not in {h.lower() for h in entry.hosts}:
            raise egress_guard.EgressDenied("host_not_allowed", "That site isn't one of the accounts you connected.")
        path = self._path(path)
        q = self._query(query)
        url = f"https://{use_host}{path}" + (f"?{q}" if q and entry.auth_style != "query" else "")

        lim = self.limiters.setdefault(provider_id + ":" + connection_id, ProviderLimiter())
        lim.acquire()

        target = egress_guard.target_for(url, entry.hosts, resolver=self.resolver or socket.getaddrinfo)
        resp = self._send(entry, connection_id, m, target, q, body, headers or {}, timeout_s, lease_force=False)
        if resp.status_code == 401:                       # a revoked or expired lease: one re-lease
            resp = self._send(entry, connection_id, m, target, q, body, headers or {}, timeout_s, lease_force=True)
        lim.on_response(resp.status_code, dict(resp.headers))
        return self._result(entry, resp)

    def _send(self, entry: ProviderEntry, connection_id: str, method: str, target: egress_guard.Target,
              q: str, body: bytes | str | None, caller_headers: dict[str, str], timeout_s: float,
              *, lease_force: bool) -> httpx.Response:
        lease = self.leases.get(connection_id, force=lease_force)
        if lease.host and lease.host.lower() != target.host:
            raise GatewayDenied("lease_host_mismatch", "That access isn't valid for that site.")
        redactor.register(lease.value)
        hdrs = {k: v for k, v in caller_headers.items() if k.lower() in SAFE_REQUEST_HEADERS}
        hdrs["Host"] = target.host
        pq = target.path_query
        if entry.auth_style == "bearer":
            hdrs["Authorization"] = f"Bearer {lease.value}"
        elif entry.auth_style == "header":
            hdrs[entry.auth_name or "Authorization"] = lease.value
        elif entry.auth_style == "basic":
            hdrs["Authorization"] = "Basic " + base64.b64encode(lease.value.encode()).decode()
        elif entry.auth_style == "query":
            sep = "&" if "?" in pq else "?"
            extra = urllib.parse.urlencode({entry.auth_name or "key": lease.value})
            pq = f"{pq}{sep}{extra}" + (f"&{q}" if q else "")
        else:
            raise GatewayDenied("auth_style_unsupported", "I can't use that kind of account yet.")
        ip = f"[{target.ip}]" if ":" in target.ip else target.ip
        with httpx.Client(transport=self.transport, follow_redirects=False, verify=True, timeout=timeout_s) as c:
            return c.request(method, f"https://{ip}:{target.port}{pq}", headers=hdrs,
                             content=body if body is not None else None,
                             extensions={"sni_hostname": target.host})

    def _result(self, entry: ProviderEntry, resp: httpx.Response) -> dict[str, Any]:
        if 300 <= resp.status_code < 400:
            raise egress_guard.EgressDenied("redirect_refused", "That account sent me somewhere else, so I stopped.")
        raw = resp.content[: self.max_bytes + 1]
        if len(raw) > self.max_bytes:
            raise GatewayDenied("response_too_large", "That answer was too big for me to use.")
        text = raw.decode("utf-8", errors="replace")
        if redactor.contains(text) or _SECRET_SHAPE_RE.search(text):
            raise GatewayDenied("secret_in_response", "That answer contained a secret, so I didn't use it.")
        return {"ok": resp.status_code < 400, "status": resp.status_code,
                "headers": {k.lower(): v for k, v in resp.headers.items() if k.lower() in SAFE_RESPONSE_HEADERS},
                "body": text, "private_source": entry.private_source}


__all__ = ["Gateway", "GatewayDenied", "Lease", "LeaseProvider", "Limited", "ProviderEntry", "enabled"]
