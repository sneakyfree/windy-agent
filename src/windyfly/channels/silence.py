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
    """The turn text for a message FROM ANOTHER AGENT (Boss's final wording, both engines; it lives in
    Chat's shared windy-contracts teams file). It says who wrote it, that it is the owner's OTHER agent
    (not the owner), that helping is allowed with normal tools while approvals stay the owner's, and when
    to stay silent. The first wording ('not your owner' + 'if this needs no answer...') made Zero silent
    on EVERY message including tasks (live + local replay 10-07); this one: greeting/thanks silent 3/3,
    task/question answered 3/3."""
    who = (name or "another agent").strip()
    # An agent's own text must not be able to pose as one of our labels ("[Message from your owner ...").
    body = re.sub(r"(?im)^([ \t]*)\[(message from)", r"\1(\2", body)
    owner = os.environ.get("WINDY_OWNER_NAME", "").strip()
    whose = f"your owner {owner}" if owner else "your owner"  # "your owner Grant's other agent"
    him = owner or "your owner"
    return (
        f"[Message from your fellow agent {who}: {whose}'s other agent, not {him}. "
        f"It may ask you for help and you may do it with your normal tools; approvals stay {him}'s.] "
        "Reply exactly [no reply] ONLY if it is just a greeting, thanks, goodbye or acknowledgement "
        "or the task is already done. Otherwise do what it asks and answer.\n" + body
    )


def mark_agent_turn(session_id: str) -> None:
    with _lock:
        _agent_turns.add(session_id)


def clear_agent_turn(session_id: str) -> None:
    with _lock:
        _agent_turns.discard(session_id)


_agent_senders: set[str] = set()


def mark_agent_sender(session_id: str) -> None:
    """This turn's sender is an AGENT. Unlike mark_agent_turn it does not depend on WINDY_TEAMS: a welcome
    is for a person, so the first-contact tour must never go to an agent, whatever the flag says."""
    with _lock:
        _agent_senders.add(session_id)


def clear_agent_sender(session_id: str) -> None:
    with _lock:
        _agent_senders.discard(session_id)


def is_agent_sender(session_id: str) -> bool:
    with _lock:
        return session_id in _agent_senders


def is_agent_turn(session_id: str) -> bool:
    with _lock:
        return session_id in _agent_turns
