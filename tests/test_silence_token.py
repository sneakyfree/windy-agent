"""Agent teams: [no reply] on a turn from another agent posts nothing; owner turns are always answered."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels import silence
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from tests.test_matrix_bot import _make_config

AGENT = "@agent_et26-ab12-cd34:chat.windychat.ai"
HUMAN = "@owner:chat.windychat.ai"


@pytest.mark.parametrize("text", ["", "   ", "[no reply]", " [No Reply] ", "[no reply].", "[NO REPLY]!\n", None])
def test_silence_forms(text):
    assert silence.is_silence(text)


@pytest.mark.parametrize("text", ["ok", "[no reply] but thanks", "no reply", "I will [no reply]", "[no reply] [no reply]"])
def test_not_silence(text):
    assert not silence.is_silence(text)


def test_dark_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_TEAMS", raising=False)
    assert not silence.enabled()


def test_instruction_is_the_agreed_line():
    assert silence.INSTRUCTION == (
        "When a message from another agent needs no answer (a greeting, thanks, acknowledgement, "
        "or the task is done), reply [no reply].")


def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _event(sender):
    room = MagicMock()
    room.room_id = "!pair:chat.windychat.ai"
    room.user_name.return_value = "x"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp = sender, "hi", time.time() * 1000
    return room, ev


async def _turn(monkeypatch, sender, answer, flag="1"):
    monkeypatch.setenv("WINDY_TEAMS", flag)
    marked = []
    real_mark = silence.mark_agent_turn
    monkeypatch.setattr(silence, "mark_agent_turn", lambda sid: (marked.append(sid), real_mark(sid)))
    bot = _bot()
    room, ev = _event(sender)
    with patch("windyfly.agent.executor.run_turn", new_callable=AsyncMock, return_value=answer):
        await bot._on_message(room, ev)
    return bot, marked


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["[no reply]", "", "[No reply]."])
async def test_agent_sender_silence_posts_nothing(monkeypatch, answer):
    bot, marked = await _turn(monkeypatch, AGENT, answer)
    bot.client.room_send.assert_not_called()
    assert len(marked) == 1 and not silence.is_agent_turn(marked[0])  # cleared after the turn


@pytest.mark.asyncio
async def test_agent_sender_real_answer_is_posted(monkeypatch):
    bot, _ = await _turn(monkeypatch, AGENT, "Here is the report.")
    assert bot.client.room_send.await_count == 1
    assert bot.client.room_send.await_args.args[2]["body"] == "Here is the report."


@pytest.mark.asyncio
async def test_human_turn_is_never_silenced_by_the_matrix_layer(monkeypatch):
    bot, marked = await _turn(monkeypatch, HUMAN, "[no reply]")
    assert marked == []
    assert bot.client.room_send.await_count == 1  # the loop layer substitutes its fallback; the layer here posts what it got


@pytest.mark.asyncio
async def test_flag_off_changes_nothing(monkeypatch):
    bot, marked = await _turn(monkeypatch, AGENT, "[no reply]", flag="0")
    assert marked == [] and bot.client.room_send.await_count == 1


# ── the loop layer: the empty-answer fallback must not override a chosen silence ──────────

import tempfile
from pathlib import Path

from windyfly.agent.loop import agent_respond
from windyfly.tools.registry import ToolRegistry


def _llm_says(text):
    def call(*args, **kwargs):
        return {"content": text, "tool_calls": None, "input_tokens": 10, "output_tokens": 3}
    return call


def _run(monkeypatch, text, *, agent_turn, flag="1", session="sess-silence"):
    monkeypatch.setenv("WINDY_TEAMS", flag)
    cfg = {"agent": {"default_model": "claude-sonnet-4-6", "active_provider": "anthropic"},
           "memory": {}, "personality": {"preset": "buddy"}}
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "s.db"))
        wq = WriteQueue()
        wq.start()
        try:
            if agent_turn:
                silence.mark_agent_turn(session)
            with patch("windyfly.agent.loop.call_llm", side_effect=_llm_says(text)) as llm:
                out = agent_respond(cfg, db, wq, "hello from a sibling", session, ToolRegistry())
            return out, llm
        finally:
            silence.clear_agent_turn(session)
            wq.stop()
            db.close()


def test_loop_agent_turn_token_returns_empty_not_the_fallback(monkeypatch):
    out, _ = _run(monkeypatch, "[no reply]", agent_turn=True)
    assert out == ""


def test_loop_agent_turn_empty_answer_is_silence(monkeypatch):
    out, _ = _run(monkeypatch, "", agent_turn=True)
    assert out == ""


def test_loop_owner_turn_token_gets_the_normal_fallback(monkeypatch):
    out, _ = _run(monkeypatch, "[no reply]", agent_turn=False)
    assert out.strip() and "no reply" not in out.lower()


def test_loop_agent_turn_gets_the_instruction_line(monkeypatch):
    _, llm = _run(monkeypatch, "fine", agent_turn=True)
    sent = str(llm.call_args)
    assert silence.INSTRUCTION in sent


def test_loop_owner_turn_does_not_get_the_instruction_line(monkeypatch):
    _, llm = _run(monkeypatch, "fine", agent_turn=False)
    assert silence.INSTRUCTION not in str(llm.call_args)


def test_loop_flag_off_agent_turn_is_unchanged(monkeypatch):
    out, llm = _run(monkeypatch, "[no reply]", agent_turn=True, flag="0")
    assert out.strip()  # the old behaviour: the token is just text, nothing special
    assert silence.INSTRUCTION not in str(llm.call_args)


# ── no welcome line in a team room ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("flag,team_room,welcomed", [("1", True, False), ("1", False, True), ("0", True, True)])
async def test_welcome_is_not_posted_into_a_team_room(monkeypatch, flag, team_room, welcomed):
    from windyfly.agent import teams

    monkeypatch.setenv("WINDY_TEAMS", flag)
    monkeypatch.setattr(teams, "room_has_other_agent", lambda room_id, me: team_room)
    bot = _bot()
    bot.client.join = AsyncMock()
    bot._auto_trust_devices = AsyncMock()
    room = MagicMock()
    room.room_id = "!pair:chat.windychat.ai"
    ev = MagicMock()
    ev.state_key = bot.bot_user_id
    await bot._on_invite(room, ev)
    bot.client.join.assert_awaited_once()
    assert (bot.client.room_send.await_count == 1) is welcomed


# ── the turn text says WHO wrote it (found live: two agents answered each other ~20 times) ──

def test_frame_uses_boss_wording_names_sender_and_owner(monkeypatch):
    monkeypatch.setenv("WINDY_OWNER_NAME", "Grant")
    out = silence.frame_agent_message("Windy 0 2", "hello there")
    assert out == (
        "[Message from your fellow agent Windy 0 2, not your owner Grant] If this needs no answer, reply "
        "exactly [no reply]. Never reply to thanks, greetings or goodbyes from another agent.\nhello there")


def test_frame_without_an_owner_name_still_says_not_your_owner(monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_NAME", raising=False)
    assert silence.frame_agent_message("", "x").startswith("[Message from your fellow agent another agent, not your owner] ")


@pytest.mark.asyncio
async def test_agent_sender_turn_text_is_framed_human_turn_text_is_not(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    for sender, framed in ((AGENT, True), (HUMAN, False)):
        bot = _bot()
        room, ev = _event(sender)
        room.user_name.return_value = "Windy 0 2"
        with patch("windyfly.agent.executor.run_turn", new_callable=AsyncMock, return_value="ok") as rt:
            await bot._on_message(room, ev)
        text = rt.await_args.args[4]
        assert text.startswith("[Message from your fellow agent Windy 0 2, not your owner") is framed
        if not framed:
            assert text == "hi"


@pytest.mark.asyncio
@pytest.mark.parametrize("sender,flag,answered", [(AGENT, "1", False), (HUMAN, "1", True), (AGENT, "0", True)])
async def test_a_restart_never_replays_an_agent_backlog(monkeypatch, sender, flag, answered):
    monkeypatch.setenv("WINDY_TEAMS", flag)
    bot = _bot()
    room, ev = _event(sender)
    ev.server_timestamp = (bot._boot_time - 20) * 1000  # sent 20 s BEFORE this process started
    with patch("windyfly.agent.executor.run_turn", new_callable=AsyncMock, return_value="ok") as rt:
        await bot._on_message(room, ev)
    assert (rt.await_count == 1) is answered
