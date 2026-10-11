"""This agent's own Matrix user id: the one the homeserver confirms for MATRIX_BOT_TOKEN.

The token is the identity; MATRIX_BOT_USER is optional (quickstart leaves it empty and Windy 0
has none). Reading the env alone made pair rooms ("chat is not configured") and phone tools
fail for an agent without it. Order: the id the Matrix channel confirmed at login, else one
whoami for the token (cached; works in any process, e.g. the Talk bridge). Never the env: a
configured id can be stale (Windy 0 once said @windyfly while its token was @agent_<passport>).
When neither answers this returns "" and every caller refuses (fail closed). Nothing runs at
import: the whoami happens on the first call.
"""

from __future__ import annotations

import os
import time

import httpx

_WHOAMI_TIMEOUT = 5.0
# After a failed whoami, answer "" without asking again for this long, so a
# down homeserver costs at most one 5 s wait per 30 s instead of one per call.
_FAILED_WHOAMI_TTL = 30.0
_confirmed: str | None = None
_failed_until = 0.0


def set_confirmed(user_id: str | None) -> None:
    """The Matrix channel calls this after login with the id the homeserver confirmed."""
    global _confirmed, _failed_until
    _confirmed = (user_id or "").strip() or None
    _failed_until = 0.0


def own_matrix_id() -> str:
    """This agent's own Matrix user id, or "" when there is no way to know it."""
    global _failed_until
    if _confirmed:
        return _confirmed
    if time.monotonic() < _failed_until:
        return ""
    homeserver = os.environ.get("MATRIX_HOMESERVER", "").rstrip("/")
    token = os.environ.get("MATRIX_BOT_TOKEN", "")
    if homeserver and token:
        try:
            resp = httpx.get(
                f"{homeserver}/_matrix/client/v3/account/whoami",
                headers={"Authorization": f"Bearer {token}"},
                timeout=_WHOAMI_TIMEOUT,
            )
            user_id = resp.json().get("user_id") if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            user_id = None
        if isinstance(user_id, str) and user_id.strip():
            set_confirmed(user_id)
            return user_id.strip()
        _failed_until = time.monotonic() + _FAILED_WHOAMI_TTL
    return ""
