"""Loop guard for the Matrix channel. OFF unless WINDY_LOOP_GUARD=1 (dark).

(a) Agent-to-agent: reply to at most ``max_agent_run`` (default 6) CONSECUTIVE
    agent-only messages in a room; the run resets when the owner speaks. Beyond
    that, stay silent so two agents can never ping-pong.
(b) Turns cap: at most ``max_turns_per_hour`` (default 120) replies per hour to
    NON-owner senders. Tripping it sets the agent PAUSED (persisted), tells the
    owner once, and the agent stays paused for non-owner senders until the owner
    clears it with /resume. A turn made for the owner never counts toward the trip
    and the owner is never blocked.
"""

from __future__ import annotations

import json
import os
import time
from collections import defaultdict, deque
from collections.abc import Callable
from pathlib import Path

REPLY = "reply"
IGNORE_AGENT_RUN = "ignore_agent_run"
IGNORE_PAUSED = "ignore_paused"
RESUME_COMMAND = "/resume"


def enabled() -> bool:
    return os.environ.get("WINDY_LOOP_GUARD", "") == "1"


def _int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, "")))
    except ValueError:
        return default


def is_agent_account(user_id: str) -> bool:
    """Agents use ``@agent_<passport>:<server>`` Matrix ids (Eternitas-derived)."""
    return user_id.startswith("@agent_")


class LoopGuard:
    def __init__(
        self,
        state_path: Path | None = None,
        *,
        max_agent_run: int | None = None,
        max_turns_per_hour: int | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.max_agent_run = max_agent_run or _int_env("WINDY_LOOP_AGENT_RUN", 6)
        self.max_turns = max_turns_per_hour or _int_env("WINDY_LOOP_TURNS_PER_HOUR", 120)
        self._clock = clock
        self._state_path = state_path
        self._agent_run: dict[str, int] = defaultdict(int)
        self._replies: deque[float] = deque()
        self._paused = self._load_paused()
        self._notified = self._paused  # a paused-at-boot agent already told its owner

    # ── persistence (a restart must not clear a pause) ───────────────
    def _load_paused(self) -> bool:
        if self._state_path and self._state_path.exists():
            try:
                return bool(json.loads(self._state_path.read_text()).get("paused"))
            except (OSError, ValueError):
                return False
        return False

    def _save(self) -> None:
        if self._state_path:
            try:
                self._state_path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._state_path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"paused": self._paused, "at": self._clock()}))
                os.replace(tmp, self._state_path)
            except OSError:
                pass

    @property
    def paused(self) -> bool:
        return self._paused

    # ── decisions ────────────────────────────────────────────────────
    def check(self, room_id: str, sender: str, *, is_owner: bool) -> str:
        """Decide whether to reply to this inbound message."""
        if is_owner:
            self._agent_run[room_id] = 0  # the owner spoke: the run resets
            return REPLY
        if is_agent_account(sender):
            self._agent_run[room_id] += 1
            if self._agent_run[room_id] > self.max_agent_run:
                return IGNORE_AGENT_RUN
        else:
            self._agent_run[room_id] = 0  # a human spoke
        if self._paused:
            return IGNORE_PAUSED
        return REPLY

    def record_reply(self, *, owner_turn: bool) -> bool:
        """Count a sent reply. Returns True exactly once when this reply TRIPS the pause."""
        if owner_turn:
            return False
        now = self._clock()
        self._replies.append(now)
        while self._replies and now - self._replies[0] > 3600:
            self._replies.popleft()
        if len(self._replies) >= self.max_turns and not self._paused:
            self._paused = True
            self._save()
            if not self._notified:
                self._notified = True
                return True
        return False

    def clear(self) -> None:
        """Owner clears the pause (/resume)."""
        self._paused = False
        self._notified = False
        self._replies.clear()
        self._save()

    def trip_notice(self) -> str:
        return (
            f"Paused: unusual activity, {self.max_turns} replies in an hour. "
            f"I'll stay quiet for everyone but you until you send {RESUME_COMMAND}."
        )
