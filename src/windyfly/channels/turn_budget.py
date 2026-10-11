"""Agent-to-agent turn budget (spec: windy-orchestra specs/AGENT_TURN_BUDGET_SPEC_2026-10-11.md).

In a room, this agent posts at most LIMIT replies to OTHER AGENTS per WINDOW seconds unless a human
speaks there. Over budget it drops further agent messages before any command or model work (so a
loop costs nothing), and its owner is told once per episode. Any human message in the room resets
the budget. "[no reply]" / empty answers post nothing and cost nothing (only posts are recorded).

Why: on 10-11 two agents of one owner answered each other's failure line ~550 times a minute for
9 minutes on another runtime. windyfly had no counter at all ("the runtime enforces no counter").
Per process: windyfly runs one runtime per channel, and a restart already drops agent messages
sent before it started.

Env: WINDY_AGENT_TURN_BUDGET (replies, default 6), WINDY_AGENT_TURN_WINDOW_S (seconds, default 600).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import deque

DEFAULT_LIMIT = 6
DEFAULT_WINDOW_S = 600.0

_lock = threading.Lock()
_posts: dict[str, deque[float]] = {}
_blocked: set[str] = set()


def _env_number(name: str, default: float, minimum: float) -> float:
    try:
        value = float(os.environ.get(name, "") or default)
    except ValueError:
        return default
    return max(minimum, value)


def limit() -> int:
    return int(_env_number("WINDY_AGENT_TURN_BUDGET", DEFAULT_LIMIT, 1))


def window_s() -> float:
    return _env_number("WINDY_AGENT_TURN_WINDOW_S", DEFAULT_WINDOW_S, 1.0)


def _prune(room: str, now: float) -> deque[float]:
    posts = _posts.setdefault(room, deque())
    edge = now - window_s()
    while posts and posts[0] <= edge:
        posts.popleft()
    return posts


def human_spoke(room: str) -> None:
    """A person spoke in the room: the agents may carry on."""
    with _lock:
        _posts.pop(room, None)
        _blocked.discard(room)


def allow(room: str, now: float | None = None) -> bool:
    """May this agent answer one more agent message in this room right now?"""
    now = time.monotonic() if now is None else now
    with _lock:
        ok = len(_prune(room, now)) < limit()
        if ok:
            _blocked.discard(room)   # the window freed a slot: a later block is a new episode
        return ok


def record(room: str, now: float | None = None) -> None:
    """This agent posted a reply to another agent in this room."""
    now = time.monotonic() if now is None else now
    with _lock:
        _prune(room, now).append(now)


def first_block(room: str) -> bool:
    """True once per budget episode (so the owner is told once, not per dropped message)."""
    with _lock:
        if room in _blocked:
            return False
        _blocked.add(room)
        return True


def room_ref(room: str) -> str:
    """A short hash for logs and events: never the room id itself."""
    return hashlib.sha256(room.encode()).hexdigest()[:12]


def owner_notice(other: str, room_name: str) -> str:
    return (f"I stopped answering {other} in {room_name}: we had sent each other {limit()} messages in "
            f"{round(window_s() / 60)} minutes with no one else talking. Say something in that room "
            "and I'll carry on.")


def reset_for_tests() -> None:
    with _lock:
        _posts.clear()
        _blocked.clear()
