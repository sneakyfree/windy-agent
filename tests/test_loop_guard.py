"""Loop guard (dark, WINDY_LOOP_GUARD=1): agent run cap, hourly turns cap, owner never blocked."""
from windyfly.channels import loop_guard as lg

AGENT = "@agent_et26-test-aaaa:chat.windychat.ai"
HUMAN = "@bob:chat.windychat.ai"


class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def test_off_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_LOOP_GUARD", raising=False)
    assert lg.enabled() is False
    monkeypatch.setenv("WINDY_LOOP_GUARD", "1")
    assert lg.enabled() is True


def test_agent_run_capped_at_six_then_owner_resets():
    g = lg.LoopGuard(max_agent_run=6, max_turns_per_hour=1000)
    for _ in range(6):
        assert g.check("!r", AGENT, is_owner=False) == lg.REPLY
    assert g.check("!r", AGENT, is_owner=False) == lg.IGNORE_AGENT_RUN
    assert g.check("!r", AGENT, is_owner=False) == lg.IGNORE_AGENT_RUN
    assert g.check("!r", "@owner:x", is_owner=True) == lg.REPLY  # owner speaks: reset
    assert g.check("!r", AGENT, is_owner=False) == lg.REPLY


def test_agent_run_is_per_room_and_a_human_resets_it():
    g = lg.LoopGuard(max_agent_run=2, max_turns_per_hour=1000)
    assert g.check("!a", AGENT, is_owner=False) == lg.REPLY
    assert g.check("!a", AGENT, is_owner=False) == lg.REPLY
    assert g.check("!a", AGENT, is_owner=False) == lg.IGNORE_AGENT_RUN
    assert g.check("!b", AGENT, is_owner=False) == lg.REPLY  # another room
    assert g.check("!a", HUMAN, is_owner=False) == lg.REPLY  # a human spoke
    assert g.check("!a", AGENT, is_owner=False) == lg.REPLY


def test_turns_cap_pauses_once_and_notice_fires_once(tmp_path):
    clk = Clock()
    g = lg.LoopGuard(tmp_path / "s.json", max_agent_run=6, max_turns_per_hour=5, clock=clk)
    trips = [g.record_reply(owner_turn=False) for _ in range(7)]
    assert trips == [False, False, False, False, True, False, False]
    assert g.paused
    assert g.check("!r", HUMAN, is_owner=False) == lg.IGNORE_PAUSED
    assert g.check("!r", "@owner:x", is_owner=True) == lg.REPLY  # the owner is never blocked
    assert "5 replies in an hour" in g.trip_notice() and lg.RESUME_COMMAND in g.trip_notice()


def test_owner_turns_never_count():
    g = lg.LoopGuard(max_turns_per_hour=3)
    for _ in range(50):
        assert g.record_reply(owner_turn=True) is False
    assert not g.paused


def test_old_replies_roll_off():
    clk = Clock()
    g = lg.LoopGuard(max_turns_per_hour=3, clock=clk)
    g.record_reply(owner_turn=False)
    g.record_reply(owner_turn=False)
    clk.t += 3601
    assert g.record_reply(owner_turn=False) is False
    assert not g.paused


def test_pause_survives_restart_and_clear_resumes(tmp_path):
    p = tmp_path / "s.json"
    g = lg.LoopGuard(p, max_turns_per_hour=2)
    g.record_reply(owner_turn=False)
    assert g.record_reply(owner_turn=False) is True
    g2 = lg.LoopGuard(p, max_turns_per_hour=2)  # a restart
    assert g2.paused
    assert g2.check("!r", HUMAN, is_owner=False) == lg.IGNORE_PAUSED
    g2.clear()
    assert not lg.LoopGuard(p, max_turns_per_hour=2).paused
    assert g2.check("!r", HUMAN, is_owner=False) == lg.REPLY


# ── wired into the Matrix bot ────────────────────────────────────────────
import time  # noqa: E402
from unittest.mock import AsyncMock, MagicMock, patch  # noqa: E402

import pytest  # noqa: E402

from windyfly.channels.matrix_bot import WindyFlyMatrixBot  # noqa: E402
from windyfly.memory.database import Database  # noqa: E402
from windyfly.memory.write_queue import WriteQueue  # noqa: E402


def _bot(tmp_path, monkeypatch, owner="@owner:chat.windychat.ai"):
    monkeypatch.setenv("WINDY_LOOP_GUARD", "1")
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{owner}")
    bot = WindyFlyMatrixBot({"matrix": {"homeserver": "https://x", "user_id": "@windyfly:chat.windychat.ai",
                                        "access_token": "t"}}, Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _msg(sender, body="hi"):
    room = MagicMock()
    room.room_id = "!r:chat.windychat.ai"
    room.user_name.return_value = "x"
    ev = MagicMock()
    ev.sender, ev.body = sender, body
    ev.server_timestamp = time.time() * 1000
    return room, ev


@pytest.mark.asyncio
async def test_bot_ignores_the_seventh_agent_message_until_the_owner_speaks(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch)
    with patch("windyfly.agent.executor.run_turn", new=AsyncMock(return_value="ok")) as run:
        for _ in range(7):
            await bot._on_message(*_msg(AGENT))
        assert run.await_count == 6
        await bot._on_message(*_msg("@owner:chat.windychat.ai"))
        assert run.await_count == 7
        await bot._on_message(*_msg(AGENT))
        assert run.await_count == 8


@pytest.mark.asyncio
async def test_bot_off_by_default_never_blocks(tmp_path, monkeypatch):
    bot = _bot(tmp_path, monkeypatch)
    monkeypatch.delenv("WINDY_LOOP_GUARD")
    with patch("windyfly.agent.executor.run_turn", new=AsyncMock(return_value="ok")) as run:
        for _ in range(9):
            await bot._on_message(*_msg(AGENT))
        assert run.await_count == 9


@pytest.mark.asyncio
async def test_agent_bound_as_owner_by_tofu_is_still_guarded(tmp_path, monkeypatch):
    """Trust-On-First-Use binds the FIRST sender as owner; if that is an agent the guard
    must still cap its run (an agent account is never 'the owner' for the guard)."""
    bot = _bot(tmp_path, monkeypatch)
    monkeypatch.delenv("WINDY_OWNER_IDS")
    monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "owners.json"))
    with patch("windyfly.agent.executor.run_turn", new=AsyncMock(return_value="ok")) as run:
        for _ in range(9):
            await bot._on_message(*_msg(AGENT))
        assert run.await_count == 6
