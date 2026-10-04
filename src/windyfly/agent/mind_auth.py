"""How windyfly authenticates to Windy Mind (plan v2.1 S10.8, R5).

A Windy-born agent presents an ``EPT+agent`` for ``aud=windy-mind``: a short-lived
token bound to the agent's own registered key, with its LIVE ei/band. Every non-GET
also carries a DPoP proof for THAT request (htm + htu, fresh jti). GETs are
bearer-only. This replaces the long-lived "legacy" EPT, which Mind counts from
10-18, refuses on non-GET from 11-03 and stops accepting 12-03 (Eternitas + Mind
sunset agreed 10-04).

Until then a failed mint (no registered key yet, Eternitas unreachable) or an
auth refusal falls back to the legacy EPT, so a hiccup never silences the agent.
Switch off with ``WINDY_MIND_EPT_AGENT=0``.

Scheme: ``Authorization: Bearer <token>`` plus a ``DPoP:`` header. (Mind's
``DPoP`` scheme answered a bare 401 until windy-mind #243; Bearer works today.)
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

MIND_AUD = "windy-mind"
ENV_FLAG = "WINDY_MIND_EPT_AGENT"

# 401 x-mind-error values where a fresh mint and a fresh proof can help. A refused
# proof is never resent: every attempt signs a new one (fresh jti).
REMINT_CODES = frozenset({
    "wrong_audience", "expired", "unknown_kid",
    "dpop_required", "invalid_dpop", "dpop_replay",
})

_warned: set[str] = set()


def enabled() -> bool:
    """On by default; ``WINDY_MIND_EPT_AGENT=0`` turns it off. Tests never mint a
    real token unless they set the flag themselves."""
    raw = os.environ.get(ENV_FLAG)
    if raw is None:
        return not os.environ.get("PYTEST_CURRENT_TEST")
    return raw.strip().lower() not in ("0", "false", "off", "no", "")


def _warn_once(key: str, msg: str, *args: Any) -> None:
    if key not in _warned:
        _warned.add(key)
        logger.warning(msg, *args)


def legacy_headers(ept: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {ept}"}


def headers(method: str, url: str, ept: str) -> tuple[dict[str, str], bool]:
    """(auth headers, used_ept_agent) for one request to Mind.

    ``url`` is the absolute URL of THAT request (no query string). Never raises:
    any mint failure returns the legacy EPT headers and False.
    """
    if not enabled():
        return legacy_headers(ept), False
    try:
        from windyfly.eternitas import agent_keys as ak

        token = ak.request_agent_token(MIND_AUD)["token"]
        out = {"Authorization": f"Bearer {token}"}
        if method.upper() != "GET":
            out["DPoP"] = ak.service_dpop(method.upper(), url)
        return out, True
    except Exception as exc:  # noqa: BLE001  (AgentTokenError, key IO, network)
        code = getattr(exc, "code", type(exc).__name__)
        _warn_once(f"mint:{code}", "Windy Mind: no EPT+agent (%s); using the legacy EPT", code)
        return legacy_headers(ept), False


def refused_auth(resp: Any) -> str | None:
    """The x-mind-error code when Mind refused the credential in a way a fresh
    token/proof can fix, else None."""
    if getattr(resp, "status_code", 0) != 401:
        return None
    code = str((getattr(resp, "headers", None) or {}).get("x-mind-error") or "").strip().lower()
    return code if code in REMINT_CODES else None


def forget_token() -> None:
    """Drop the cached mint so the next headers() call asks Eternitas for a new one."""
    try:
        from windyfly.eternitas import agent_keys as ak

        ak.clear_token_cache()
    except Exception:  # noqa: BLE001
        pass


def _call(send: Any, method: str, url: str, ept: str) -> Any:
    """Send one request with EPT+agent (+ a fresh DPoP proof on non-GET).

    ``send(auth_headers)`` performs the request. One refused-credential retry with a
    fresh mint and proof, then one with the legacy EPT (until the sunset). Any other
    answer is returned as is.
    """
    auth, agent = headers(method, url, ept)
    resp = send(auth)
    if not agent:
        return resp
    code = refused_auth(resp)
    if code:
        logger.warning("Windy Mind refused the EPT+agent (%s); minting a fresh one", code)
        forget_token()
        auth, agent = headers(method, url, ept)
        resp = send(auth)
        code = refused_auth(resp) if agent else None
        if code:
            _warn_once(f"refused:{code}", "Windy Mind refused the EPT+agent twice (%s); using the legacy EPT", code)
            resp = send(legacy_headers(ept))
    return resp


def post(url: str, ept: str, body: dict[str, Any], timeout: float) -> Any:
    """POST JSON to Mind with EPT+agent + a fresh DPoP proof per attempt."""
    import httpx

    return _call(
        lambda auth: httpx.post(url, headers={**auth, "Content-Type": "application/json"},
                                json=body, timeout=timeout),
        "POST", url, ept)


def client_post(client: Any, path: str, ept: str, body: dict[str, Any]) -> Any:
    """POST ``path`` through an httpx.Client whose base_url is Mind (claim, heartbeat,
    release). The proof's htu is the client's base_url + path."""
    url = str(client.base_url).rstrip("/") + path
    return _call(lambda auth: client.post(path, json=body, headers=auth), "POST", url, ept)


def get(url: str, ept: str, timeout: float) -> Any:
    """GET from Mind, bearer-only. A refused credential retries once with the legacy EPT."""
    import httpx

    auth, agent = headers("GET", url, ept)
    resp = httpx.get(url, headers=auth, timeout=timeout)
    if agent and refused_auth(resp):
        forget_token()
        resp = httpx.get(url, headers=legacy_headers(ept), timeout=timeout)
    return resp
