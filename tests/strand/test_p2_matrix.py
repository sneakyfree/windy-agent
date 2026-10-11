"""Strand v2, Part 2 genes 1.1-1.3: the Matrix channel (docs/strand/STRAND_V2.md).

The homeserver is a mock; these prove what the channel does around a turn. No network, no home.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_matrix_bot import _make_config
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

ROOM = "!dm:chat.example.test"


def _bot():
    return WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())


def _event(body="hello"):
    room = MagicMock()
    room.room_id = ROOM
    room.user_name.return_value = "Grant"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp = "@owner:chat.example.test", body, time.time() * 1000
    ev.source = {"content": {}}
    return room, ev


@pytest.mark.asyncio
@patch.dict("os.environ", {"MATRIX_BOT_PASSWORD": "pw"}, clear=True)
@patch("windyfly.chat_session.fetch_agent_chat_session", new_callable=AsyncMock, return_value=None)
async def test_p2_11_password_login_with_a_user(_session):
    bot = _bot()
    bot.bot_user_id = "@fly:chat.example.test"
    bot.client.login = AsyncMock(return_value=MagicMock())
    bot.client.whoami = AsyncMock(return_value=MagicMock(user_id="@fly:chat.example.test"))
    await bot.login()
    bot.client.login.assert_awaited_once_with("pw")
    assert bot.bot_user_id == "@fly:chat.example.test"


@pytest.mark.asyncio
async def test_p2_12_typing_on_before_the_turn_and_off_after_the_reply():
    bot = _bot()
    order: list[str] = []
    bot.client.room_typing = AsyncMock(side_effect=lambda room_id, on, **k: order.append(f"typing:{on}"))
    bot.client.room_send = AsyncMock(side_effect=lambda *a, **k: order.append("send"))

    def respond(*a, **k):
        order.append("turn")
        return "hi Grant"

    with patch("windyfly.channels.matrix_bot.agent_respond", side_effect=respond):
        await bot._on_message(*_event())
    assert order.index("typing:True") < order.index("turn") < order.index("send") < order.index("typing:False")


@pytest.mark.asyncio
async def test_p2_12_presence_online_at_start():
    bot = _bot()
    bot.login = AsyncMock()
    bot._flush_pending = AsyncMock()
    bot._replay_offline_queue = AsyncMock()
    bot._join_hatch_dm_room = AsyncMock()

    async def _sync_forever(*a, **k):
        await asyncio.Event().wait()

    bot.client.sync_forever = _sync_forever
    bot.client.add_event_callback = MagicMock()
    bot.client.add_response_callback = MagicMock()
    bot.client.set_presence = AsyncMock()
    bot.client.close = AsyncMock()
    task = asyncio.create_task(bot.start())
    for _ in range(200):
        if bot._sync_task is not None and not bot._sync_task.done():
            break
        await asyncio.sleep(0.01)
    bot._handle_shutdown_signal(15, asyncio.get_running_loop())
    await asyncio.wait_for(task, timeout=5)
    states = [c.args[0] for c in bot.client.set_presence.await_args_list]
    assert states[0] == "online" and states[-1] == "offline"


@pytest.mark.asyncio
async def test_p2_13_a_failed_reply_is_kept_for_later():
    bot = _bot()
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock(side_effect=RuntimeError("homeserver down"))
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="hi Grant"):
        await bot._on_message(*_event())
    assert bot._pending_responses == [(ROOM, "hi Grant")]
