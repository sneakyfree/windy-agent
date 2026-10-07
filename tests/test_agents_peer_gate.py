"""/agents [on|off] and the peer gate (same meaning as the roster's; Chat's lib/peer-gate.js):
OFF blocks a PEER agent only, never stored / never fed to the model, one canned line per (sender, room) per hour;
the owner's choice is persisted; the owner, humans and the owner's own agents are never touched."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_matrix_bot import _make_config
from windyfly.channels import agent_peers
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.commands import core
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

PEER = "@agent_et26-peer-0001:chat.windychat.ai"
SIBLING = "@agent_et26-sibl-0002:chat.windychat.ai"
OWNER = "@owner:chat.windychat.ai"
ROOM = "!room:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.delenv("WINDY_TEAMS", raising=False)
    agent_peers._reset_for_tests()
    core.init_core()


async def _say(text: str, sender: str = OWNER) -> str:
    from windyfly.channels.base import handle_incoming
    return (await handle_incoming(text, {"platform": "matrix", "channel_id": ROOM, "sender_id": sender}))[1]


# ── the command ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_default_is_on_and_the_reply_says_the_state():
    assert agent_peers.policy() == "on"
    reply = await _say("/agents")
    assert "**on**" in reply and "/agents off" in reply


@pytest.mark.asyncio
async def test_off_and_on_state_the_state_after_the_change_and_persist_across_a_restart(tmp_path):
    assert "**off**" in await _say("/agents off")
    assert agent_peers.policy() == "off"
    agent_peers._reset_for_tests()  # a restart forgets memory, not the file
    assert agent_peers.policy() == "off" and "**off**" in await _say("/agents")
    assert (tmp_path / "state" / "agents_policy.json").stat().st_mode & 0o777 == 0o600
    assert "**on**" in await _say("/agents on")
    assert agent_peers.policy() == "on"


@pytest.mark.asyncio
async def test_anything_else_changes_nothing_and_says_how():
    await _say("/agents off")
    assert await _say("/agents maybe") == agent_peers.HINT and agent_peers.policy() == "off"


@pytest.mark.asyncio
async def test_only_the_owner_can_change_it_a_peer_or_stranger_cannot():
    for sender in (PEER, "@stranger:chat.windychat.ai"):
        reply = await _say("/agents off", sender=sender)
        assert "owner" in reply.lower() and "only" in reply.lower()
    assert agent_peers.policy() == "on"


def test_an_unreadable_file_means_the_default(tmp_path):
    p = tmp_path / "state"
    p.mkdir()
    (p / "agents_policy.json").write_text("{not json", encoding="utf-8")
    assert agent_peers.policy() == "on"


def test_the_notice_goes_once_per_sender_and_room_per_hour():
    t = 1_000_000.0
    assert agent_peers.should_notify(PEER, ROOM, now=t)
    assert not agent_peers.should_notify(PEER, ROOM, now=t + 3599)
    assert agent_peers.should_notify(PEER, "!other:x", now=t + 10)      # another room
    assert agent_peers.should_notify(SIBLING, ROOM, now=t + 10)         # another sender
    assert agent_peers.should_notify(PEER, ROOM, now=t + 3600)          # an hour later


# ── the gate in the Matrix channel ───────────────────────────────────

def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _event(sender, body="hello there"):
    room = MagicMock()
    room.room_id = ROOM
    room.user_name.return_value = "x"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp = sender, body, time.time() * 1000
    ev.source = {"content": {"msgtype": "m.text", "body": body}}
    return room, ev


async def _deliver(sender, *, policy, body="hello there", teams_siblings=(), times=1):
    agent_peers.set_policy(policy)
    bot = _bot()
    turn = AsyncMock(return_value="an answer")
    handler = AsyncMock(return_value=(False, ""))
    with patch("windyfly.agent.executor.run_turn", turn), \
         patch("windyfly.channels.base.handle_incoming", handler), \
         patch("windyfly.channels.identity._teams_siblings", lambda: frozenset(teams_siblings)), \
         patch("windyfly.agent.teams.siblings_stale", lambda: False):
        for _ in range(times):
            room, ev = _event(sender, body)
            await bot._on_message(room, ev)
    return bot, turn, handler


@pytest.mark.asyncio
async def test_off_a_peer_is_not_heard_and_gets_one_canned_line(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    bot, turn, handler = await _deliver(PEER, policy="off", times=3)
    turn.assert_not_called()            # never fed to the model
    handler.assert_not_called()         # not even treated as a command
    assert bot.client.room_send.await_count == 1  # one line for three messages
    sent = bot.client.room_send.await_args.args[2]
    assert sent["body"] == agent_peers.NOTICE and "ai.windy.command_reply" not in sent


@pytest.mark.asyncio
async def test_off_a_peers_command_is_not_answered_either(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    bot, turn, handler = await _deliver(PEER, policy="off", body="/status")
    handler.assert_not_called()
    turn.assert_not_called()
    assert bot.client.room_send.await_args.args[2]["body"] == agent_peers.NOTICE


@pytest.mark.asyncio
async def test_on_a_peer_is_answered_as_before():
    _, turn, _ = await _deliver(PEER, policy="on")
    assert turn.await_count == 1


@pytest.mark.asyncio
async def test_off_the_owner_is_never_touched():
    _, turn, _ = await _deliver(OWNER, policy="off")
    assert turn.await_count == 1


@pytest.mark.asyncio
async def test_off_the_owners_own_agent_is_never_touched(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    bot, turn, _ = await _deliver(SIBLING, policy="off", teams_siblings=(SIBLING,))
    assert turn.await_count == 1
    assert all(c.args[2]["body"] != agent_peers.NOTICE for c in bot.client.room_send.await_args_list)


@pytest.mark.asyncio
async def test_off_a_sibling_is_decided_on_fresh_data(monkeypatch):
    """The sibling list is refreshed BEFORE the gate decides, so a not-yet-cached sibling is not blocked."""
    monkeypatch.setenv("WINDY_TEAMS", "1")
    agent_peers.set_policy("off")
    bot = _bot()
    cache = {"siblings": frozenset()}
    refreshed = []

    def refresh():
        refreshed.append(1)
        cache["siblings"] = frozenset({SIBLING})

    with patch("windyfly.agent.executor.run_turn", AsyncMock(return_value="ok")) as turn, \
         patch("windyfly.channels.base.handle_incoming", AsyncMock(return_value=(False, ""))), \
         patch("windyfly.channels.identity._teams_siblings", lambda: cache["siblings"]), \
         patch("windyfly.agent.teams.siblings_stale", lambda: not refreshed), \
         patch("windyfly.agent.teams.refresh_siblings", refresh):
        room, ev = _event(SIBLING)
        await bot._on_message(room, ev)
    assert refreshed and turn.await_count == 1
