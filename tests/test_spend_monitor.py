"""Spend monitor + pause / kill-switch regressions.

Pinning the contract so future refactors can't accidentally
re-enable LLM calls while paused, or hide spending from /spend.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from windyfly.agent.spend_monitor import (
    check_burn_threshold,
    get_burn_rate,
    get_spend_summary,
    is_paused,
    pause,
    pause_reason,
    resume,
)


@pytest.fixture(autouse=True)
def isolated_pause_flag(monkeypatch, tmp_path):
    flag = tmp_path / ".paused"
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(flag))
    yield flag


# ── Pause primitives ──────────────────────────────────────────────


def test_initial_state_not_paused():
    assert is_paused() is False
    assert pause_reason() == {}


def test_pause_creates_flag_with_reason():
    out = pause(reason="testing", actor="unit-test")
    assert out["ok"] is True
    assert is_paused() is True
    info = pause_reason()
    assert info["reason"] == "testing"
    assert info["actor"] == "unit-test"
    assert "ts" in info


def test_resume_clears_flag():
    pause(reason="x")
    assert is_paused() is True
    out = resume()
    assert out["ok"] is True
    assert out["was_paused"] is True
    assert is_paused() is False


def test_resume_when_not_paused_is_noop():
    out = resume()
    assert out["ok"] is True
    assert out["was_paused"] is False


def test_pause_atomic_write_no_torn_file(isolated_pause_flag):
    pause(reason="atomic")
    # No .tmp sibling left around
    assert not (isolated_pause_flag.parent / ".paused.tmp").exists()
    # File parses as JSON
    data = json.loads(isolated_pause_flag.read_text())
    assert data["reason"] == "atomic"


def test_corrupt_flag_still_counts_as_paused(isolated_pause_flag):
    """A torn / hand-edited flag still means paused — better
    failsafe than silently un-pausing on parse error."""
    isolated_pause_flag.parent.mkdir(parents=True, exist_ok=True)
    isolated_pause_flag.write_text("not valid json {{{")
    assert is_paused() is True
    info = pause_reason()
    # Either a "raw" key holds the bad content, or another shape —
    # the important contract is is_paused() returns True.
    assert info  # non-empty


# ── Burn rate ─────────────────────────────────────────────────────


@pytest.fixture
def db_with_costs(tmp_path):
    """Real sqlite database with the cost_ledger schema + a few
    synthetic rows so we can grade the burn-rate aggregator."""
    from windyfly.memory.database import Database
    db = Database(str(tmp_path / "burn.db"))
    # Recent rows — within 1 hour window
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    rows = [
        ("anth1", "claude-haiku-4-5", 100, 50, 0.001),
        ("anth2", "claude-sonnet-4-6", 500, 250, 0.005),
        ("oai1",  "gpt-4o-mini",         200, 100, 0.0008),
        ("oai2",  "gpt-4o",                300, 150, 0.003),
        ("xai1",  "grok-3-mini",         100, 50, 0.0005),
    ]
    for entry_id, model, ti, to, cost in rows:
        db.execute(
            "INSERT INTO cost_ledger "
            "(id, model, input_tokens, output_tokens, cost_usd, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (entry_id, model, ti, to, cost, now),
        )
    db.commit()
    yield db
    db.close()


def test_burn_rate_aggregates_by_provider(db_with_costs):
    rate = get_burn_rate(db_with_costs, window_minutes=60)
    assert rate["total_calls"] == 5
    assert rate["total_input_tokens"] == 1200
    assert rate["total_output_tokens"] == 600
    by = rate["by_provider"]
    assert "anthropic" in by
    assert "openai" in by
    assert "xai" in by
    assert by["anthropic"]["calls"] == 2
    assert by["openai"]["calls"] == 2
    assert by["xai"]["calls"] == 1


def test_burn_rate_estimated_hourly_burn(db_with_costs):
    """5-minute window of 5 calls = a higher hourly extrapolation
    than the same calls over 60 minutes."""
    rate_5min = get_burn_rate(db_with_costs, window_minutes=5)
    rate_60min = get_burn_rate(db_with_costs, window_minutes=60)
    # 5-min sees the same rows but extrapolates to 12x for hourly
    assert rate_5min["estimated_hourly_burn_usd"] >= rate_60min["estimated_hourly_burn_usd"]


def test_burn_rate_window_excludes_old_rows(tmp_path):
    """Costs older than the window must not count in burn rate."""
    from windyfly.memory.database import Database
    db = Database(str(tmp_path / "old.db"))
    # Insert a row from 2 hours ago
    db.execute(
        "INSERT INTO cost_ledger "
        "(id, model, input_tokens, output_tokens, cost_usd, created_at) "
        "VALUES (?, ?, ?, ?, ?, datetime('now', '-2 hours'))",
        ("old1", "claude-haiku-4-5", 100, 50, 1.0),
    )
    db.commit()
    rate = get_burn_rate(db, window_minutes=60)
    assert rate["total_cost_usd"] == 0.0
    db.close()


# ── Spend summary ─────────────────────────────────────────────────


def test_spend_summary_includes_pause_status(db_with_costs):
    pause(reason="test")
    summary = get_spend_summary(db_with_costs)
    assert summary["paused"] is True
    assert summary["pause_info"]["reason"] == "test"
    assert "last_5_min" in summary
    assert "last_hour" in summary
    assert "last_day" in summary


def test_spend_summary_when_not_paused(db_with_costs):
    summary = get_spend_summary(db_with_costs)
    assert summary["paused"] is False
    assert summary["pause_info"] is None


# ── Auto-pause threshold ──────────────────────────────────────────


def test_threshold_disabled_when_zero(db_with_costs, monkeypatch):
    monkeypatch.setenv("WINDY_BURN_AUTOPAUSE_USD_PER_HOUR", "0")
    out = check_burn_threshold(db_with_costs)
    assert out["breach"] is False
    assert out["reason"] == "disabled"


def test_threshold_breach_detected(db_with_costs):
    """Force a low threshold to confirm breach detection works."""
    out = check_burn_threshold(db_with_costs, threshold_usd_per_hour=0.0001)
    assert out["breach"] is True
    assert out["current_hourly"] > out["threshold"]
    assert "by_provider" in out


def test_threshold_no_breach_under_limit(db_with_costs):
    """Generous threshold — no breach."""
    out = check_burn_threshold(db_with_costs, threshold_usd_per_hour=1000.0)
    assert out["breach"] is False
