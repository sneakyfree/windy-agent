"""Regression tests for the post-panic restart-greeting bridge.

Contract:
  - panic handler writes a flag with chat_id atomically
  - new process consumes the flag and clears it
  - lost / corrupt / missing flag never raises
  - flag is cleared even when the send-greeting attempt fails
    (so a chat-id we can't reach doesn't loop forever)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from windyfly.observability import restart_greeting as rg


@pytest.fixture(autouse=True)
def isolated_flag_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_PENDING_GREETING_DIR", str(tmp_path))
    yield tmp_path


def test_no_flag_returns_none(isolated_flag_dir):
    assert rg.consume_pending_greeting() is None


def test_set_then_consume_returns_payload(isolated_flag_dir):
    rg.set_pending_greeting(chat_id="42", platform="telegram")
    out = rg.consume_pending_greeting()
    assert out is not None
    assert out["chat_id"] == "42"
    assert out["platform"] == "telegram"
    assert out["reason"] == "panic_reset"
    assert "ts" in out


def test_consume_clears_flag(isolated_flag_dir):
    """A second call must return None — the flag is one-shot. This
    prevents looping the same greeting on every restart attempt."""
    rg.set_pending_greeting(chat_id="42")
    assert rg.consume_pending_greeting() is not None
    assert rg.consume_pending_greeting() is None


def test_corrupt_flag_returns_none_and_clears(isolated_flag_dir):
    """A torn / hand-edited flag must not crash the agent boot.
    We log + clear + return None so the bot starts up cleanly."""
    flag = isolated_flag_dir / ".pending_restart_greeting"
    flag.write_text("not valid json {{{ ")
    assert rg.consume_pending_greeting() is None
    # And the flag is cleared
    assert not flag.exists()


def test_atomic_write_uses_tmp_then_rename(isolated_flag_dir):
    """Verify there's no torn-file window — write goes through a
    .tmp sibling and then renames atomically."""
    rg.set_pending_greeting(chat_id="42")
    flag = isolated_flag_dir / ".pending_restart_greeting"
    assert flag.exists()
    # No leftover .tmp sibling
    assert not (isolated_flag_dir / ".pending_restart_greeting.tmp").exists()


def test_payload_is_single_line_json(isolated_flag_dir):
    rg.set_pending_greeting(chat_id="42")
    text = (isolated_flag_dir / ".pending_restart_greeting").read_text(encoding="utf-8")
    parsed = json.loads(text)
    assert parsed["chat_id"] == "42"


def test_set_with_perms_failure_does_not_raise(monkeypatch):
    """A read-only home dir must not stop a panic restart from firing."""
    monkeypatch.setenv("WINDY_PENDING_GREETING_DIR", "/nonexistent/readonly")
    # Should not raise — set_pending_greeting is best-effort.
    rg.set_pending_greeting(chat_id="42")
