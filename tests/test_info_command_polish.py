"""Regression: info commands must report reality, not stale defaults.

Surfaced 2026-07-06 by a live Windy Chat command sweep against Windy 0:
- /uptime claimed "Agent is not running" while it was answering over chat
  (it only checked a PID file that systemd deployments never write).
- /context reported a hardcoded 8000-token window for an Opus agent whose
  real window is 200K/1M.
- /errors and /logs looked only at data/windyfly.log, so they found nothing
  on a systemd deployment whose logs live elsewhere.
"""

from __future__ import annotations

import asyncio

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database


@pytest.fixture
def booted():
    from windyfly.commands.core import wire_runtime
    db = Database(":memory:")
    init_all_commands(db=db, config={"agent": {"default_model": "claude-opus-4-8"}})
    wire_runtime(db=db)
    yield db
    db.close()


def _run(text):
    return asyncio.run(handle_incoming(text, {"platform": "matrix", "channel_id": "x"}))


def test_uptime_reports_process_uptime_not_missing_pid(booted):
    ok, out = _run("/uptime")
    assert "not running" not in out.lower()
    assert "Uptime:" in out


def test_context_reports_real_model_window(booted, monkeypatch):
    monkeypatch.delenv("MAX_CONTEXT_TOKENS", raising=False)
    monkeypatch.setenv("DEFAULT_MODEL", "claude-opus-4-8")
    ok, out = _run("/context")
    # Opus is 200K native — must not report the old 8000 default.
    assert "8000" not in out and "8,000" not in out
    assert "200" in out or "1M" in out or "1,000,000" in out


def test_context_honors_explicit_override(booted, monkeypatch):
    monkeypatch.setenv("MAX_CONTEXT_TOKENS", "32000")
    ok, out = _run("/context")
    assert "32,000" in out or "32000" in out


def test_logs_honor_configurable_path(booted, tmp_path, monkeypatch):
    log = tmp_path / "windy-0.log"
    log.write_text("hello\nERROR boom\nworld\n")
    monkeypatch.setenv("WINDYFLY_LOG_FILE", str(log))
    ok, out = _run("/logs")
    assert "world" in out
    ok, out = _run("/errors")
    assert "boom" in out
