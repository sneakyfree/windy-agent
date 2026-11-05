"""The Matrix typing sign stays on for the whole turn (Boss 11-05, Grant's honest-progress rule).

Matrix drops a typing notice after the timeout we send (15 s); Zero's turns took ~85 s on 11-05, so the person saw
'typing' for 15 s and then nothing. The bot now re-sends it every _TYPING_REFRESH_S until the turn ends, and the
refresher always stops: after a normal turn, after a turn that raises, and a failed send never kills it.
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels import matrix_bot as mb
from windyfly.channels.base import stop_side_task
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

from tests.test_matrix_bot import _make_config


def _bot(db):
    bot = WindyFlyMatrixBot(_make_config(), db, WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _room_event():
    room = MagicMock()
    room.room_id = "!test:chat.windychat.ai"
    room.user_name.return_value = "Grandma"
    event = MagicMock()
    event.sender = "@user:chat.windychat.ai"
    event.body = "what's the weather?"
    event.server_timestamp = time.time() * 1000
    return room, event


def _typing_on(bot) -> int:
    return sum(1 for c in bot.client.room_typing.await_args_list if c.args[1:2] == (True,))


@pytest.mark.asyncio
@patch.object(mb, "_TYPING_REFRESH_S", 0.01)
@patch("windyfly.agent.executor.run_turn", new_callable=AsyncMock)
@patch("windyfly.channels.matrix_bot.agent_respond")
async def test_a_slow_turn_keeps_the_typing_sign_on_until_it_ends(mock_respond, mock_run_turn):
    async def slow(*a, **k):
        await asyncio.sleep(0.08)
        return "Sunny."
    mock_run_turn.side_effect = slow
    db = Database(":memory:")
    bot = _bot(db)
    await bot._on_message(*_room_event())
    assert _typing_on(bot) >= 4          # the first sign + refreshes while the turn ran
    assert bot.client.room_typing.await_args_list[-1].args[1:2] == (False,)   # cleared at the end
    for c in bot.client.room_typing.await_args_list:
        if c.args[1:2] == (True,):
            assert c.kwargs.get("timeout") == mb._TYPING_TIMEOUT_MS
    after = bot.client.room_typing.await_count
    await asyncio.sleep(0.05)
    assert bot.client.room_typing.await_count == after   # the refresher stopped with the turn
    db.close()


@pytest.mark.asyncio
@patch.object(mb, "_TYPING_REFRESH_S", 0.01)
@patch("windyfly.agent.executor.run_turn", new_callable=AsyncMock)
@patch("windyfly.channels.matrix_bot.agent_respond")
async def test_a_turn_that_fails_still_stops_the_refresher(mock_respond, mock_run_turn):
    async def boom(*a, **k):
        await asyncio.sleep(0.03)
        raise RuntimeError("provider down")
    mock_run_turn.side_effect = boom
    db = Database(":memory:")
    bot = _bot(db)
    await bot._on_message(*_room_event())
    after = bot.client.room_typing.await_count
    await asyncio.sleep(0.05)
    assert bot.client.room_typing.await_count == after
    assert bot.client.room_typing.await_args_list[-1].args[1:2] == (False,)
    db.close()


@pytest.mark.asyncio
@patch.object(mb, "_TYPING_REFRESH_S", 0.01)
async def test_a_failed_typing_send_does_not_kill_the_refresher():
    db = Database(":memory:")
    bot = _bot(db)
    bot.client.room_typing = AsyncMock(side_effect=[RuntimeError("network"), None, None, None, None, None, None])
    task = asyncio.create_task(bot._keep_typing("!r:x"))
    await asyncio.sleep(0.06)
    await stop_side_task(task)
    assert task.done()
    assert bot.client.room_typing.await_count >= 2   # it kept going after the first failure
    db.close()


@pytest.mark.asyncio
async def test_the_refresher_sleeps_first_so_a_fast_turn_sends_one_sign():
    db = Database(":memory:")
    bot = _bot(db)
    task = asyncio.create_task(bot._keep_typing("!r:x"))
    await asyncio.sleep(0)
    await stop_side_task(task)
    assert bot.client.room_typing.await_count == 0
    db.close()
