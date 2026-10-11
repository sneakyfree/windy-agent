"""Agent-to-agent turn budget (10-11 loop incident; spec AGENT_TURN_BUDGET_SPEC_2026-10-11).

Per room: at most LIMIT replies to other agents per WINDOW unless a human speaks; past it an
agent's message is dropped before any command or model work, and the owner is told once.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_matrix_bot import _make_config
from windyfly.channels import turn_budget as tb
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

AGENT = "@agent_et26-ab12-cd34:chat.windychat.ai"
HUMAN = "@owner:chat.windychat.ai"
ROOM = "!pair:chat.windychat.ai"
OWNER_ROOM = "!owner-dm:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("WINDY_AGENT_TURN_BUDGET", raising=False)
    monkeypatch.delenv("WINDY_AGENT_TURN_WINDOW_S", raising=False)
    tb.reset_for_tests()
    yield
    tb.reset_for_tests()


# ── the counter ──────────────────────────────────────────────────────────────────────────

def test_defaults_and_env(monkeypatch):
    assert (tb.limit(), tb.window_s()) == (6, 600.0)
    monkeypatch.setenv("WINDY_AGENT_TURN_BUDGET", "2")
    monkeypatch.setenv("WINDY_AGENT_TURN_WINDOW_S", "30")
    assert (tb.limit(), tb.window_s()) == (2, 30.0)
    monkeypatch.setenv("WINDY_AGENT_TURN_BUDGET", "0")      # never below 1
    monkeypatch.setenv("WINDY_AGENT_TURN_WINDOW_S", "soon")  # bad value -> default
    assert (tb.limit(), tb.window_s()) == (1, 600.0)


def test_limit_per_window_slides_and_rooms_are_separate():
    for i in range(6):
        assert tb.allow(ROOM, now=100.0 + i)
        tb.record(ROOM, now=100.0 + i)
    assert not tb.allow(ROOM, now=110.0)
    assert tb.allow("!other:x", now=110.0)                 # another room has its own budget
    assert tb.allow(ROOM, now=100.0 + 600.5)               # the oldest post left the window


def test_a_human_message_resets_the_budget():
    for i in range(6):
        tb.record(ROOM, now=100.0 + i)
    assert not tb.allow(ROOM, now=110.0)
    tb.human_spoke(ROOM)
    assert tb.allow(ROOM, now=110.0)


def test_owner_told_once_per_episode():
    for i in range(6):
        tb.record(ROOM, now=100.0 + i)
    assert not tb.allow(ROOM, now=110.0)
    assert tb.first_block(ROOM) is True
    assert tb.first_block(ROOM) is False                   # same episode: not again
    tb.human_spoke(ROOM)
    assert tb.first_block(ROOM) is True                    # a new episode after a human spoke


def test_room_ref_hides_the_room_id():
    assert ROOM not in tb.room_ref(ROOM) and len(tb.room_ref(ROOM)) == 12


# ── the Matrix channel ───────────────────────────────────────────────────────────────────

def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    bot._hatch_dm_room_id = OWNER_ROOM
    return bot


def _event(sender, body="hi"):
    room = MagicMock()
    room.room_id, room.display_name = ROOM, "Email helpers"
    room.user_name.return_value = "Fly Two"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp = sender, body, time.time() * 1000
    return room, ev


def _sends(bot, room_id):
    return [c for c in bot.client.room_send.await_args_list if c.args[0] == room_id]


@pytest.mark.asyncio
async def test_two_agents_stop_at_the_budget_and_the_model_is_not_called(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    bot = _bot()
    turns = AsyncMock(return_value="Sure, here is the next part of the email.")
    with patch("windyfly.agent.executor.run_turn", turns), \
         patch("windyfly.observability.events.log_event") as event:
        for _ in range(20):                                  # the other agent keeps talking
            await bot._on_message(*_event(AGENT))
    assert turns.await_count == 6                            # no model work past the budget
    assert len(_sends(bot, ROOM)) == 6
    owner = _sends(bot, OWNER_ROOM)
    assert len(owner) == 1 and "I stopped answering Fly Two in Email helpers" in owner[0].args[2]["body"]
    assert [c.args[2] for c in event.call_args_list] == ["agent.turn_budget_hit"]
    assert ROOM not in str(event.call_args_list)


@pytest.mark.asyncio
async def test_a_person_in_the_room_lets_the_agents_carry_on(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    bot = _bot()
    turns = AsyncMock(return_value="ok")
    with patch("windyfly.agent.executor.run_turn", turns), patch("windyfly.observability.events.log_event"):
        for _ in range(8):
            await bot._on_message(*_event(AGENT))
        assert turns.await_count == 6
        await bot._on_message(*_event(HUMAN, "keep going, you two"))   # a human answers too
        for _ in range(3):
            await bot._on_message(*_event(AGENT))
    assert turns.await_count == 6 + 1 + 3


@pytest.mark.asyncio
async def test_no_reply_costs_nothing(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    bot = _bot()
    turns = AsyncMock(return_value="[no reply]")
    with patch("windyfly.agent.executor.run_turn", turns), patch("windyfly.observability.events.log_event"):
        for _ in range(10):
            await bot._on_message(*_event(AGENT))
    assert turns.await_count == 10 and _sends(bot, ROOM) == []   # nothing posted, nothing counted


@pytest.mark.asyncio
async def test_humans_are_never_budgeted(monkeypatch):
    bot = _bot()
    turns = AsyncMock(return_value="hello")
    with patch("windyfly.agent.executor.run_turn", turns):
        for _ in range(12):
            await bot._on_message(*_event(HUMAN))
    assert turns.await_count == 12


# ── the names in the owner's line (Hub: never a raw id, nothing Chat/Inbox would refuse) ────────

@pytest.mark.parametrize("raw,expected", [
    ("Email helpers", "Email helpers"),
    ("  Email\n\thelpers\x00 ", "Email helpers"),                               # whitespace/control collapsed
    ("x" * 80, "x" * 59 + "…"),                                                 # capped at 60
    ("!edXNwBuzZLRCXWFmii:chat.windychat.ai", "a shared room"),                  # raw room id
    ("#ops:chat.windychat.ai", "a shared room"),                                  # alias
    ("@agent_et26-ab12-cd34:chat.windychat.ai and Grant", "a shared room"),     # member id fallback
    ("", "a shared room"),
    (None, "a shared room"),
])
def test_room_names_are_safe(raw, expected):
    assert tb.safe_name(raw, "a shared room") == expected


def test_agent_names_are_safe_too():
    line = tb.owner_notice("@agent_et26-ab12-cd34:chat.windychat.ai", "Email helpers")
    assert line.startswith("I stopped answering another agent in Email helpers:")
    line = tb.owner_notice("Fly\nTwo", "!room:server")
    assert line.startswith("I stopped answering Fly Two in a shared room:")
    assert "\n" not in line and ":server" not in line
