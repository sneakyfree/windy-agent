"""Post-heavy-turn skill-capture nudge (Sprint 3).

The Hermes finding that reframed "self-improving agent" for us: their
celebrated learning loop is mostly a PROMPT NUDGE — after substantial
work, remind the model that persisting what it learned is an option,
and give it a tool to do so. Cheap, deterministic, hugely compounding.

Mechanism here: the agent loop records how many tool calls each turn
executed. When the PREVIOUS turn was heavy (≥ ``NUDGE_THRESHOLD``
calls), the next turn's prompt carries a one-shot system nudge to
consider ``skill.save``. Next-turn (not same-turn) because injection
happens before the model call — and it reads naturally: "that thing we
just did — worth saving?"
"""

from __future__ import annotations

import threading

NUDGE_THRESHOLD = 5
_MAX_TRACKED_SESSIONS = 500

_last_turn_tool_calls: dict[str, int] = {}
_lock = threading.Lock()

NUDGE_TEXT = (
    "Your previous reply completed a multi-step task ({n} tool calls). "
    "If that workflow is repeatable and went well, consider saving it "
    "with skill.save (a short numbered playbook with the exact "
    "commands/values that worked) so you remember it permanently. "
    "Skip saving if it was one-off, trivial, or didn't work."
)


def record_turn(session_id: str, tool_call_count: int) -> None:
    """Called by the agent loop at end of turn."""
    if not session_id:
        return
    with _lock:
        if len(_last_turn_tool_calls) >= _MAX_TRACKED_SESSIONS:
            # Cheap bound: drop the oldest half. Sessions are rolling
            # ids; stale keys are one-restart garbage anyway.
            for key in list(_last_turn_tool_calls)[: _MAX_TRACKED_SESSIONS // 2]:
                _last_turn_tool_calls.pop(key, None)
        _last_turn_tool_calls[session_id] = tool_call_count


def pending_nudge(session_id: str) -> str | None:
    """One-shot: returns the nudge if last turn was heavy, then clears."""
    if not session_id:
        return None
    with _lock:
        n = _last_turn_tool_calls.pop(session_id, 0)
    if n >= NUDGE_THRESHOLD:
        return NUDGE_TEXT.format(n=n)
    return None


def _reset_for_tests() -> None:
    with _lock:
        _last_turn_tool_calls.clear()
