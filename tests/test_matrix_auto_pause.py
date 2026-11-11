"""The burn-rate guard runs on Windy Chat too.

``spend_monitor.maybe_auto_pause`` (an agent in a runaway loop pauses ITSELF and tells its owner) was called only
from the Telegram channel's heartbeat. An agent that lives on Windy Chat alone, which is every agent brought home,
had no guard at all. The Matrix channel now asks it every few minutes and tells the owner in their direct room.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from windyfly.agent import spend_monitor
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

from tests.test_matrix_bot import _make_config

ROOM = "!owner:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _own_pause_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.delenv("WINDY_BURN_AUTOPAUSE_USD_PER_HOUR", raising=False)
    yield


def _ledger(tmp_path, calls: int, cost: float) -> Database:
    db = Database(str(tmp_path / "burn.db"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(calls):
        db.execute(
            "INSERT INTO cost_ledger (id, model, input_tokens, output_tokens, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (f"row{i}", "claude-sonnet-4-6", 1000, 500, cost, now))
    db.commit()
    return db


def _bot(db, room: str | None = ROOM) -> WindyFlyMatrixBot:
    bot = WindyFlyMatrixBot(_make_config(), db, WriteQueue())
    bot.client.room_send = AsyncMock()
    bot._hatch_dm_room_id = room
    return bot


@pytest.mark.asyncio
async def test_a_runaway_burn_rate_pauses_the_agent_and_tells_the_owner_in_their_room(tmp_path):
    db = _ledger(tmp_path, 50, 0.10)                       # $5 in 15 minutes: about $20 an hour
    bot = _bot(db)
    try:
        assert await bot._auto_pause_check() is True
        assert spend_monitor.is_paused()
        bot.client.room_send.assert_awaited_once()
        room, kind, content = bot.client.room_send.await_args.args
        assert room == ROOM and kind == "m.room.message"
        assert content["body"] == spend_monitor.auto_pause_notice({"current_hourly": 20.0, "threshold": 5.0})
        assert "/resume" in content["body"] and "/spend" in content["body"]
        # the next look sees "already paused": no second pause, no second message
        assert await bot._auto_pause_check() is False
        bot.client.room_send.assert_awaited_once()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_an_ordinary_burn_rate_does_nothing(tmp_path):
    db = _ledger(tmp_path, 5, 0.001)
    bot = _bot(db)
    try:
        assert await bot._auto_pause_check() is False
        assert not spend_monitor.is_paused()
        bot.client.room_send.assert_not_awaited()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_with_no_room_yet_the_agent_still_pauses(tmp_path):
    db = _ledger(tmp_path, 50, 0.10)
    bot = _bot(db, room=None)
    try:
        assert await bot._auto_pause_check() is True and spend_monitor.is_paused()
        bot.client.room_send.assert_not_awaited()
    finally:
        db.close()


@pytest.mark.asyncio
async def test_a_failed_message_or_a_failed_read_never_ends_the_guard(tmp_path, monkeypatch):
    db = _ledger(tmp_path, 50, 0.10)
    bot = _bot(db)
    bot.client.room_send = AsyncMock(side_effect=RuntimeError("homeserver down"))
    try:
        assert await bot._auto_pause_check() is True       # paused; the failed message is logged, not raised
        monkeypatch.setattr(spend_monitor, "maybe_auto_pause", lambda _db: (_ for _ in ()).throw(OSError("disk")))
        assert await bot._auto_pause_check() is False
    finally:
        db.close()


@pytest.mark.asyncio
async def test_the_loop_keeps_asking_until_the_channel_stops(tmp_path, monkeypatch):
    db = _ledger(tmp_path, 5, 0.001)
    bot = _bot(db)
    looks: list[int] = []

    async def look() -> bool:
        looks.append(1)
        if len(looks) == 3:
            bot._shutting_down = True
        return False

    monkeypatch.setattr(spend_monitor, "AUTO_PAUSE_CHECK_S", 0.01)
    monkeypatch.setattr(bot, "_auto_pause_check", look)
    try:
        await asyncio.wait_for(bot._auto_pause_loop(), timeout=5)
        assert len(looks) == 3
    finally:
        db.close()


def test_the_channel_starts_the_guard_and_stops_it():
    """start() creates the task beside the heartbeat, and the graceful shutdown cancels it."""
    import inspect

    start = inspect.getsource(WindyFlyMatrixBot.start)
    assert "self._auto_pause_task = asyncio.create_task(self._auto_pause_loop())" in start
    assert "self._auto_pause_task.cancel()" in inspect.getsource(WindyFlyMatrixBot._graceful_shutdown)


LEGAL_55 = ("⚠️ I paused myself because I was spending too fast.\n\n"
            "In the last few minutes I was spending at $20.00 an hour, and I pause myself at $5.00 an hour.\n\n"
            "Until you say /resume, I won't use the AI model, which is where this spending came from. "
            "Say /spend to see where the money went.")


def test_the_notice_is_legals_words_word_for_word():
    """LEGAL DECISION 55 (BOARD 2026-11-11T06:43:52Z), typed out here on purpose: a change to the function fails."""
    assert spend_monitor.auto_pause_notice({"current_hourly": 20.0, "threshold": 5.0}) == LEGAL_55
    for word in ("LLM", "burn rate", "threshold", "*"):
        assert word not in LEGAL_55


def test_every_channel_says_the_one_copy():
    """No second copy of the words: the Telegram channel calls the same function and sends it as plain text."""
    import inspect

    from windyfly.channels import telegram_bot

    src = inspect.getsource(telegram_bot.TelegramChannel._maybe_auto_pause)
    assert "auto_pause_notice(result)" in src and "parse_mode" not in src
    assert "LLM calls" not in src and "protect your wallet" not in src
