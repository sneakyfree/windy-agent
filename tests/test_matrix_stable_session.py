"""Windy Chat keeps one thread per room across restarts (Hub, 10-12).

The Matrix channel used to key each room to a fresh uuid4 held in memory, so every restart or
deploy started an empty conversation. It now uses the rolling ``matrix:<room_id>:v<N>`` id that
Telegram and the wake-up catch-up already use.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.agent import session_reset
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_prior_session_episodes, get_recent_episodes, save_episode
from windyfly.memory.write_queue import WriteQueue

ROOM = "!dmroom:chat.windychat.ai"
OTHER = "!other:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _counters(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_SESSION_COUNTER_PATH", str(tmp_path / "counters.json"))
    session_reset._reset_module_state_for_tests()
    yield
    session_reset._reset_module_state_for_tests()


def _config() -> dict:
    return {
        "agent": {"default_model": "gpt-4o-mini"},
        "memory": {"db_path": ":memory:"},
        "matrix": {"homeserver": "https://chat.windychat.ai", "bot_user": "@windyfly:chat.windychat.ai"},
    }


def _bot(db: Database) -> WindyFlyMatrixBot:
    bot = WindyFlyMatrixBot(_config(), db, WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


async def _session_after_one_message(db: Database, room_id: str, bot: WindyFlyMatrixBot | None = None) -> str:
    """One message handled; a fresh bot object (= a fresh process) unless one is passed."""
    bot = bot or _bot(db)
    room = MagicMock()
    room.room_id = room_id
    room.user_name.return_value = "Owner"
    event = MagicMock()
    event.sender = "@owner:chat.windychat.ai"
    event.body = "Hi"
    event.server_timestamp = time.time() * 1000
    event.source = {}
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="Hello"):
        await bot._on_message(room, event)
    return bot._room_sessions[room_id]


@pytest.mark.asyncio
async def test_a_restart_keeps_the_room_thread():
    db = Database(":memory:")
    before = await _session_after_one_message(db, ROOM)
    after = await _session_after_one_message(db, ROOM)
    assert before == after == f"matrix:{ROOM}:v0"
    db.close()


@pytest.mark.asyncio
async def test_what_was_said_before_a_restart_is_in_the_thread_after_it():
    db = Database(":memory:")
    sid = await _session_after_one_message(db, ROOM)
    save_episode(db, "user", "My granddaughter's recital is Friday.", session_id=sid)
    sid_after_restart = await _session_after_one_message(db, ROOM)
    said = [e["content"] for e in get_recent_episodes(db, session_id=sid_after_restart)]
    assert "My granddaughter's recital is Friday." in said
    db.close()


@pytest.mark.asyncio
async def test_new_moves_only_that_room_to_a_fresh_thread_and_the_catch_up_finds_the_old_one():
    db = Database(":memory:")
    old = await _session_after_one_message(db, ROOM)
    save_episode(db, "user", "Remind me about the recital.", session_id=old)
    session_reset.reset_session("matrix", ROOM)
    new = await _session_after_one_message(db, ROOM)
    assert new == f"matrix:{ROOM}:v1"
    assert await _session_after_one_message(db, OTHER) == f"matrix:{OTHER}:v0"
    woke = [e["content"] for e in get_prior_session_episodes(db, new, char_budget=2000)]
    assert "Remind me about the recital." in woke
    db.close()


@pytest.mark.asyncio
async def test_new_takes_effect_without_a_restart():
    db = Database(":memory:")
    bot = _bot(db)
    assert await _session_after_one_message(db, ROOM, bot) == f"matrix:{ROOM}:v0"
    session_reset.reset_session("matrix", ROOM)
    assert await _session_after_one_message(db, ROOM, bot) == f"matrix:{ROOM}:v1"
    db.close()


def test_a_room_id_with_colons_parses_whole():
    assert session_reset.parse_session_id(f"matrix:{ROOM}:v2") == ("matrix", ROOM, 2)
    assert session_reset.parse_session_id(f"matrix:{ROOM}") == ("matrix", ROOM, 0)
    assert session_reset.parse_session_id("telegram:8545546994:v3") == ("telegram", "8545546994", 3)
    assert session_reset.parse_session_id("telegram:8545546994") == ("telegram", "8545546994", 0)
    assert session_reset.parse_session_id("") == ("", "", 0)


def test_two_agents_keep_separate_counters(tmp_path, monkeypatch):
    # Each agent runs with its own HOME (zero-lab sets HOME and WINDY_STATE_DIR), so a /new in
    # one agent never moves another agent's thread, even in a room they share.
    monkeypatch.setenv("WINDYFLY_SESSION_COUNTER_PATH", str(tmp_path / "agent-a.json"))
    session_reset._reset_module_state_for_tests()
    session_reset.reset_session("matrix", ROOM)
    assert session_reset.next_session_id("matrix", ROOM) == f"matrix:{ROOM}:v1"
    monkeypatch.setenv("WINDYFLY_SESSION_COUNTER_PATH", str(tmp_path / "agent-b.json"))
    session_reset._reset_module_state_for_tests()
    assert session_reset.next_session_id("matrix", ROOM) == f"matrix:{ROOM}:v0"
