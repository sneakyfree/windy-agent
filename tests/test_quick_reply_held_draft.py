"""quick-reply.v1 on a held email draft: [send, wait] under the owner's own question, no authority."""

from __future__ import annotations

import asyncio
import re
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.agent.capabilities import Band
from windyfly.channels import base, identity
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools import mail
from tests.test_matrix_bot import _make_config

OWNER = "@owner:chat.windychat.ai"
SIB = "@agent_et26-sib0-0002:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    mail._PENDING.clear()
    monkeypatch.setenv("WINDY_SEND_CONFIRM", "1")
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.setattr(mail, "_send_email_now", lambda *a, **k: {"status": "sent", "provider": "windymail"})
    yield
    mail._PENDING.clear()


def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


async def _turn(sender, *, draft: bool, flag: str = "1", monkeypatch=None):
    if monkeypatch:
        monkeypatch.setenv("WINDY_SEND_CONFIRM", flag)
        monkeypatch.setenv("WINDY_TEAMS", "1")
    bot = _bot()
    room = MagicMock(); room.room_id = "!dm:chat.windychat.ai"; room.user_name.return_value = "x"
    ev = MagicMock(); ev.sender, ev.body, ev.server_timestamp = sender, "email Sam the report", time.time() * 1000 + 5

    async def fake_run_turn(fn, *a, **k):
        if draft:
            mail.send_email("sam@example.com", "hi", "body")
        return "I drafted the email to Sam. Send it?"

    with patch("windyfly.agent.executor.run_turn", new=fake_run_turn):
        await bot._on_message(room, ev)
    return bot.client.room_send.await_args.args[2]


@pytest.mark.asyncio
async def test_owner_turn_that_held_a_draft_carries_send_wait(monkeypatch):
    content = await _turn(OWNER, draft=True, monkeypatch=monkeypatch)
    qr = content["ai.windy.quick_reply"]
    assert qr["options"] == ["send", "wait"] and re.fullmatch(r"qr_[0-9a-f]{12}", qr["id"])
    assert content["body"] == "I drafted the email to Sam. Send it?"


@pytest.mark.asyncio
async def test_no_buttons_without_a_new_draft_or_with_the_flag_off(monkeypatch):
    assert "ai.windy.quick_reply" not in await _turn(OWNER, draft=False, monkeypatch=monkeypatch)
    mail._PENDING.clear()
    assert "ai.windy.quick_reply" not in await _turn(OWNER, draft=True, flag="0", monkeypatch=monkeypatch)


@pytest.mark.asyncio
async def test_a_siblings_turn_never_carries_the_card(monkeypatch):
    monkeypatch.setattr(identity, "resolve_band", lambda p, s, **k: Band.TRUSTED)
    assert "ai.windy.quick_reply" not in await _turn(SIB, draft=True, monkeypatch=monkeypatch)


def _incoming(text, sender=OWNER):
    return asyncio.run(base.handle_incoming(text, {"platform": "matrix", "sender_id": sender}))


def test_wait_keeps_the_draft_and_needs_no_model_call(monkeypatch):
    monkeypatch.setattr(identity, "resolve_band", lambda p, s, **k: Band.OWNER if s == OWNER else Band.SANDBOX)
    mail.send_email("sam@example.com", "hi", "body")
    was_cmd, reply = _incoming("wait")
    assert was_cmd and "stays held" in reply and len(mail.pending_drafts()) == 1
    was_cmd, reply = _incoming("send")  # the other button
    assert was_cmd and "Sent to" in reply and not mail.pending_drafts()


def test_a_sibling_cannot_tap_wait_or_send(monkeypatch):
    monkeypatch.setattr(identity, "resolve_band", lambda p, s, **k: Band.OWNER if s == OWNER else Band.TRUSTED)
    mail.send_email("sam@example.com", "hi", "body")
    for word in ("wait", "send"):
        was_cmd, _ = _incoming(word, sender=SIB)
        assert not was_cmd and len(mail.pending_drafts()) == 1
