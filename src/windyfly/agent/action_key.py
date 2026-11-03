"""One idempotency key per intended outward action (Boss 2026-11-02T22:13Z hard gate, part 2).

The 11-02 lean-Fly lab restart re-ran the owner's "send" approval and the agent mailed twice. The chat side now never
re-runs a handled message (channels/handled_events.py on the lean branch); this is the second layer. Each outward send
carries ``Idempotency-Key`` = sha256(passport | the chat event that triggered this turn | route | target), so
Windy Mail (#275) and Windy Text (#125) answer a repeat with the FIRST result and never send twice, even if a
turn runs again.

The trigger is the chat message being handled, kept in a context variable. ``channels.matrix_bot`` sets it per message,
and ``agent.executor.run_turn`` carries it onto the turn thread. With no trigger (a scheduled job, a test) there is no
key, and the send behaves as before. Keys are 64 hex chars, which fits Mail (1-200 chars) and Text (8-64 of [A-Za-z0-9_-]).
"""

from __future__ import annotations

import contextvars
import hashlib
import os

_trigger: contextvars.ContextVar[str] = contextvars.ContextVar("windyfly_action_trigger", default="")


def enter(event_id: str) -> contextvars.Token[str]:
    """Mark ``event_id`` as the message this turn answers; pair with ``leave``."""
    return _trigger.set(str(event_id or ""))


def leave(token: contextvars.Token[str]) -> None:
    _trigger.reset(token)


def trigger() -> str:
    return _trigger.get()


def key(route: str, *target: str) -> str:
    """The key for one intended action, or "" when no chat message triggered this turn."""
    t = _trigger.get()
    if not t:
        return ""
    parts = (os.environ.get("ETERNITAS_PASSPORT", ""), t, route, *(str(x) for x in target))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def headers(route: str, *target: str) -> dict[str, str]:
    k = key(route, *target)
    return {"Idempotency-Key": k} if k else {}
