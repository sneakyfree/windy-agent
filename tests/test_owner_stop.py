"""The owner's stop and addressed-only agent turns (agent-teamwork.v1 P1 + P5; Grant's order 10-17).

On 10-17 two of the owner's agents looped in a group room and the owner's "stop" did not stop them. In
windyfly, #528's budget even reset on any human message, so a "stop" bought six more replies.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_matrix_bot import _make_config
from windyfly.channels import room_stop as rs
from windyfly.channels import turn_budget as tb
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

ROOM = "!trip:chat.windychat.ai"
OWNER = "@owner:example.test"
OTHER_AGENT = "@agent_et26-ab12-cd34:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    rs.reset_for_tests()
    tb.reset_for_tests()
    yield
    rs.reset_for_tests()
    tb.reset_for_tests()


def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    bot.client.room_read_markers = AsyncMock()
    return bot


def _msg(sender, body="hi", content=None, members=3):
    room = MagicMock()
    room.room_id, room.display_name, room.joined_count = ROOM, "Virgin Islands trip", members
    room.user_name.return_value = "someone"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp, ev.event_id = sender, body, time.time() * 1000, "$e"
    ev.source = {"content": {"msgtype": "m.text", "body": body, **(content or {})}}
    return room, ev


async def _run(bot, room, ev):
    calls = []

    def respond(*a, **k):
        calls.append(a[3])
        return "an answer"

    with patch("windyfly.channels.matrix_bot.agent_respond", side_effect=respond):
        await bot._on_message(room, ev)
    return calls


def _to_me(bot):
    return {"m.mentions": {"user_ids": [bot.bot_user_id]}}


@pytest.mark.asyncio
async def test_the_owners_stop_silences_agent_replies_without_a_model_call_or_a_reply():
    bot = _bot()
    assert await _run(bot, *_msg(OWNER, "Stop!")) == []                     # the stop itself: no turn
    bot.client.room_send.assert_not_awaited()                               # and no reply, so no push
    assert await _run(bot, *_msg(OTHER_AGENT, "more ideas", _to_me(bot))) == []   # silenced
    bot.client.room_send.assert_not_awaited()
    assert await _run(bot, *_msg(OWNER, "ok, carry on with the hotels")) == ["ok, carry on with the hotels"]
    assert await _run(bot, *_msg(OTHER_AGENT, "here are hotels", _to_me(bot))) != []   # resumed


@pytest.mark.asyncio
async def test_a_stop_never_resets_the_turn_budget():
    bot = _bot()
    for _ in range(tb.limit()):
        tb.record(ROOM)
    assert not tb.allow(ROOM)
    await _run(bot, *_msg(OWNER, "please stop"))
    assert not tb.allow(ROOM)                                               # still spent: no fresh replies


@pytest.mark.asyncio
async def test_the_stop_event_from_the_owner_silences_and_from_anyone_else_does_nothing():
    bot = _bot()
    room = MagicMock()
    room.room_id = ROOM
    ev = MagicMock()
    ev.type, ev.sender = rs.STOP_EVENT, OTHER_AGENT
    await bot._on_stop_event(room, ev)
    assert not rs.stopped(ROOM)
    ev.sender = OWNER
    await bot._on_stop_event(room, ev)
    assert rs.stopped(ROOM)


@pytest.mark.asyncio
async def test_the_owners_stop_works_in_guest_mode_too(monkeypatch, tmp_path):
    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    bot = _bot()
    await _run(bot, *_msg(OWNER, "stop"))
    assert rs.stopped(ROOM)


@pytest.mark.parametrize("text,stops", [
    ("stop", True), ("STOP.", True), ("please stop", True), ("enough!", True), ("be quiet", True),
    ("/pause", False), ("/stop", False), ("can you stop by the store", False), ("don't stop", False), ("", False),
])
def test_only_a_whole_stop_word_is_a_stop(text, stops):
    assert rs.is_stop_word(text) is stops


@pytest.mark.asyncio
async def test_another_agents_message_is_a_turn_only_when_it_addresses_this_agent():
    bot = _bot()
    assert await _run(bot, *_msg(OTHER_AGENT, "anyone?")) == []                       # group room, not addressed
    assert await _run(bot, *_msg(OTHER_AGENT, "you there?", _to_me(bot))) != []       # a mention
    task = {"ai.windy.task": {"type": "request", "task_id": "t1", "to": bot.bot_user_id, "ask": "find flights"}}
    assert await _run(bot, *_msg(OTHER_AGENT, "find flights", task)) != []            # a typed request to me
    other = {"ai.windy.task": {"type": "request", "task_id": "t2", "to": "@someone-else:x", "ask": "x"}}
    assert await _run(bot, *_msg(OTHER_AGENT, "x", other)) == []                      # a request to someone else
    assert await _run(bot, *_msg(OTHER_AGENT, "just us", members=2)) != []            # two of us alone


@pytest.mark.asyncio
async def test_a_person_who_is_not_the_owner_cannot_stop_the_agents():
    bot = _bot()
    await _run(bot, *_msg("@guest:example.test", "stop"))
    assert not rs.stopped(ROOM)
