"""Mode-B (EPT+agent + DPoP) authentication to ANY Windy service (strand gene A0.3).

One implementation for Windy Mind (``aud=windy-mind``) and Windy Vault (``aud=windy-vault``).
``Authorization: Bearer <EPT+agent>`` plus a fresh ``DPoP:`` proof header on every non-GET
(the wire form Mind, Text and the Vault accept). A refused credential is re-minted once with a
fresh proof; there is NO legacy fallback here (``mind_auth`` adds the Mind-only legacy EPT
fallback on top until Mind's 11-03 sunset).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# 401 x-<service>-error values where a fresh mint and a fresh proof can help.
REMINT_CODES = frozenset({
    "wrong_audience", "expired", "unknown_kid",
    "dpop_required", "invalid_dpop", "dpop_replay",
})


class ServiceAuthError(Exception):
    """The agent could not mint a token for ``aud`` (no registered key, Eternitas down...)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def agent_headers(aud: str, method: str, url: str) -> dict[str, str]:
    """Auth headers for ONE request. ``url`` is the absolute URL of that request (no query)."""
    try:
        from windyfly.eternitas import agent_keys as ak

        token = ak.request_agent_token(aud)["token"]
        out = {"Authorization": f"Bearer {token}"}
        if method.upper() != "GET":
            out["DPoP"] = ak.service_dpop(method.upper(), url)
        return out
    except Exception as exc:  # noqa: BLE001  (AgentTokenError, key IO, network)
        raise ServiceAuthError(str(getattr(exc, "code", type(exc).__name__))) from exc


def forget_token() -> None:
    """Drop the cached mint so the next call asks Eternitas for a new one."""
    try:
        from windyfly.eternitas import agent_keys as ak

        ak.clear_token_cache()
    except Exception:  # noqa: BLE001
        pass


def refused_code(resp: Any, error_header: str) -> str | None:
    """The error code when the service refused the credential in a way a fresh mint can fix."""
    if getattr(resp, "status_code", 0) != 401:
        return None
    code = str((getattr(resp, "headers", None) or {}).get(error_header) or "").strip().lower()
    return code if code in REMINT_CODES else None


def call_with_remint(aud: str, method: str, url: str, send: Callable[[dict[str, str]], Any],
                     error_header: str) -> Any:
    """Send once; on a refusable 401 re-mint ONCE with a fresh proof and send again. Raises
    ServiceAuthError if no token can be minted."""
    resp = send(agent_headers(aud, method, url))
    if refused_code(resp, error_header):
        forget_token()
        resp = send(agent_headers(aud, method, url))
    return resp
