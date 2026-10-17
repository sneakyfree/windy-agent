"""The owner's stop, and which agent messages are turns at all (agent-teamwork.v1; Grant's order 10-17).

On 10-17 two of the owner's agents looped in a group room and the owner's "stop" did not stop them. In
windyfly the turn budget (#528) even treated any human message as a reset, so a "stop" bought the agents
six more replies each. Now:

- **Stop (P5).** In a room, the owner's stop word ("stop", "stop it", "please stop", "enough", "be
  quiet", ...) or an ``ai.windy.stop`` event from the owner silences this agent's replies to OTHER AGENTS
  in that room. A stop is answered with nothing (no model call, no reply, no push), and it never resets
  the turn budget. The owner's next normal message in that room resumes. Stops live in this process only.
- **Addressed only (P1).** Another agent's message is a turn only when it is addressed to this agent: its
  ``m.mentions.user_ids`` names this agent, or its ``ai.windy.task.to`` does, or the room holds just the
  two of them. Everything else is not a turn and makes no model call.
"""

from __future__ import annotations

import re
import threading

STOP_EVENT = "ai.windy.stop"
TASK_FIELD = "ai.windy.task"

_WORDS = {
    "stop", "stop it", "stop now", "please stop", "stop please", "stop talking", "stop that",
    "enough", "that's enough", "thats enough", "be quiet", "quiet", "shut up", "pause", "hold on stop",
    "stop stop", "stop everyone", "everyone stop", "agents stop", "stop agents",
}
_stopped: set[str] = set()
_lock = threading.Lock()


def is_stop_word(text: str | None) -> bool:
    """The whole message is a stop word (case and punctuation ignored); a sentence that mentions
    stopping is not."""
    if (text or "").lstrip().startswith("/"):
        return False                         # a slash command (/pause, /stop) stays a command
    norm = re.sub(r"[^\w\s']", " ", (text or "").lower())
    norm = " ".join(norm.split())
    return norm in _WORDS


def stop(room: str) -> None:
    with _lock:
        _stopped.add(room)


def resume(room: str) -> None:
    with _lock:
        _stopped.discard(room)


def stopped(room: str) -> bool:
    with _lock:
        return room in _stopped


def addressed_to(content: object, me: str | None, joined_members: int) -> bool:
    """Is another agent's message addressed to this agent (``me``)?"""
    if joined_members == 2:
        return True
    if not isinstance(content, dict) or not me:
        return False
    mentions = content.get("m.mentions")
    if isinstance(mentions, dict) and me in (mentions.get("user_ids") or []):
        return True
    task = content.get(TASK_FIELD)
    return isinstance(task, dict) and task.get("to") == me


def is_owner(sender: str | None, config: dict | None = None) -> bool:
    """The room owner by id, not by band: guest mode lowers the owner's band, and a stop must still work."""
    from windyfly.channels.identity import owner_ids

    return bool(sender) and sender in (owner_ids(config).get("matrix") or set())


def reset_for_tests() -> None:
    with _lock:
        _stopped.clear()
