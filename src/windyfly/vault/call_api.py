"""vault.call_api: one https call with a key the owner shared, never shown to the model (Vault everywhere item 3).

Design: docs/VAULT_CALL_API_DESIGN.md rev 4; contract: windy-contracts schema/agent/vault-tools.v1.json (codes, words,
limits); Vault agent-routes 1.5.0 (lease.targets). Order, so nothing is leased that can't be used:
  1. arguments and the target URL (egress.parse_target: https only, no userinfo, no dot-segments, safe characters);
  2. the item is one the owner shared (capabilities), else not_shared;
  3. a GET spends the per-item budget (30 calls / hour / item / agent), else too_many_calls;
  4. lease the item; its owner-set targets must hold the request's exact origin (empty = refused);
  5. the value must be usable: no control characters (ASCII only when it goes in a header as-is), at least MIN_SECRET characters (whole-form redaction can't guard less);
  6. a WRITE (anything but GET) stops here: it answers needs_owner_ok with a hash-bound confirm id, so the owner is
     only ever asked about a call that could run (G4 a: the host is already in lease.targets). The owner's confirm
     screen (R16) comes with Hub/Mobile; until then a write simply waits and never runs;
  7. send through the pinned egress; scrub the decoded body and allow-listed headers of every form of the value;
  8. release the lease in finally, always.
Returns a dict with a contract code (no words: the tool adds them) or the CallResult / NeedsOwnerOk shape.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from windyfly.vault import client as vc
from windyfly.vault import egress
from windyfly.vault.redact import Redactor, SecretTooShort

logger = logging.getLogger(__name__)

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
AUTHS = ("bearer", "header", "basic")
BUDGET_PER_HOUR = 30
TO_MODEL_MAX = 64 * 1024
CONFIRM_TTL_S = 600
HEADER_ALLOW = ("content-type", "content-length", "content-language", "etag", "last-modified", "retry-after",
                "x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset", "x-request-id")
# vault-tools.v1 x-mapping vault.call_api: a lease refused 404 not_found / 403 needs_owner / grant_expired = not shared.
# Every other lease refusal propagates and the tool maps it like any vault.* answer (vault_locked, rate_limited, ...).
# The mapping's "non-leasable class -> key_unusable" can't arise here: an item lease names no class (Vault, 10-28).
_NOT_SHARED = frozenset({"not_found", "needs_owner", "grant_expired"})
FORBIDDEN_HEADERS = frozenset({"host", "content-length", "transfer-encoding", "connection", "cookie",
                               "proxy-authorization", "te", "upgrade", "accept-encoding"})
_QUERY_KEY = re.compile(r"[A-Za-z0-9._~-]{1,64}")
_QUERY_VALUE = re.compile(r"[A-Za-z0-9._~%+,-]{0,512}")
_EGRESS_CODE = {"private_address": "blocked_address", "body_too_large": "request_too_large",
                "https_only": "bad_url", "userinfo_refused": "bad_url", "bad_url": "bad_url",
                "path_characters": "bad_url", "dot_segments": "bad_url"}


def host_of(url: Any) -> str:
    """The parsed host for the audit row (host + method only), or '?'. Never the path, query or body."""
    try:
        return egress.parse_target(str(url)).host
    except Exception:  # noqa: BLE001
        return "?"


def _state(name: str) -> Path:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / name


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _budget_wait(item_id: str, now: float) -> int:
    """Record one call for the item when it fits BUDGET_PER_HOUR (calls in the last hour) and return 0; otherwise
    record nothing and return the seconds until the oldest call leaves the hour (1..3600)."""
    path = _state("vault_call_budget.json")
    data = _load(path)
    recent = sorted(t for t in data.get(item_id, []) if isinstance(t, (int, float)) and 0 <= now - t < 3600)
    if len(recent) >= BUDGET_PER_HOUR:
        return min(3600, max(1, int(recent[-BUDGET_PER_HOUR] + 3600 - now) + 1))
    data[item_id] = recent + [now]
    _save(path, {k: v for k, v in data.items() if v})
    return 0


def _with_query(url: str, query: dict[str, Any] | None) -> str:
    if not query:
        return url
    if not isinstance(query, dict) or len(query) > 32:
        raise ValueError("bad_arguments")
    parts = []
    for k, v in query.items():
        # CallApiArgs: names and values are already URL-safe (a value may carry its own %-escapes): sent verbatim.
        if not (isinstance(k, str) and isinstance(v, str) and _QUERY_KEY.fullmatch(k) and _QUERY_VALUE.fullmatch(v)):
            raise ValueError("bad_arguments")
        parts.append(f"{k}={v}")
    return url + ("&" if "?" in url else "?") + "&".join(parts)


def _usable(value: str, auth: str) -> bool:
    """No control character anywhere (CR/LF would split a header); a value sent as-is in a header (bearer, header)
    must also be plain ASCII, since a header can't carry anything else. Basic sends it base64'd, so any text goes."""
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        return False
    return auth == "basic" or value.isascii()


def _request_hash(item_id: str, method: str, target: egress.Target, body: bytes | None) -> str:
    h = hashlib.sha256()
    for part in (item_id, method, target.origin, target.path, target.query):
        h.update(part.encode() + b"\0")
    h.update(body or b"")
    return h.hexdigest()


def _needs_owner_ok(req_hash: str, now: float) -> dict[str, Any]:
    path = _state("vault_call_confirms.json")
    data = {k: v for k, v in _load(path).items() if isinstance(v, dict) and v.get("expires_at", 0) > now}
    cid = "cnf_" + secrets.token_urlsafe(18)
    data[cid] = {"req_hash": req_hash, "expires_at": int(now + CONFIRM_TTL_S)}
    _save(path, data)
    return {"ok": False, "needs_owner_ok": True, "confirm_id": cid}


def call(*, item_id: str, method: str, url: str, auth: str, header_name: str | None = None,
         username: str | None = None, query: dict[str, Any] | None = None, body: Any = None,
         now: Callable[[], float] = time.time, transport: httpx.BaseTransport | None = None,
         resolver: Callable[[str, int], str] = egress.resolve_pinned) -> dict[str, Any]:
    # 1. arguments and target
    if method not in METHODS or auth not in AUTHS:
        return {"ok": False, "code": "bad_arguments"}
    if (auth == "header") != bool(header_name) or (auth == "basic") != bool(username):
        return {"ok": False, "code": "bad_arguments"}
    if header_name is not None and (not isinstance(header_name, str) or header_name.lower() in FORBIDDEN_HEADERS):
        return {"ok": False, "code": "bad_arguments"}
    if method == "GET" and body is not None:
        return {"ok": False, "code": "bad_arguments"}
    try:
        target = egress.parse_target(_with_query(url, query))
    except ValueError:
        return {"ok": False, "code": "bad_arguments"}
    except egress.EgressError as e:
        return {"ok": False, "code": _EGRESS_CODE.get(e.code, "bad_url")}
    data = None
    if body is not None:
        try:
            data = json.dumps(body, separators=(",", ":"), allow_nan=False).encode()
        except (TypeError, ValueError):
            return {"ok": False, "code": "bad_arguments"}
        if len(data) > egress.MAX_BODY:
            return {"ok": False, "code": "request_too_large"}
    # 2. shared with this agent
    shared = vc.shared()  # VaultError propagates: the tool maps it like every vault.* refusal
    if not any(i["item_id"] == item_id for i in shared["items"]):
        return {"ok": False, "code": "not_shared"}
    # 3. budget (a write spends it when it runs, after the owner's yes)
    t = now()
    if method == "GET" and (wait := _budget_wait(item_id, t)):
        return {"ok": False, "code": "too_many_calls", "retry_after_s": wait}
    # 4. lease
    try:
        lease = vc.lease(item_id)
    except vc.VaultError as e:
        if e.code in _NOT_SHARED or e.status == 404:
            return {"ok": False, "code": "not_shared"}
        raise
    try:
        if not egress.origin_allowed(target, list(lease.targets)):
            return {"ok": False, "code": "host_not_allowed"}
        value = lease.value
        if not _usable(value, auth):
            return {"ok": False, "code": "key_unusable"}
        red = Redactor()
        try:
            red.register(value, username=username if auth == "basic" else None)
        except SecretTooShort:
            return {"ok": False, "code": "key_unusable"}
        # 6. writes ask the owner first (DELETE always; POST/PUT/PATCH by default)
        if method != "GET":
            return _needs_owner_ok(_request_hash(item_id, method, target, data), t)
        headers: dict[str, str] = {"User-Agent": "WindyFly-vault-call"}
        if auth == "bearer":
            headers["Authorization"] = f"Bearer {value}"
        elif auth == "header":
            headers[str(header_name)] = value
        else:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{username}:{value}".encode()).decode()
        if data is not None:
            headers["Content-Type"] = "application/json"
        # 7. send and scrub
        try:
            resp, raw = egress.send(method, target, headers, data, resolver=resolver, transport=transport)
        except egress.EgressError as e:
            return {"ok": False, "code": _EGRESS_CODE.get(e.code, "call_failed")}
        text = red.scrub(raw.decode("utf-8", errors="replace"))
        cut = text.encode("utf-8")[:TO_MODEL_MAX].decode("utf-8", errors="ignore")
        kept = {k.lower(): red.scrub(v) for k, v in resp.headers.items() if k.lower() in HEADER_ALLOW}
        logger.info("vault.call_api: %s %s -> %d", method, target.host, resp.status_code)  # host+method+status
        return {"ok": True, "status": resp.status_code, "headers": kept, "body": cut, "truncated": cut != text}
    finally:
        # 8. always give the key back
        try:
            vc.release(lease.lease_id)
        except vc.VaultError as e:
            logger.info("vault.call_api: release refused (%s); the lease expires on its own", e.code)
