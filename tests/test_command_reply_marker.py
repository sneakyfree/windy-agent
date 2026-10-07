"""ai.windy.command_reply (Windy Chat #319): a runtime's command replies are marked, and a marked
message from another AGENT never reaches the model or the history. An owner's message never is skipped."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_matrix_bot import _make_config
from windyfly.channels.matrix_bot import COMMAND_REPLY_KEY, WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

AGENT = "@agent_et26-ab12-cd34:chat.windychat.ai"
HUMAN = "@owner:chat.windychat.ai"


def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _event(sender, body="hi", content=None):
    room = MagicMock()
    room.room_id = "!pair:chat.windychat.ai"
    room.user_name.return_value = "x"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp = sender, body, time.time() * 1000
    ev.source = {"content": {"msgtype": "m.text", "body": body, **(content or {})}}
    return room, ev


async def _deliver(sender, content=None, body="hi", *, command_reply=None):
    bot = _bot()
    room, ev = _event(sender, body, content)
    turn = AsyncMock(return_value="an answer")
    handler = AsyncMock(return_value=(command_reply is not None, command_reply))
    with patch("windyfly.agent.executor.run_turn", turn), patch("windyfly.channels.base.handle_incoming", handler):
        await bot._on_message(room, ev)
    return bot, turn, handler


def test_the_key_is_chats_key():
    assert COMMAND_REPLY_KEY == "ai.windy.command_reply"


@pytest.mark.asyncio
async def test_a_marked_message_from_another_agent_is_skipped_entirely():
    bot, turn, handler = await _deliver(AGENT, {COMMAND_REPLY_KEY: True}, body="status: ok, 3 tools")
    turn.assert_not_called()          # no model turn
    handler.assert_not_called()       # not even treated as a command
    bot.client.room_send.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [False, "true", 1, None])
async def test_only_a_real_true_marks(value):
    _, turn, _ = await _deliver(AGENT, {COMMAND_REPLY_KEY: value})
    assert turn.await_count == 1


@pytest.mark.asyncio
async def test_an_unmarked_agent_message_still_gets_its_turn():
    _, turn, _ = await _deliver(AGENT)
    assert turn.await_count == 1


@pytest.mark.asyncio
async def test_an_owner_message_is_never_skipped_by_the_marker():
    _, turn, _ = await _deliver(HUMAN, {COMMAND_REPLY_KEY: True})
    assert turn.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", [HUMAN, AGENT])
async def test_our_command_reply_carries_the_marker(sender):
    bot, turn, _ = await _deliver(sender, body="!status", command_reply="Windy Zero: all good")
    turn.assert_not_called()
    content = bot.client.room_send.await_args.args[2]
    assert content["body"] == "Windy Zero: all good" and content[COMMAND_REPLY_KEY] is True


@pytest.mark.asyncio
async def test_an_ordinary_answer_is_never_marked():
    bot, _, _ = await _deliver(HUMAN, body="what's the weather")
    content = bot.client.room_send.await_args.args[2]
    assert content["body"] == "an answer" and COMMAND_REPLY_KEY not in content
