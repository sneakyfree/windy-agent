"""Every chat message is acted on AT MOST ONCE, across restarts (Boss 2026-11-02T22:13Z, a hard gate).

The lean-Fly lab, 11-02: a restart re-processed the owner's "send" (82 s old, inside the 120 s catch-up window,
with no kept sync position), and the agent mailed a second time. The record below lives in the state dir and
survives restarts. Per room it keeps:
- the ids of the last messages taken;
- the newest server time among them.
A message is taken by ``claim`` BEFORE its turn runs, so a crash mid-turn never replays it: the owner can ask
again, but an approval is never spent twice. A message is a repeat when its id was taken, or when it is OLDER than
the newest message taken in that room. A message that arrived while the agent was down is newer than all of
those, so it is still answered, once.

A record that can't be read starts empty (never a crash), and the 120 s window still applies after that. A record
that can't be written still answers the message: never silent. Every write is atomic and 0600.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

FILE_NAME = "handled_events.json"
ENV_VAR = "WINDY_HANDLED_EVENTS_FILE"
KEEP_IDS = 200  # per room; older ones are covered by the newest time

_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def _path() -> Path:
    from windyfly.state_files import state_path

    return state_path(ENV_VAR, FILE_NAME)


def _load() -> dict[str, Any]:
    global _cache
    if _cache is None:
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
            _cache = data if isinstance(data, dict) else {}
        except FileNotFoundError:
            _cache = {}
        except (OSError, ValueError) as exc:
            logger.warning("handled events: unreadable record (%s); starting empty", type(exc).__name__)
            _cache = {}
    return _cache


def claim(room_id: str, event_id: str, server_ts_ms: int) -> bool:
    """True = act on this message now (it is recorded as taken first). False = already taken or older: skip."""
    with _lock:
        rooms = _load()
        rec = rooms.get(room_id)
        if not isinstance(rec, dict):
            rec = {"newest_ts": 0, "ids": []}
        raw_ids, raw_newest = rec.get("ids"), rec.get("newest_ts")
        ids: list[str] = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else []
        newest: int = raw_newest if isinstance(raw_newest, int) else 0
        if (event_id and event_id in ids) or server_ts_ms < newest:
            return False
        if event_id:
            ids = (ids + [event_id])[-KEEP_IDS:]
        rooms[room_id] = {"newest_ts": max(newest, int(server_ts_ms)), "ids": ids}
        try:
            from windyfly.state_files import write_private

            write_private(_path(), json.dumps(rooms, sort_keys=True).encode("utf-8"))
        except OSError as exc:
            logger.warning("handled events: not saved (%s); answering anyway", type(exc).__name__)
        return True


def reset_cache() -> None:
    """Tests: forget the in-memory copy (the file stays)."""
    global _cache
    with _lock:
        _cache = None
