"""Invite gate (Boss ruling 10-09): join only the owner's invites and same-owner sibling agents' (Chat says)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from windyfly.agent import teams
from windyfly.channels import invite_gate
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

OWNER = "@grant:chat.windychat.ai"
SIBLING = "@agent_et26-test-abcd:chat.windychat.ai"
ME = "@windyfly:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _owner(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "owners.json"))  # never this machine's bindings
    monkeypatch.setenv("WINDY_INVITE_GATE", "1")


def _chat(monkeypatch, status=200, data=None, exc=None):
    calls = []

    def fake(method, route, body=None, params=None):
        calls.append((method, route, params))
        if exc:
            raise exc
        return status, ({"ok": True} if data is None else data)

    monkeypatch.setattr(teams, "call", fake)
    return calls


def test_owner_joins_without_asking_chat(monkeypatch):
    calls = _chat(monkeypatch)
    assert invite_gate.decide(OWNER, "!r:x") == "join"
    assert calls == []


def test_stranger_is_ignored(monkeypatch):
    calls = _chat(monkeypatch)
    assert invite_gate.decide("@stranger:matrix.org", "!r:x") == "ignore"
    assert calls == []


def test_sibling_asks_chat_with_room_and_inviter(monkeypatch):
    calls = _chat(monkeypatch)
    assert invite_gate.decide(SIBLING, "!r:x") == "join"
    assert calls == [("GET", "pair-room/invite-check", {"room": "!r:x", "inviter": SIBLING})]


@pytest.mark.parametrize("status,error", [(403, "not_siblings"), (404, "unknown_agent"),
                                          (410, "claim_expired"), (403, "stranger_in_room")])
def test_chat_refusal_is_definitive(monkeypatch, status, error):
    _chat(monkeypatch, status, {"ok": False, "error": error})
    assert invite_gate.decide(SIBLING, "!r:x") == "ignore"


def test_ok_false_on_200_is_not_a_join(monkeypatch):
    _chat(monkeypatch, 200, {"ok": False})
    assert invite_gate.decide(SIBLING, "!r:x") == "retry"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_chat_busy_or_5xx_is_retry(monkeypatch, status):
    _chat(monkeypatch, status, {})
    assert invite_gate.decide(SIBLING, "!r:x") == "retry"


def test_chat_unreachable_is_retry(monkeypatch):
    _chat(monkeypatch, exc=RuntimeError("I couldn't reach Windy Chat just now."))
    assert invite_gate.decide(SIBLING, "!r:x") == "retry"


def test_no_matrix_owner_known_keeps_first_contact(monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_IDS")
    _chat(monkeypatch)
    assert invite_gate.decide("@someone:matrix.org", "!r:x") == "join"


def test_flag_off_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_INVITE_GATE")
    assert invite_gate.enabled() is False


# ── wired into the Matrix bot ────────────────────────────────────────────────────────────

def _bot():
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot

    from tests.test_matrix_bot import _make_config

    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.join = AsyncMock()
    bot.client.room_send = AsyncMock()
    bot.client.room_leave = AsyncMock()
    return bot


def _invite(sender, room_id="!r:x"):
    room = MagicMock()
    room.room_id = room_id
    event = MagicMock()
    event.state_key = ME
    event.sender = sender
    return room, event


@pytest.mark.asyncio
async def test_bot_stranger_invite_gets_silence(monkeypatch):
    _chat(monkeypatch)
    bot = _bot()
    bot.bot_user_id = ME
    await bot._on_invite(*_invite("@stranger:matrix.org"))
    bot.client.join.assert_not_called()
    bot.client.room_send.assert_not_called()
    bot.client.room_leave.assert_not_called()


@pytest.mark.asyncio
async def test_bot_owner_invite_joins(monkeypatch):
    _chat(monkeypatch)
    bot = _bot()
    bot.bot_user_id = ME
    await bot._on_invite(*_invite(OWNER))
    bot.client.join.assert_called_once_with("!r:x")


@pytest.mark.asyncio
async def test_bot_rechecks_on_next_sync_then_joins(monkeypatch):
    _chat(monkeypatch, 503, {})
    bot = _bot()
    bot.bot_user_id = ME
    await bot._on_invite(*_invite(SIBLING))
    bot.client.join.assert_not_called()
    assert "!r:x" in bot._invite_rechecks

    _chat(monkeypatch)  # Chat is back
    await bot._on_sync_response(MagicMock())
    bot.client.join.assert_called_once_with("!r:x")
    assert bot._invite_rechecks == {}


@pytest.mark.asyncio
async def test_bot_gives_up_rechecking_after_ten_minutes(monkeypatch):
    calls = _chat(monkeypatch, 503, {})
    bot = _bot()
    bot.bot_user_id = ME
    room, event = _invite(SIBLING)
    bot._invite_rechecks["!r:x"] = (room, event, 0.0)  # first seen long ago
    await bot._on_sync_response(MagicMock())
    assert bot._invite_rechecks == {}
    assert calls == []
    bot.client.join.assert_not_called()


@pytest.mark.asyncio
async def test_bot_flag_off_joins_anyone(monkeypatch):
    monkeypatch.delenv("WINDY_INVITE_GATE")
    bot = _bot()
    bot.bot_user_id = ME
    await bot._on_invite(*_invite("@stranger:matrix.org"))
    bot.client.join.assert_called_once_with("!r:x")
