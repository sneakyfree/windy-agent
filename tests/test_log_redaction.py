"""Tests for the logging-time secret redaction filter.

Each common secret format gets a dedicated case so we can prove the
filter doesn't accidentally pass tokens through. The end-to-end test
uses an in-memory handler and asserts on what would actually be
written to the log file under launchd.
"""

from __future__ import annotations

import io
import logging

import pytest

from windyfly.observability.redact import (
    RedactingFilter,
    install_root_redaction,
    redact,
)


def test_redacts_telegram_bot_token():
    text = (
        "POST https://api.telegram.org/bot1234567890:"
        "AAFAKE-token-for-tests-only_0000000/getUpdates"
    )
    out = redact(text)
    assert "AAFAKE-token-for-tests-only_0000000" not in out
    assert "***REDACTED***" in out
    # Bot ID + first 4 chars survive for instance-distinguishing
    assert "bot1234567890:AAFA" in out


def test_redacts_openai_api_key():
    text = "openai key sk-proj-1alR1kliXxM7ltCFsCUjE9cvW85TDy7eYpByUq"
    out = redact(text)
    assert "1alR1kliXxM7ltCFsCUjE9cvW85TDy7eYpByUq" not in out
    assert "***REDACTED***" in out


def test_redacts_anthropic_api_key():
    text = "anthropic key sk-ant-api03-AbCdEfGhIjKlMnOpQrSt"
    out = redact(text)
    assert "AbCdEfGhIjKlMnOpQrSt" not in out


def test_redacts_wk_broker_key():
    text = "ANTHROPIC_API_KEY=wk_broker_abcdefghijklmnopqrst"
    out = redact(text)
    assert "abcdefghijklmnopqrst" not in out
    assert "***REDACTED***" in out


def test_redacts_zai_key():
    text = "ZAI_API_KEY=c9842e4898804f4999e39f780f006cae.3KmkZghdXNEO9xo0"
    out = redact(text)
    assert "3KmkZghdXNEO9xo0" not in out
    assert "***REDACTED***" in out


def test_redacts_bearer_header():
    text = "headers={'Authorization': 'Bearer abc123def456ghi789jkl012'}"
    out = redact(text)
    assert "abc123def456ghi789jkl012" not in out


def test_redacts_authorization_header_assignment():
    text = "Authorization: sk-proj-NotARealKeyButLongEnough"
    out = redact(text)
    assert "NotARealKeyButLongEnough" not in out


def test_passthrough_for_innocuous_text():
    text = "user said hello and the agent replied"
    assert redact(text) == text


def test_filter_modifies_record_msg_in_place():
    f = RedactingFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="hit /bot1234567890:AAFAKE-token-for-tests-only_0000000/x",
        args=None,
        exc_info=None,
    )
    f.filter(record)
    assert "AAFAKE-token-for-tests-only_0000000" not in record.msg
    assert "***REDACTED***" in record.msg


def test_filter_handles_args_formatting():
    f = RedactingFilter()
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="key=%s",
        args=("sk-proj-abcdefghijklmnopqrstuvwxyz",),
        exc_info=None,
    )
    f.filter(record)
    assert "abcdefghijklmnopqrstuvwxyz" not in record.msg
    assert record.args is None


def test_install_root_redaction_is_idempotent():
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    install_root_redaction()
    install_root_redaction()
    install_root_redaction()
    counts = [
        sum(1 for f in h.filters if isinstance(f, RedactingFilter))
        for h in root.handlers
    ]
    assert all(c == 1 for c in counts), counts


def test_end_to_end_via_root_handler():
    """The whole point: a log call with a secret in args shouldn't
    show the secret in the captured stream."""
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.addFilter(RedactingFilter())

    logger = logging.getLogger("test_e2e_redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "calling https://api.telegram.org/bot1234567890:"
        "AAFAKE-token-for-tests-only_0000000/getMe",
    )
    output = buf.getvalue()
    assert "AAFAKE-token-for-tests-only_0000000" not in output
    assert "***REDACTED***" in output


def test_redacts_bare_telegram_token_in_ptb_invalid_token_message():
    # python-telegram-bot quotes the token WITHOUT the "bot" URL prefix
    # when getMe 401s. This exact line leaked the full token into
    # windy-0-telegram.log on every reconnect for six days.
    text = (
        "Telegram start failed: The token "
        "`1234567890:AAFAKE-token-for-tests-only_0000000` "
        "was rejected by the server.. Reconnecting in 8s..."
    )
    out = redact(text)
    assert "KE-token-for-tests-only_0000000" not in out
    assert "1234567890:AAFA***REDACTED***" in out


def test_bare_token_pattern_leaves_clock_times_and_ids_alone():
    for text in ("01:56:23 heartbeat ok", "room 12345678:abc", "ratio 1234567:12"):
        assert redact(text) == text


def test_telegram_reconnect_event_is_redacted(monkeypatch):
    # The events ledger is written directly, not through the logging
    # filter, so the reconnect path must redact on its own.
    import types

    import windyfly.observability.events as events
    from windyfly.channels.telegram_bot import TelegramChannel

    captured = {}
    monkeypatch.setattr(
        events, "log_event",
        lambda db, wq, etype, props: captured.update(etype=etype, **props),
    )
    fake_self = types.SimpleNamespace(_db=object(), _write_queue=object())
    TelegramChannel._log_reconnect_event(
        fake_self,
        "The token `1234567890:AAFAKE-token-for-tests-only_0000000` "
        "was rejected by the server.",
        8,
    )
    assert captured["etype"] == "telegram.reconnect"
    assert "KE-token-for-tests-only_0000000" not in captured["error"]
    assert "***REDACTED***" in captured["error"]
