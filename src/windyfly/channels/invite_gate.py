"""Who may pull this agent into a Matrix room (Boss ruling 10-09): the owner's own account and the
owner's sibling agents, nobody else. Dark: WINDY_INVITE_GATE=1 (unset = the old join-any-invite).

The answer is a runtime FACT, never a model sentence:
- the inviter is the owner (``identity.owner_ids`` for matrix) -> join;
- the inviter is an agent (@agent_<passport>) -> ask Chat ``GET /pair-room/invite-check`` (same owner per
  Chat's onboarding data, revoked/retired refused): 200 ok -> join; 4xx -> ignore; 429/5xx/unreachable -> retry
  on a later sync (the caller keeps it at most ``RECHECK_FOR_S``);
- anyone else -> ignore.
No owner known on matrix yet (a fresh install, first contact) keeps today's behaviour: join.
A refused invite gets silence: no message, no reject event, one log line without content.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Literal

logger = logging.getLogger(__name__)

RECHECK_FOR_S = 600.0

Decision = Literal["join", "ignore", "retry"]


def enabled() -> bool:
    return os.environ.get("WINDY_INVITE_GATE", "") == "1"


def decide(inviter: str, room_id: str, config: dict[str, Any] | None = None) -> Decision:
    """Blocking (one HTTP call for an agent inviter); run it off the event loop."""
    from windyfly.channels import identity, parity

    inviter = (inviter or "").strip()
    if parity.passport_of(inviter):
        return _ask_chat(inviter, room_id)
    owners = identity.owner_ids(config).get("matrix")
    if not owners:
        return "join"
    return "join" if inviter in owners else "ignore"


def _ask_chat(inviter: str, room_id: str) -> Decision:
    from windyfly.agent import teams

    try:
        status, data = teams.call("GET", "pair-room/invite-check", params={"room": room_id, "inviter": inviter})
    except RuntimeError:
        return "retry"
    if status == 200 and data.get("ok") is True:
        return "join"
    if 400 <= status < 500 and status != 429:  # 429 = Chat busy, not an answer: retry
        logger.info("invite gate: Chat refused agent invite to %s (%s)", room_id, data.get("error", status))
        return "ignore"
    return "retry"
