"""Windy Cloud calls carry the agent's own short-lived token (Cloud KE3, Hub GO 10-10).

Every Cloud call (backups, files, domains, the site builder) first presents an EPT+agent
minted for the audience ``windy-cloud`` with a per-request DPoP proof on writes
(``agent.service_auth``, as Chat, Calendar and the housing report do). The token is cached
per (agent key, audience) until 30 s before expiry, so a burst of calls mints once.

If no token can be minted, or Cloud answers 401/403 to it (a cell that does not take it
yet), the call is sent ONCE more with the old credential (bot key, WINDY_CLOUD_TOKEN,
WINDY_JWT or the long-lived Eternitas token), so nothing is stranded before Cloud's flips
(domains, then sites, then core last, on Hub's word). ``WINDY_CLOUD_EPT_AGENT=0`` = the old
path only. Off under pytest unless the flag is set explicitly (unrelated tests never mint).
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
    """The EPT+agent (+ DPoP on writes) headers for ONE Cloud request, or None
    (then the caller uses the old credential)."""
    if not enabled():
        return None
    from windyfly.agent import service_auth

    try:
        return service_auth.agent_headers(AUD, method, url.split("?", 1)[0])
    except service_auth.ServiceAuthError as exc:
        reason = str(exc) or "unknown"
        if reason not in _warned:  # once per reason, never the token
            _warned.add(reason)
            logger.info("[cloud-auth] no EPT+agent for windy-cloud (%s); old credential", reason)
        return None


def refused(status_code: int) -> bool:
    """Cloud did not accept the EPT+agent: retry once with the old credential."""
    return status_code in (401, 403)


def fallback_used(where: str, status_code: int) -> None:
    logger.info("[cloud-auth] %s: Cloud answered %d to the EPT+agent; retried with the old one",
                where, status_code)
