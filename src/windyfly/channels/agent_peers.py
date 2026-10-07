"""``/agents [on|off]``: whether OTHER agents (not the owner's own) may talk to this agent.

Same meaning as the roster's (windy-contracts ``schema/chat/commands.v1.json``; Windy Chat's peer gate):

* OFF blocks a PEER: an agent sender (``@agent_<passport>``) that is not one of the owner's own agents.
  The owner's own agents (the TRUSTED band, agent teams) and humans are never touched by it.
* A blocked message is never stored, never fed to the model, leaves no history line. The sender sees ONE
  canned line, at most once per (sender, room) per hour. No model call.
* The owner's choice is PERSISTED (one small file in the state dir) and survives restarts; default is ON.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

NOTICE = ("I'm not taking messages from other agents right now — "
          "my owner has agent-to-agent chat switched off.")
HINT = "Say /agents on, /agents off, or just /agents to see where it stands."
_ON = "Agent-to-agent chat is **on** — other agents can talk to me. Say **/agents off** to stop it."
_OFF = ("Agent-to-agent chat is **off** — agents that aren't yours can't talk to me. "
        "Say **/agents on** to change that.")
NOTICE_EVERY_S = 3600.0

_lock = threading.Lock()
_notified: dict[tuple[str, str], float] = {}


def _path() -> Path:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / "agents_policy.json"


def policy() -> str:
    """``"on"`` (default) or ``"off"``. An unreadable file means the default."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "on"
    return "off" if isinstance(data, dict) and data.get("policy") == "off" else "on"


def set_policy(value: str) -> None:
    """Persist the owner's choice (atomic write, 0600)."""
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, json.dumps({"policy": "off" if value == "off" else "on"}).encode("utf-8"))
    finally:
        os.close(fd)
    os.replace(tmp, path)


def reply(arg: str) -> str:
    """The owner's ``/agents`` answer. States the state AFTER any change."""
    word = (arg or "").strip().lower()
    if word in ("on", "off"):
        set_policy(word)
    elif word:
        return HINT
    return _OFF if policy() == "off" else _ON


def should_notify(sender: str, room_id: str, now: float | None = None) -> bool:
    """True the first time in an hour that a blocked ``sender`` writes in ``room_id`` (and records it)."""
    t = time.time() if now is None else now
    key = (sender, room_id)
    with _lock:
        last = _notified.get(key)
        if last is not None and t - last < NOTICE_EVERY_S:
            return False
        _notified[key] = t
        return True


def _reset_for_tests() -> None:
    with _lock:
        _notified.clear()
