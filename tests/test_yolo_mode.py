"""YOLO mode regression tests.

YOLO is the user opt-out of auto-pause for a bounded window.
Pinning the contract:
  - Time-bounded — auto-expires
  - Hard cap (7 days) — prevents "forgot YOLO was on for months"
  - File-based — survives restart
  - Auto-cleanup of expired flag on read
  - maybe_auto_pause skips threshold check during YOLO
  - The moment YOLO expires, next heartbeat re-arms auto-pause
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from windyfly.agent.spend_monitor import (
    is_yolo_active,
    maybe_auto_pause,
    yolo_disable,
    yolo_enable,
    yolo_status,
)


@pytest.fixture(autouse=True)
def isolated_flags(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.setenv("WINDY_YOLO_FLAG",  str(tmp_path / ".yolo"))
    yield tmp_path


@pytest.fixture
def db_burning_fast(tmp_path):
    from windyfly.memory.database import Database
    db = Database(str(tmp_path / "burn.db"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    for i in range(50):
        db.execute(
            "INSERT INTO cost_ledger "
            "(id, model, input_tokens, output_tokens, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"row{i}", "claude-sonnet-4-6", 1000, 500, 0.10, now),
        )
    db.commit()
    yield db
    db.close()


# ── Lifecycle ──────────────────────────────────────────────────────


def test_initial_state_inactive():
    assert is_yolo_active() is False
    s = yolo_status()
    assert s["active"] is False
    assert s["hours_remaining"] == 0


def test_enable_for_24_hours():
    out = yolo_enable(hours=24, actor="grant")
    assert out["ok"] is True
    assert out["active"] is True
    assert out["hours"] == 24
    assert out["actor"] == "grant"
    assert is_yolo_active() is True
    s = yolo_status()
    assert s["active"] is True
    assert 23.5 < s["hours_remaining"] <= 24.0


def test_disable_clears():
    yolo_enable(hours=24)
    assert is_yolo_active() is True
    out = yolo_disable()
    assert out["ok"] is True
    assert out["was_active"] is True
    assert is_yolo_active() is False


def test_disable_when_inactive_idempotent():
    out = yolo_disable()
    assert out["ok"] is True
    assert out["was_active"] is False


# ── Bounds & validation ────────────────────────────────────────────


def test_negative_hours_rejected():
    out = yolo_enable(hours=-5)
    assert out["ok"] is False
    assert "positive" in out["error"]


def test_zero_hours_rejected():
    out = yolo_enable(hours=0)
    assert out["ok"] is False


def test_hard_cap_at_7_days():
    """Hard cap prevents 'forgot YOLO was on for 3 months' scenario."""
    out = yolo_enable(hours=8 * 24)  # 8 days, over the 7-day cap
    assert out["ok"] is False
    assert "capped" in out["error"].lower()


def test_at_cap_accepted():
    out = yolo_enable(hours=7 * 24)  # exactly 7 days
    assert out["ok"] is True


# ── Expiry ─────────────────────────────────────────────────────────


def test_expired_flag_treated_as_inactive(isolated_flags):
    """Manually plant an expired flag and verify yolo_status reports
    inactive AND cleans up the file."""
    flag = isolated_flags / ".yolo"
    flag.write_text(json.dumps({
        "expires_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
        "enabled_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        "hours": 24,
        "actor": "test",
    }))
    s = yolo_status()
    assert s["active"] is False
    assert s["hours_remaining"] == 0
    # File should be cleaned up
    assert not flag.exists()


def test_torn_flag_treated_as_inactive(isolated_flags):
    """Garbled flag file (mid-write or hand-edited) → treat as inactive
    but DON'T delete (might be racing with a write)."""
    flag = isolated_flags / ".yolo"
    flag.write_text("not valid json {{{")
    assert is_yolo_active() is False


# ── Integration with auto-pause ───────────────────────────────────


def test_auto_pause_skips_during_yolo(db_burning_fast):
    """The whole point: when YOLO is active, even a burn-rate spike
    must NOT trigger pause."""
    yolo_enable(hours=24)
    result = maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert result["action"] == "noop:yolo"
    assert result["yolo_hours_remaining"] > 23.0


def test_auto_pause_resumes_after_yolo_expires(db_burning_fast, isolated_flags):
    """After YOLO expires, next heartbeat tick triggers normal
    threshold check and pauses on burn spike."""
    # Plant an expired YOLO flag
    flag = isolated_flags / ".yolo"
    flag.write_text(json.dumps({
        "expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        "enabled_at": (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat(),
        "hours": 24,
        "actor": "test",
    }))
    # is_yolo_active should report inactive (cleans up flag)
    assert is_yolo_active() is False
    # And maybe_auto_pause should now trip
    result = maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert result["action"] == "paused"


def test_pause_takes_priority_over_yolo(db_burning_fast):
    """If user explicitly /pause'd while YOLO is active, the pause
    flag wins. Pause is the higher-level intent — user said STOP."""
    from windyfly.agent.spend_monitor import pause
    yolo_enable(hours=24)
    pause(reason="manual stop")
    result = maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert result["action"] == "noop:already_paused"


# ── Persistence ───────────────────────────────────────────────────


def test_yolo_survives_simulated_restart(isolated_flags):
    """The whole point of file-based: a restart of the bot doesn't
    silently un-YOLO. Operator opted into the spend; that intent
    persists through the restart."""
    yolo_enable(hours=12)
    # Simulate a fresh process by reading the flag from scratch
    flag = isolated_flags / ".yolo"
    assert flag.exists()
    data = json.loads(flag.read_text())
    assert data["hours"] == 12
    # New "process" reads it
    assert is_yolo_active() is True
