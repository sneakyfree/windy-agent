"""The owner's stop, and which agent messages are turns at all (agent-teamwork.v1; Grant's order 10-17).

On 10-17 two of the owner's agents looped in a group room and the owner's "stop" did not stop them. In
windyfly the turn budget (#528) even treated any human message as a reset, so a "stop" bought the agents
six more replies each. Now:

- **Stop (P5).** In a room, the owner's stop word ("stop", "stop it", "please stop", "enough", "be
  quiet", ...) or an ``ai.windy.stop`` event from the owner silences this agent's replies to OTHER AGENTS
  in that room. A stop is answered with nothing (no model call, no reply, no push), and it never resets
  the turn budget. The owner's next normal message in that room resumes. Stops are kept in a small
  state file (room ids only), so a restart in the middle of a loop does not let the agents go on.
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
_loaded = False


def _path():
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "room_stops.json"


def _load() -> None:
    """Stops survive a restart (Hub on #596): otherwise a restart mid-loop would let agents go on."""
    global _loaded
    if _loaded:
        return
    _loaded = True
    import json

    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, list):
            _stopped.update(r for r in data if isinstance(r, str))
    except (OSError, ValueError):
        pass


def _save() -> None:
    import json
    import os

    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(_stopped)), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        pass


def is_stop_word(text: str | None) -> bool:
    """The whole message is a stop word (case and punctuation ignored); a sentence that mentions
    stopping is not."""
    if (text or "").lstrip().startswith("/"):
        return False                         # a slash command (/pause, /stop) stays a command
    text = (text or "").replace("\u2019", "'").replace("\u2018", "'")      # iOS types a curly apostrophe
    norm = re.sub(r"[^\w\s']", " ", text.lower())
    norm = " ".join(norm.split())
    return norm in _WORDS


def stop(room: str) -> None:
    with _lock:
        _load()
        _stopped.add(room)
        _save()


def resume(room: str) -> None:
    with _lock:
        _load()
        if room in _stopped:
            _stopped.discard(room)
            _save()


def stopped(room: str) -> bool:
    with _lock:
        _load()
        return room in _stopped


def addressed_to(content: object, me: str | None, joined_members: int) -> bool:
    """Is another agent's message addressed to this agent (``me``)?"""
    if joined_members == 2:
        return True
    if not isinstance(content, dict) or not me:
        return False
    mentions = content.get("m.mentions")
    ids = mentions.get("user_ids") if isinstance(mentions, dict) else None
    if isinstance(ids, list) and any(isinstance(u, str) and u == me for u in ids):
        return True
    task = content.get(TASK_FIELD)
    return isinstance(task, dict) and isinstance(task.get("to"), str) and task.get("to") == me


def is_owner(sender: str | None, config: dict | None = None) -> bool:
    """The room owner by id, not by band: guest mode lowers the owner's band, and a stop must still work."""
    from windyfly.channels.identity import owner_ids

    return bool(sender) and sender in (owner_ids(config).get("matrix") or set())


def reset_for_tests() -> None:
    global _loaded
    with _lock:
        _stopped.clear()
        _loaded = True        # tests start empty and never read a stale file
