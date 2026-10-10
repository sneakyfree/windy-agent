"""Windy Cloud calls carry the agent's own short-lived token (Cloud KE3, Hub GO 10-10).

Every Cloud call (backups, files, domains, the site builder) first presents an EPT+agent minted for the audience
``windy-cloud`` with a per-request DPoP proof (``agent.service_auth``, as Chat, Calendar and the housing report do).
If that cannot be minted, or Cloud answers 401/403 to it (a cell that does not take it yet), the call is sent ONCE
more with the old credential (bot key, WINDY_CLOUD_TOKEN, WINDY_JWT or the long-lived Eternitas token), so nothing
is stranded until Cloud's flips (domains, then sites, then core, on Hub's word). ``WINDY_CLOUD_EPT_AGENT=0`` = the
old path only. Off under pytest unless the flag is set explicitly (no minting from unrelated tests).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

AUD = "windy-cloud"
FLAG = "WINDY_CLOUD_EPT_AGENT"
_warned: set[str] = set()


def enabled() -> bool:
    raw = os.environ.get(FLAG)
    if raw is None:
        return not os.environ.get("PYTEST_CURRENT_TEST")
    return raw.strip().lower() not in ("0", "false", "off", "no")


def agent_headers(method: str, url: str) -> dict[str, str] | None:
    """The EPT+agent (+ DPoP) headers for ONE Cloud request, or None (then the caller uses the old credential)."""
    if not enabled():
        return None
    from windyfly.agent import service_auth

    try:
        return service_auth.agent_headers(AUD, method, url.split("?", 1)[0])
    except service_auth.ServiceAuthError as exc:
        reason = str(exc) or "unknown"
        if reason not in _warned:  # once per reason, never the token
            _warned.add(reason)
            logger.info("[cloud-auth] no EPT+agent for windy-cloud (%s); using the old credential", reason)
        return None


def refused(status_code: int) -> bool:
    """Cloud did not accept the EPT+agent: retry once with the old credential."""
    return status_code in (401, 403)


def fallback_used(where: str, status_code: int) -> None:
    logger.info("[cloud-auth] %s: Cloud answered %d to the EPT+agent; retried with the old credential",
                where, status_code)
