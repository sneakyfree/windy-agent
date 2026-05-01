"""Auto-pause-on-burn-threshold regressions.

Pin the contract for the heartbeat-driven safety net:
  - When already paused → no-op (don't double-pause)
  - When below threshold → no-op (don't spam DMs)
  - When breaching → pause + return structured action payload
  - Threshold of 0 → disabled
  - DB read failure → graceful no-op (heartbeat keeps ticking)
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from windyfly.agent.spend_monitor import (
    is_paused,
    maybe_auto_pause,
    pause,
    resume,
)


@pytest.fixture(autouse=True)
def isolated_pause_flag(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    yield


@pytest.fixture
def db_burning_fast(tmp_path):
    """A DB with enough recent cost to trip even a generous threshold."""
    from windyfly.memory.database import Database
    db = Database(str(tmp_path / "burn.db"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # 50 calls in the last 15 minutes at $0.10 each = $5 in 15 min
    # → extrapolated $20/hr
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


@pytest.fixture
def db_burning_slow(tmp_path):
    """A DB with negligible cost — under any threshold."""
    from windyfly.memory.database import Database
    db = Database(str(tmp_path / "slow.db"))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    # 5 calls × $0.001 = $0.005 in 15 min → ~$0.02/hr
    for i in range(5):
        db.execute(
            "INSERT INTO cost_ledger "
            "(id, model, input_tokens, output_tokens, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"slow{i}", "claude-haiku-4-5", 100, 50, 0.001, now),
        )
    db.commit()
    yield db
    db.close()


def test_already_paused_returns_noop(db_burning_fast):
    """If we're already paused, don't re-pause (don't spam DMs)."""
    pause(reason="user", actor="manual")
    result = maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert result["action"] == "noop:already_paused"
    # Still paused (didn't get unpaused either)
    assert is_paused() is True


def test_below_threshold_returns_noop(db_burning_slow):
    """Slow burn → no action."""
    result = maybe_auto_pause(db_burning_slow, threshold_usd_per_hour=5.0)
    assert result["action"] == "noop:below_threshold"
    assert is_paused() is False


def test_breach_triggers_pause(db_burning_fast):
    """Burn rate exceeds threshold → pause + structured payload."""
    assert is_paused() is False
    result = maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert result["action"] == "paused"
    assert result["current_hourly"] > result["threshold"]
    assert is_paused() is True


def test_threshold_zero_disables(db_burning_fast, monkeypatch):
    """Setting threshold env var to 0 disables auto-pause."""
    monkeypatch.setenv("WINDY_BURN_AUTOPAUSE_USD_PER_HOUR", "0")
    result = maybe_auto_pause(db_burning_fast)
    assert result["action"] == "noop:below_threshold"
    assert result.get("threshold") in (None, 0, 0.0) or "disabled" in str(result)
    assert is_paused() is False


def test_breach_payload_includes_provider_breakdown(db_burning_fast):
    """The DM the bot sends should be able to surface WHICH provider
    is burning. Verify the breakdown data is there for the caller."""
    result = maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert "by_provider" in result
    assert "anthropic" in result["by_provider"]


def test_pause_reason_is_descriptive(db_burning_fast):
    """The auto-pause reason in the flag file should explain itself."""
    maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    from windyfly.agent.spend_monitor import pause_reason
    info = pause_reason()
    assert info["actor"] == "auto"
    assert "burn rate" in info["reason"].lower()
    assert "threshold" in info["reason"].lower()


def test_resume_after_auto_pause_clears_state(db_burning_fast):
    """After auto-pause + manual /resume, the bot is unpaused."""
    maybe_auto_pause(db_burning_fast, threshold_usd_per_hour=1.0)
    assert is_paused() is True
    resume()
    assert is_paused() is False


def test_db_read_failure_returns_safe_default(monkeypatch, tmp_path):
    """If the cost-ledger query fails, maybe_auto_pause must NOT
    crash — the heartbeat needs to keep ticking. The check_burn_threshold
    helper already swallows DB errors and returns a no-breach
    response; verify maybe_auto_pause inherits that resilience."""
    from windyfly.memory.database import Database
    db = Database(str(tmp_path / "broken.db"))
    db.close()  # close before query → triggers a programming error
    # Should not raise
    result = maybe_auto_pause(db, threshold_usd_per_hour=1.0)
    assert "action" in result
    assert is_paused() is False
