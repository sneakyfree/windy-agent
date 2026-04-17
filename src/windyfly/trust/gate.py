"""Trust gate — the single choke point for sensitive agent actions.

Call sites wrap their work in `await require_trust("action_name")`.
If the agent's integrity band or clearance doesn't grant that action,
TrustDenied is raised and the call never reaches the network.

This is separate from scope-checking on the wk_ bot key: scopes
describe what the *key* may do; trust describes what the *agent*
may do right now given its current behaviour. Both must pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from windyfly.trust.check import TrustDecision, check_trust

logger = logging.getLogger(__name__)


GATED_ACTIONS: tuple[str, ...] = (
    "send_email",
    "post_chat_message",
    "run_command",
    "install_package",
    "commit_push",
    "upload_file",
)


@dataclass
class TrustDenied(Exception):
    """Raised when the trust gate refuses a sensitive action."""

    action: str
    band: str
    reason: str

    def __str__(self) -> str:
        return f"trust gate denied '{self.action}': {self.reason} (band={self.band})"


async def require_trust(action: str, passport: str | None = None, db=None) -> TrustDecision:
    """Gate: allow or raise.

    Call at the top of every sensitive action. The returned decision
    carries the snapshot so callers can log the integrity score /
    band used to authorize the action.
    """
    decision = await check_trust(action, passport=passport, db=db)
    if not decision.allowed:
        logger.warning(
            "Trust gate denied: action=%s band=%s reason=%s",
            action, decision.snapshot.band, decision.reason,
        )
        raise TrustDenied(
            action=action,
            band=decision.snapshot.band,
            reason=decision.reason,
        )
    return decision


def require_trust_sync(action: str, passport: str | None = None, db=None) -> TrustDecision:
    """Sync variant — for legacy non-async call sites.

    Reads the SQLite cache directly (sync) when it's fresh. Falls back
    to an async fetch via asyncio.run when there's no cache. When
    we're already inside a running event loop and the cache is empty,
    we defer to the permissive dev policy rather than deadlocking —
    async callers should use `require_trust` instead.
    """
    import asyncio
    from windyfly.trust.check import _cache_read, _strict_mode, TrustSnapshot

    pp = passport or __import__("os").environ.get("ETERNITAS_PASSPORT", "")
    cached = _cache_read(pp, db=db) if pp else None
    if cached is not None:
        if cached.allows(action):
            return TrustDecision(allowed=True, snapshot=cached)
        logger.warning("Trust gate (sync) denied from cache: action=%s band=%s", action, cached.band)
        raise TrustDenied(
            action=action,
            band=cached.band,
            reason=f"action '{action}' not in allowed_actions for band '{cached.band}'",
        )

    try:
        asyncio.get_running_loop()
        in_loop = True
    except RuntimeError:
        in_loop = False

    if in_loop:
        if _strict_mode():
            raise TrustDenied(
                action=action,
                band="unknown",
                reason="sync gate cannot fetch trust from inside running event loop (strict)",
            )
        return TrustDecision(
            allowed=True,
            snapshot=TrustSnapshot(passport=pp or "unknown", band="unknown"),
            reason="sync gate in event loop, no cache, fail-open",
        )

    return asyncio.run(require_trust(action, passport=passport, db=db))
