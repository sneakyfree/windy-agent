"""Auth-state classification — shared between resurrect.py + models.py.

Phase 2.2.2 Q5 (per docs/LIFEBOAT_FSM_AS_BUILT.md §5) — Claude's
opinionated answer: the perma-auth classifier lives in its own module
(NOT inside the FSM), because auth detection is orthogonal to lifeboat
state. Both resurrect.py and models.py import from here so the
classifier has a single source of truth.

Pre-extraction, `is_permanent_auth_error` lived in resurrect.py:399
and was called from resurrect.py:471 + models.py:87. PRs #209 + #210
both touched it; future fixes can stay local to this module.
"""

from __future__ import annotations


def is_permanent_auth_error(error_str: str | None) -> bool:
    """Classify a chain-exhaustion error string as permanent-auth
    vs. transient. Permanent-auth = the provider rejected the
    credential itself (401 invalid x-api-key, 403 permission
    denied on org). Transient = rate limit, 5xx, network blip —
    those WILL eventually clear; resurrect is the right answer
    for them. Permanent-auth WON'T clear without operator
    intervention, so resurrecting just wedges the bot in lifeboat
    while paid keeps 401-ing on every escape attempt.

    Surfaced 2026-05-20: Grant's OAuth Max token (sk-ant-oat01-…)
    expired; auto_resurrect kept firing on every 401, wedging
    lifeboat. PR #201's escape mechanism worked correctly but
    couldn't overcome the underlying permanent failure — the
    bot needed to STOP trying lifeboat and surface "your auth is
    dead" instead.

    Conservative pattern: require BOTH "401" (HTTP status) AND
    one of the auth-specific Anthropic markers ("authentication_
    error" / "invalid x-api-key" / "invalid api key"). The
    double-signal requirement avoids treating ambiguous 401s
    (e.g., from a 5xx mis-labeled by some intermediate proxy)
    as permanent. Falls back to "transient" (resurrect-OK) on
    any uncertainty — safer to enter lifeboat than to strand
    the user.
    """
    if not error_str:
        return False
    s = error_str.lower()
    has_401 = "401" in s or "403" in s
    auth_markers = (
        "authentication_error", "authentication error",
        "invalid x-api-key", "invalid api key",
        "invalid_api_key", "invalid_authentication",
        "permission_error",
        "credit balance is too low",
    )
    has_auth_marker = any(m in s for m in auth_markers)
    return has_401 and has_auth_marker
