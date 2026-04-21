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
        "POST https://api.telegram.org/bot8669155077:"
        "AAE-5ee2VMzkkXmxI8Rnjg6gDZG0AGwjBzI/getUpdates"
    )
    out = redact(text)
    assert "AAE-5ee2VMzkkXmxI8Rnjg6gDZG0AGwjBzI" not in out
    assert "***REDACTED***" in out
    # Bot ID + first 4 chars survive for instance-distinguishing
    assert "bot8669155077:AAE-" in out


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
        msg="hit /bot8669155077:AAE-5ee2VMzkkXmxI8Rnjg6gDZG0AGwjBzI/x",
        args=None,
        exc_info=None,
    )
    f.filter(record)
    assert "AAE-5ee2VMzkkXmxI8Rnjg6gDZG0AGwjBzI" not in record.msg
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
        "calling https://api.telegram.org/bot8669155077:"
        "AAE-5ee2VMzkkXmxI8Rnjg6gDZG0AGwjBzI/getMe",
    )
    output = buf.getvalue()
    assert "AAE-5ee2VMzkkXmxI8Rnjg6gDZG0AGwjBzI" not in output
    assert "***REDACTED***" in output
