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
# The ordinal of the send to each (route, target) in this trigger (Hub + Boss on #693): "text Mom I'm late, and text her
# the address" is two texts to one number from one message, so two keys; a retry of ONE send keeps its key. A re-run of
# the same turn makes the same sends in the same order, so it gets the same keys and is deduped.
_counts: contextvars.ContextVar[dict[tuple[str, ...], int] | None] = contextvars.ContextVar(
    "windyfly_action_counts", default=None)


def enter(event_id: str) -> tuple[contextvars.Token[str], contextvars.Token[dict[tuple[str, ...], int] | None]]:
    """Mark ``event_id`` as the message this turn answers (with a fresh send count); pair with ``leave``."""
    return _trigger.set(str(event_id or "")), _counts.set({})


def leave(tokens: tuple[contextvars.Token[str], contextvars.Token[dict[tuple[str, ...], int] | None]]) -> None:
    _trigger.reset(tokens[0])
    _counts.reset(tokens[1])


def trigger() -> str:
    return _trigger.get()


def key(route: str, *target: str) -> str:
    """The key for the NEXT intended action on (route, target) in this turn, or "" when no chat message triggered
    it. Call it ONCE per intended send and reuse the result on that send's own retries."""
    t = _trigger.get()
    if not t:
        return ""
    who = (route, *(str(x) for x in target))
    counts = _counts.get()
    if counts is None:
        counts = {}
        _counts.set(counts)
    # Boss 11-03 09:10Z: the ordinal moves on ONLY after the last send to this target got a DEFINITIVE answer
    # (``done``). After an unknown outcome (a timeout, a transport error, a 5xx, a 409), the next send in the turn reuses
    # the SAME key, so the server answers replay / in progress / unknown and never sends twice.
    n = counts.get(who, 0)
    if n <= 0:
        n = abs(n) + 1
        counts[who] = n          # open: positive = a send with key n has no definitive answer yet
    parts = (os.environ.get("ETERNITAS_PASSPORT", ""), t, *who, str(n))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def done(route: str, *target: str) -> None:
    """The last send to (route, target) got a DEFINITIVE answer (sent, or a final refusal): the next one is a new
    action with the next ordinal. Never call it after an unknown outcome."""
    counts = _counts.get()
    who = (route, *(str(x) for x in target))
    if counts is not None and counts.get(who, 0) > 0:
        counts[who] = -counts[who]   # closed: negative = answered; the next key() takes the next ordinal


def definitive(status: int, code: str = "") -> bool:
    """A 2xx, or a 4xx that is a final refusal. NOT definitive: 409 (in progress / unknown), 429, any 5xx, and a 422
    idempotency_key_reused (Hub on #693): that one says only THIS request sent nothing, while the first try with the key
    may have gone, so the key must stay for the next retry."""
    if code == "idempotency_key_reused":
        return False
    if status == 409:
        # Only the idempotency 409s (in progress / outcome unknown), or one with no code, are unknown. A 409 with a
        # business code (e.g. carrier_registration_pending) is a final refusal: nothing was sent.
        return bool(code) and code not in UNKNOWN_409_CODES
    return 200 <= status < 300 or (400 <= status < 500 and status != 429)


UNKNOWN_409_CODES = frozenset({"idempotency_in_progress", "idempotency_outcome_unknown", "send_in_progress"})


def headers(route: str, *target: str) -> dict[str, str]:
    k = key(route, *target)
    return {"Idempotency-Key": k} if k else {}
