"""14_email + 15_phone categories now allowed on Telegram (with
``--confirm`` gating on mutating sends).

Pre-PR: Grant's bot could not send email / SMS from Telegram
because both categories were excluded from the
``_REMOTE_ALLOWED_CATEGORIES`` allow-list in
``commands/registry.py``. The original product mandate explicitly
calls out "send emails, send text messages" as core out-of-box
capabilities, so the bot literally could not perform its
advertised job from the chat surface.

Fix:
  - Add ``14_email`` and ``15_phone`` to the remote allow-list
  - Mark mutating commands (``send-mail``, ``reply-mail``, ``sms``)
    with ``dangerous=True`` so they require ``--confirm`` before
    firing. Read-only views (``inbox``, ``read-mail``, etc.) stay
    one-tap because they don't mutate state.
"""

from __future__ import annotations

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.core import wire_runtime
from windyfly.commands.registry import (
    _REMOTE_ALLOWED_CATEGORIES,
    registry,
)
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database


@pytest.fixture
def db():
    d = Database(":memory:")
    init_all_commands(db=d, config={})
    wire_runtime(db=d)
    yield d
    d.close()


# ── Allow-list shape ─────────────────────────────────────────────


def test_email_and_phone_categories_in_remote_allowlist():
    assert "14_email" in _REMOTE_ALLOWED_CATEGORIES
    assert "15_phone" in _REMOTE_ALLOWED_CATEGORIES


# ── Mutating commands are dangerous=True ─────────────────────────


def test_send_mail_is_dangerous(db):
    cmds = {c.name: c for c in registry.all()}
    assert cmds["send-mail"].dangerous is True


def test_reply_mail_is_dangerous(db):
    cmds = {c.name: c for c in registry.all()}
    assert cmds["reply-mail"].dangerous is True


def test_sms_is_dangerous(db):
    cmds = {c.name: c for c in registry.all()}
    assert cmds["sms"].dangerous is True


def test_read_only_mail_commands_NOT_dangerous(db):
    """Read-only views shouldn't require ``--confirm`` — they
    don't mutate. One-tap UX preserved."""
    cmds = {c.name: c for c in registry.all()}
    for name in ("inbox", "read-mail", "mail-stats"):
        assert cmds[name].dangerous is False, name


def test_read_only_sms_commands_NOT_dangerous(db):
    cmds = {c.name: c for c in registry.all()}
    for name in ("sms-history", "voicemail"):
        assert cmds[name].dangerous is False, name


# ── Telegram dispatcher behavior ─────────────────────────────────


@pytest.mark.asyncio
async def test_telegram_inbox_not_denied(db):
    """/inbox should now respond on Telegram (not the
    'not allowed from telegram' denial). The reply may say
    'not configured' if Windy Mail isn't provisioned — that's
    a different, correct message."""
    ok, out = await handle_incoming(
        "/inbox", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "not allowed from" not in out.lower()


@pytest.mark.asyncio
async def test_telegram_sms_history_not_denied(db):
    ok, out = await handle_incoming(
        "/sms-history", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "not allowed from" not in out.lower()


@pytest.mark.asyncio
async def test_telegram_send_mail_requires_confirm(db):
    """/send-mail on Telegram must surface the dangerous-command
    confirmation prompt — NOT fire silently, NOT be denied."""
    ok, out = await handle_incoming(
        "/send-mail mom@example.com hi",
        {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    # Must NOT be denied
    assert "not allowed from" not in out.lower()
    # Must require confirmation
    assert "confirm" in out.lower() or "--confirm" in out


@pytest.mark.asyncio
async def test_telegram_sms_requires_confirm(db):
    ok, out = await handle_incoming(
        "/sms 5551234567 test",
        {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "not allowed from" not in out.lower()
    assert "confirm" in out.lower() or "--confirm" in out


@pytest.mark.asyncio
async def test_telegram_reply_mail_requires_confirm(db):
    ok, out = await handle_incoming(
        "/reply-mail abc123",
        {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "not allowed from" not in out.lower()
    assert "confirm" in out.lower() or "--confirm" in out
