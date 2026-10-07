"""Agent teams: an agent may CHOOSE SILENCE on a turn that came from another agent.

Contract: windy-contracts ``schema/chat/team-tools.v1.json`` (x-tokens.silence = ``[no reply]``).
The answer ``[no reply]`` (trimmed, case-insensitive, optional trailing punctuation) or an empty
answer, on a turn whose sender is ANOTHER AGENT, posts nothing. An owner (or any human) turn is
always answered: the token there is treated as an empty answer and gets the runtime's normal
fallback line. The runtime enforces no counter; the instruction below is the guidance.
Dark: WINDY_TEAMS=1.
"""

from __future__ import annotations

import os
import re
import threading

TOKEN = "[no reply]"
INSTRUCTION = (
    "When a message from another agent needs no answer (a greeting, thanks, acknowledgement, "
    "or the task is done), reply [no reply]."
)
_TOKEN_RE = re.compile(r"\[no reply\][\s.!?…]*", re.IGNORECASE)
_lock = threading.Lock()
_agent_turns: set[str] = set()


def enabled() -> bool:
    return os.environ.get("WINDY_TEAMS", "") == "1"


def is_silence(text: str | None) -> bool:
    t = (text or "").strip()
    return not t or _TOKEN_RE.fullmatch(t) is not None


def sender_is_agent(sender: str | None) -> bool:
    from windyfly.channels import parity

    return parity.passport_of(sender) is not None


def frame_agent_message(name: str, body: str) -> str:
    """The turn text for a message FROM ANOTHER AGENT (Boss's wording, both engines): it says who wrote
    it, that it is NOT the owner, and carries the silence rule right next to the message where a model
    follows it. Found live 10-07: without a sender, Zero took its sibling for Grant and two agents
    answered each other ~20 times."""
    who = (name or "another agent").strip()
    owner = os.environ.get("WINDY_OWNER_NAME", "").strip()
    not_owner = f"not your owner {owner}" if owner else "not your owner"
    return (
        f"[Message from your fellow agent {who}, {not_owner}] "
        "If this needs no answer, reply exactly [no reply]. "
        "Never reply to thanks, greetings or goodbyes from another agent.\n" + body
    )


def mark_agent_turn(session_id: str) -> None:
    with _lock:
        _agent_turns.add(session_id)


def clear_agent_turn(session_id: str) -> None:
    with _lock:
        _agent_turns.discard(session_id)


def is_agent_turn(session_id: str) -> bool:
    with _lock:
        return session_id in _agent_turns
