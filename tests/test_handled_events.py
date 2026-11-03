"""At most once, across restarts (Boss 2026-11-02T22:13Z hard gate). The 11-02 lean-Fly lab restart re-ran the
owner's "send" (82 s old, inside the 120 s window) and the agent mailed twice."""

from __future__ import annotations

import json
import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels import handled_events as he

ROOM = "!lab:chat.windychat.ai"


def restart() -> None:
    """A new process: nothing in memory, only the state file."""
    he.reset_cache()


def test_a_new_message_is_taken_once():
    assert he.claim(ROOM, "$a", 1000) is True
    assert he.claim(ROOM, "$a", 1000) is False


def test_a_restart_never_retakes_a_message():
    assert he.claim(ROOM, "$send", 1000) is True
    restart()
    assert he.claim(ROOM, "$send", 1000) is False


def test_an_older_message_is_never_taken_after_a_newer_one():
    assert he.claim(ROOM, "$new", 2000) is True
    restart()
    assert he.claim(ROOM, "$older-never-seen", 1500) is False


def test_a_message_that_arrived_while_down_is_answered_once():
    assert he.claim(ROOM, "$before", 1000) is True
    restart()
    assert he.claim(ROOM, "$while-down", 3000) is True
    assert he.claim(ROOM, "$while-down", 3000) is False


def test_rooms_are_separate():
    assert he.claim(ROOM, "$x", 5000) is True
    assert he.claim("!other:chat", "$y", 1000) is True


def test_a_message_without_an_id_uses_the_time_only():
    assert he.claim(ROOM, "", 1000) is True
    assert he.claim(ROOM, "", 1001) is True
    assert he.claim(ROOM, "", 900) is False


def test_the_record_is_private_and_keeps_only_the_last_ids(tmp_path):
    for i in range(he.KEEP_IDS + 5):
        assert he.claim(ROOM, f"$e{i}", 1000 + i)
    path = he._path()
    assert (os.stat(path).st_mode & 0o777) == 0o600
    rec = json.loads(path.read_text())[ROOM]
    assert len(rec["ids"]) == he.KEEP_IDS and rec["newest_ts"] == 1000 + he.KEEP_IDS + 4


def test_an_unreadable_record_starts_empty_never_crashes():
    he._path().write_text("{not json")
    restart()
    assert he.claim(ROOM, "$a", 1000) is True


def test_an_unwritable_record_still_answers(monkeypatch):
    import windyfly.state_files as sf

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(sf, "write_private", boom)
    assert he.claim(ROOM, "$a", 1000) is True


# ── end to end: the 11-02 replay, through matrix_bot._on_message ─────────────────────────────────────────────────────

def _bot():
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue

    cfg = {"matrix": {"homeserver": "https://chat.windychat.ai", "bot_user": "@agent:chat.windychat.ai"},
           "agent": {"default_model": "m"}, "memory": {"db_path": ":memory:"}, "personality": {}}
    bot = WindyFlyMatrixBot(cfg, Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _send_event(ts_ms: float):
    room = MagicMock()
    room.room_id = ROOM
    room.user_name.return_value = "Owner"
    ev = MagicMock()
    ev.sender = "@owner:chat.windychat.ai"
    ev.body = "send"
    ev.event_id = "$owner-send"
    ev.server_timestamp = ts_ms
    ev.source = {}
    return room, ev


@pytest.mark.asyncio
async def test_a_restart_mid_thread_does_not_re_run_the_owners_send():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="Sent.") as respond:
        room, ev = _send_event((time.time() - 82) * 1000)  # the 11-02 case: 82 s old at the restart
        await _bot()._on_message(room, ev)
        assert respond.call_count == 1
        restart()
        await _bot()._on_message(room, ev)  # the new process sees the same event on its first sync
        assert respond.call_count == 1


@pytest.mark.asyncio
async def test_after_a_restart_a_new_owner_message_is_still_answered():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="ok") as respond:
        room, ev = _send_event((time.time() - 82) * 1000)
        await _bot()._on_message(room, ev)
        restart()
        room2, ev2 = _send_event(time.time() * 1000)
        ev2.event_id = "$owner-new"
        await _bot()._on_message(room2, ev2)
        assert respond.call_count == 2
