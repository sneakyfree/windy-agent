"""The agent side of Windy Vault's agent routes (windy-contracts schema/vault/agent-routes.v1.json). DARK.

Off unless WINDY_VAULT_LEASE=1 AND WINDY_VAULT_API_URL is set (no default: nothing here can reach a real
Vault by accident). Every request is mode B for aud windy-vault (service_auth: EPT+agent + a DPoP proof).

Flow after the agent is born (vault-setup.v1, windy-contracts #169: the Birth Canal stores each birth login
in the OWNER's Vault and grants it to the agent):
  1. ``register_lease_key()``  PUT  /v1/agent/lease-key   (the agent's X25519 lease key, vault/lease_key.py)
  2. ``capabilities()``        GET  /v1/agent/capabilities (what the owner has shared: grant, item, label)
  3. ``lease(item_id)``        POST /v1/agent/lease        (a ~15-min lease; the value is a JWE to our key,
                                                            decrypted HERE; Vault never sees it in clear again)
  4. ``release(lease_id)``     POST /v1/agent/lease/{id}/release

A value is never logged, never cached on disk and never put in an exception message. An item the owner did
not share is simply not listed (Vault 10-21): there is no "not ready" state on this side.
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Any

import httpx

from windyfly.agent import service_auth
from windyfly.vault import lease_jwe, lease_key

logger = logging.getLogger(__name__)

ENV_FLAG = "WINDY_VAULT_LEASE"
ENV_URL = "WINDY_VAULT_API_URL"
AUD = "windy-vault"
_TIMEOUT_S = 15.0
_LEASE_ID_RE = re.compile(r"lse_[A-Za-z0-9_-]+")  # used with fullmatch: no trailing-newline loophole
_REQUEST_ID_RE = re.compile(r"req_[A-Za-z0-9_-]{1,64}")  # fullmatch too: it goes into a URL path
_ITEM_ID_RE = re.compile(r"itm_[A-Za-z0-9_-]{1,64}")
# agent-routes.v1 requests.create: ENUMS ONLY (v3.0 KEEP 5, the prompt-injection defence). No free text, no label.
ITEM_TYPES = ("api_key", "token", "password", "ssh_private_key", "wireguard_key", "note",
              "github_app_installation", "cloudflare_token")
PROVIDERS = ("github", "cloudflare", "other")
_ACCESS_STATES = frozenset({"pending", "approved", "denied", "expired"})
_SAVE_STATES = frozenset({"pending", "saved", "declined", "expired"})
# vault-api answers EVERY agent-auth failure with one code, 401 {"error": "unauthenticated"} (no oracle; Windy
# Vault 10-21, workers/vault-api/src/index.ts:130-151). A blocked passport gets it too, so re-mint ONCE, never loop.
REMINT_CODES = frozenset({"unauthenticated"})


class VaultError(Exception):
    """A Vault call failed. ``code`` is Vault's error code (or unavailable / bad_response / http_<n>)."""

    def __init__(self, code: str, status: int = 0, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code, self.status, self.retry_after = code, status, retry_after


@dataclass(frozen=True)
class Lease:
    lease_id: str
    item_id: str
    value: str
    expires_at: int
    # agent-routes 1.5.0 (V7.6 c2e): the item's OWNER-set https origins, normalized; empty = call_api refuses it.
    targets: tuple[str, ...] = ()

    def __repr__(self) -> str:  # never print the value
        return f"Lease(lease_id={self.lease_id!r}, item_id={self.item_id!r}, expires_at={self.expires_at})"


def enabled() -> bool:
    return os.environ.get(ENV_FLAG, "") == "1" and bool(os.environ.get(ENV_URL, "").strip())


def _base() -> str:
    return os.environ.get(ENV_URL, "").strip().rstrip("/")


def _call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    if not enabled():
        raise VaultError("unavailable")
    url = f"{_base()}{path}"
    for attempt in (1, 2):
        try:
            headers = service_auth.agent_headers(AUD, method, url)
        except service_auth.ServiceAuthError as exc:
            raise VaultError(exc.code or "no_agent_token") from None
        if body is not None:
            headers["Content-Type"] = "application/json"
        try:
            resp = httpx.request(method, url, headers=headers, json=body, timeout=_TIMEOUT_S)
        except httpx.HTTPError as exc:
            raise VaultError("unavailable") from exc
        code = _error_code(resp)
        if resp.status_code == 401 and attempt == 1 and code in REMINT_CODES:
            service_auth.forget_token()  # one fresh mint + a fresh proof, then give up
            continue
        if not 200 <= resp.status_code < 300:
            # 503 vault_unavailable carries retry_after_s: the caller backs off that long (no re-mint).
            raise VaultError(code or f"http_{resp.status_code}", resp.status_code, _retry_after_s(resp))
        try:
            return resp.json()
        except ValueError:
            raise VaultError("bad_response", resp.status_code) from None
    raise VaultError("refused", 401)


def _retry_after_s(resp: httpx.Response) -> float | None:
    try:
        data = resp.json()
        value = data.get("retry_after_s") if isinstance(data, dict) else None
        # A hostile body could send 1e300 (or inf): anything but a whole number of seconds in 1..3600 is absent.
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return float(int(value)) if 1 <= value <= 3600 else None
    except ValueError:
        return None


def _error_code(resp: httpx.Response) -> str:
    try:
        data = resp.json()
    except ValueError:
        return ""
    return str(data.get("error") or "") if isinstance(data, dict) else ""


def register_lease_key() -> str:
    """Register (or re-register) the active lease key; returns Vault's kid for it."""
    def put(body: dict[str, str]) -> tuple[int, Any]:
        try:
            return 200, _call("PUT", "/v1/agent/lease-key", body)
        except VaultError as e:
            return e.status or 503, {}
    return lease_key.register(put)


def capabilities() -> list[dict[str, Any]]:
    """What the owner has shared with this agent: [{grant_id, item_id, label, expires_at}]. Labels are the
    OWNER's words: show them, never act on them."""
    return shared()["items"]


def shared() -> dict[str, Any]:
    """capabilities() plus Vault's ``vault`` field (1.4.0, V7.9 c1): "ready" = the agent may offer to save now,
    anything else (or a missing field) = "none" (the contract: a reader MUST treat a missing field as none)."""
    data = _call("GET", "/v1/agent/capabilities")
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise VaultError("bad_response")
    out = []
    for it in items:
        if isinstance(it, dict) and all(isinstance(it.get(k), str) and it[k] for k in ("grant_id", "item_id", "label")):
            out.append({"grant_id": it["grant_id"], "item_id": it["item_id"], "label": it["label"],
                        "expires_at": it.get("expires_at") if isinstance(it.get("expires_at"), int) else None})
    return {"items": out, "vault": "ready" if data.get("vault") == "ready" else "none"}


def _enum(value: str | None, allowed: tuple[str, ...], code: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise VaultError(code)
    return value


def _pending(data: Any) -> dict[str, Any]:
    if not (isinstance(data, dict) and isinstance(data.get("request_id"), str)
            and _REQUEST_ID_RE.fullmatch(data["request_id"]) and data.get("state") == "pending"
            and isinstance(data.get("expires_at"), int)):
        raise VaultError("bad_response")
    retry = data.get("retry_after_s")
    return {"request_id": data["request_id"], "state": "pending", "expires_at": data["expires_at"],
            "retry_after_s": retry if isinstance(retry, int) and not isinstance(retry, bool) and 1 <= retry <= 3600
            else 15}


def request_access(item_type: str | None = None, provider: str | None = None,
                   item_id: str | None = None) -> dict[str, Any]:
    """ACCESS request: ask the owner for a key. The owner says yes or no in Windy; a yes shows up as a new
    item in shared(). Enums only; item_id is a hint (an id from shared(), never free text)."""
    body: dict[str, Any] = {}
    if (t := _enum(item_type, ITEM_TYPES, "bad_item_type")) is not None:
        body["item_type"] = t
    if (p := _enum(provider, PROVIDERS, "bad_provider")) is not None:
        body["provider"] = p
    if item_id is not None:
        if not (isinstance(item_id, str) and _ITEM_ID_RE.fullmatch(item_id)):
            raise VaultError("bad_item_id")
        body["item_id"] = item_id
    return _pending(_call("POST", "/v1/agent/requests", body))


def offer_save(item_type: str, provider: str | None = None) -> dict[str, Any]:
    """SAVE request (V7.9 c2), the NO-VALUE path only: the owner names the item and types the secret into the
    Vault's own screen, so it never passes through this agent or its model. Saving never grants it back."""
    body: dict[str, Any] = {"class": "save", "item_type": _enum(item_type, ITEM_TYPES, "bad_item_type")}
    if body["item_type"] is None:
        raise VaultError("bad_item_type")
    if (p := _enum(provider, PROVIDERS, "bad_provider")) is not None:
        body["provider"] = p
    return _pending(_call("POST", "/v1/agent/requests", body))


def request_state(request_id: str) -> dict[str, Any]:
    """Where a request stands: access = pending|approved|denied|expired; save = pending|saved|declined|expired."""
    if not isinstance(request_id, str) or not _REQUEST_ID_RE.fullmatch(request_id):
        raise VaultError("bad_request_id")  # never let a "/" or ".." into the URL path
    data = _call("GET", f"/v1/agent/requests/{request_id}")
    if not isinstance(data, dict) or data.get("class") not in ("access", "save"):
        raise VaultError("bad_response")
    states = _SAVE_STATES if data["class"] == "save" else _ACCESS_STATES
    if data.get("state") not in states or not isinstance(data.get("expires_at"), int):
        raise VaultError("bad_response")
    return {"class": data["class"], "state": data["state"], "expires_at": data["expires_at"]}


def lease(item_id: str) -> Lease:
    """Lease one shared item and decrypt its value locally. A lease to a kid we no longer hold (a rotation
    beyond the overlap) re-registers the key and asks once more."""
    for attempt in (1, 2):
        data = _call("POST", "/v1/agent/lease", {"item_id": item_id})
        if not isinstance(data, dict) or data.get("mode") != "static_key" or not isinstance(data.get("value_jwe"), str):
            raise VaultError("bad_response")
        lid, kid, exp = data.get("lease_id"), data.get("kid"), data.get("expires_at")
        if not (isinstance(lid, str) and _LEASE_ID_RE.fullmatch(lid) and isinstance(kid, str) and isinstance(exp, int)):
            raise VaultError("bad_response")
        try:
            value = lease_jwe.decrypt_lease(data["value_jwe"], lambda k: lease_key.private_for(k), lease_kid=kid)
        except lease_jwe.UnknownLeaseKey:
            _release_quietly(lid)  # that lease is OPEN at Vault: give it back before asking again
            if attempt == 2:
                raise VaultError("unknown_lease_key") from None
            logger.info("Vault lease to a key this agent no longer holds; re-registering the lease key")
            register_lease_key()
            continue
        except lease_jwe.LeaseDecryptError:
            raise VaultError("bad_lease") from None
        return Lease(lease_id=lid, item_id=str(data.get("item_id") or item_id), value=value, expires_at=exp,
                     targets=_targets(data.get("targets")))
    raise VaultError("unknown_lease_key")


def _targets(raw: Any) -> tuple[str, ...]:
    """The lease's targets in FillTarget form; anything malformed is dropped (fail closed: no target, no call)."""
    from windyfly.vault.egress import normalize_origin

    if not isinstance(raw, list):
        return ()
    out = [o for o in (normalize_origin(t) for t in raw[:8] if isinstance(t, str)) if o]
    return tuple(dict.fromkeys(out))


def _release_quietly(lease_id: str) -> None:
    try:
        release(lease_id)
    except VaultError as e:
        logger.info("Vault: could not release lease %s (%s); it expires on its own", lease_id, e.code)


def release(lease_id: str) -> bool:
    """End a lease early. True = Vault confirmed; False = Vault keeps retrying (stop using it either way)."""
    if not isinstance(lease_id, str) or not _LEASE_ID_RE.fullmatch(lease_id):
        raise VaultError("bad_lease_id")  # never let a "/" or ".." into the URL path
    data = _call("POST", f"/v1/agent/lease/{lease_id}/release")
    return bool(isinstance(data, dict) and data.get("released") is True)
