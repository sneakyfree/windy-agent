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

from windyfly.trust.check import TrustDecision, TrustSnapshot, check_trust

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
    #: True when nobody could tell just now (no verdict fresh enough), as opposed to a verdict against the agent.
    unverified: bool = False

    def __str__(self) -> str:
        return f"trust gate denied '{self.action}': {self.reason} (band={self.band})"


async def require_trust(action: str, passport: str | None = None, db=None, live: bool = False) -> TrustDecision:
    """Gate: allow or raise.

    Call at the top of every sensitive action. The returned decision
    carries the snapshot so callers can log the integrity score /
    band used to authorize the action. ``live=True`` for a high-value act: no cache (see check_trust).
    """
    decision = await check_trust(action, passport=passport, db=db, live=live)
    if not decision.allowed:
        logger.warning(
            "Trust gate denied: action=%s band=%s reason=%s",
            action, decision.snapshot.band, decision.reason,
        )
        raise TrustDenied(
            action=action,
            band=decision.snapshot.band,
            reason=decision.reason,
            unverified=decision.unverified,
        )
    return decision


def require_trust_sync(action: str, passport: str | None = None, db=None, live: bool = False) -> TrustDecision:
    """Sync variant — for non-async call sites (tools, the command registry).

    A fresh cached verdict decides at once. Otherwise the verdict is FETCHED, also from inside a running event
    loop: there the fetch runs on a helper thread, bounded by the fetch timeout. (Until 2026-11 a call from inside a
    loop with no fresh cache was simply allowed, without asking Eternitas at all.) If the fetch cannot finish, the
    rule for an unrefreshed verdict decides (check.decide_unrefreshed).
    """
    import asyncio
    import os

    from windyfly.trust.check import _TIMEOUT, NO_LIVE_VERDICT, _cache_read, decide_unrefreshed

    pp = passport or os.environ.get("ETERNITAS_PASSPORT", "")
    cached = _cache_read(pp, db=db) if pp and not live else None
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

    if not in_loop:
        return asyncio.run(require_trust(action, passport=passport, db=db, live=live))

    import concurrent.futures

    def fetch_and_decide() -> TrustDecision:
        return asyncio.run(require_trust(action, passport=passport, db=db, live=live))

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="trust-fetch")
    try:
        return pool.submit(fetch_and_decide).result(timeout=_TIMEOUT + 2.0)
    except concurrent.futures.TimeoutError:
        logger.warning("Trust gate (sync): the fetch did not finish in time for '%s'", action)
        decision = decide_unrefreshed(action, pp, db=db) if pp else TrustDecision(
            allowed=True, snapshot=TrustSnapshot(passport="none", band="unknown"), reason="no passport")
        if live and (decision.allowed or decision.unverified):
            # The same sentence as check_trust's live path: one way to say "no live answer" (Boss on #781).
            raise TrustDenied(action=action, band="unknown", reason=NO_LIVE_VERDICT, unverified=True) from None
        if not decision.allowed:
            raise TrustDenied(action=action, band=decision.snapshot.band, reason=decision.reason,
                              unverified=decision.unverified) from None
        return decision
    finally:
        pool.shutdown(wait=False)
