"""/goal Phase 2 — timer-driven pacing tests.

Covers:
  - Migration 9 applied (schema_version >= 9; goals has pace columns)
  - parse_goal_command for /goal pace variants
  - set_goal_pace validates MIN/MAX bounds
  - mark_paced advances last_paced_at
  - goals_due_for_pacing returns goals where age >= pace_seconds
  - in_quiet_hours wraps midnight correctly
  - user_recently_active threshold
  - bump_ignored_fires + auto-pause threshold
  - scheduler tick fires due goals + respects guards (quiet hours,
    recent activity, no chat_id)
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from windyfly.agent import goal_pacing
from windyfly.channels.slash_commands import _parse_duration, parse_goal_command
from windyfly.memory import goals as goals_mod
from windyfly.memory.database import Database


# ── Migration + columns ──────────────────────────────────────────


def test_migration_9_applied():
    db = Database(":memory:")
    ver = db.fetchone("SELECT MAX(version) AS v FROM schema_version")
    assert ver and ver["v"] >= 9
    cols = {r["name"] for r in db.fetchall("PRAGMA table_info(goals)")}
    assert {"pace_seconds", "last_paced_at", "chat_id",
            "ignored_pace_fires"}.issubset(cols)


# ── Parser ────────────────────────────────────────────────────────


class TestPaceParser:

    def test_durations(self):
        assert _parse_duration("4h") == 4 * 3600
        assert _parse_duration("30m") == 30 * 60
        assert _parse_duration("60sec") == 60
        assert _parse_duration("2hours") == 2 * 3600
        assert _parse_duration("daily") == 86400
        assert _parse_duration("hourly") == 3600

    def test_bad_durations(self):
        assert _parse_duration("garbage") is None
        assert _parse_duration("4y") is None
        assert _parse_duration("") is None

    def test_slash_routing(self):
        assert parse_goal_command("/goal pace 4h") == (True, "pace_set", "14400")
        assert parse_goal_command("/goal pace off") == (True, "pace_set", "0")
        assert parse_goal_command("/goal pace") == (True, "pace_status", None)
        assert parse_goal_command("/goal pace status") == (True, "pace_status", None)
        assert parse_goal_command("/goal pace garbage")[1] == "pace_invalid"


# ── CRUD ─────────────────────────────────────────────────────────


@pytest.fixture
def db_with_goal():
    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    return db, gid


def test_set_goal_pace_stores(db_with_goal):
    db, gid = db_with_goal
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="chat-123")
    g = goals_mod.get_goal(db, gid)
    assert g["pace_seconds"] == 3600
    assert g["chat_id"] == "chat-123"


def test_set_goal_pace_validates_min(db_with_goal):
    db, gid = db_with_goal
    with pytest.raises(ValueError):
        goals_mod.set_goal_pace(db, gid, pace_seconds=60)


def test_set_goal_pace_validates_max(db_with_goal):
    db, gid = db_with_goal
    with pytest.raises(ValueError):
        goals_mod.set_goal_pace(db, gid, pace_seconds=48 * 3600)


def test_set_goal_pace_zero_disables(db_with_goal):
    db, gid = db_with_goal
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="c")
    goals_mod.set_goal_pace(db, gid, pace_seconds=0)
    g = goals_mod.get_goal(db, gid)
    assert g["pace_seconds"] == 0


def test_mark_paced_updates_timestamp(db_with_goal):
    db, gid = db_with_goal
    goals_mod.mark_paced(db, gid, fired=True)
    g = goals_mod.get_goal(db, gid)
    assert g["last_paced_at"] is not None


def test_bump_ignored_and_reset(db_with_goal):
    db, gid = db_with_goal
    assert goals_mod.bump_ignored_fires(db, gid) == 1
    assert goals_mod.bump_ignored_fires(db, gid) == 2
    goals_mod.reset_ignored_fires(db, gid)
    g = goals_mod.get_goal(db, gid)
    assert g["ignored_pace_fires"] == 0


def test_goals_due_for_pacing(db_with_goal):
    db, gid = db_with_goal
    # Not yet paced — never_paced + age 0 vs pace_seconds=3600 → not due
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="c")
    due = goals_mod.goals_due_for_pacing(db)
    assert all(g["id"] != gid for g in due)
    # Bump created_at into the past so age > pace_seconds
    db.execute(
        "UPDATE goals SET created_at = datetime('now', '-2 hours') WHERE id = ?",
        (gid,),
    )
    db.commit()
    due = goals_mod.goals_due_for_pacing(db)
    assert any(g["id"] == gid for g in due)


def test_inactive_goal_never_due(db_with_goal):
    """Completed/abandoned goals must not be picked up by the
    scheduler even if pace_seconds > 0."""
    db, gid = db_with_goal
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="c")
    db.execute(
        "UPDATE goals SET created_at = datetime('now', '-2 hours'), "
        "status = 'completed' WHERE id = ?",
        (gid,),
    )
    db.commit()
    due = goals_mod.goals_due_for_pacing(db)
    assert all(g["id"] != gid for g in due)


# ── Quiet hours ──────────────────────────────────────────────────


class TestQuietHours:

    def test_default_window_includes_3am(self, monkeypatch):
        # Default config: 23 → 7 (wraparound midnight)
        monkeypatch.setattr(goal_pacing, "QUIET_HOURS_START", 23)
        monkeypatch.setattr(goal_pacing, "QUIET_HOURS_END", 7)
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 3, 0)) is True
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 23, 30)) is True
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 12, 0)) is False
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 7, 30)) is False

    def test_non_wraparound_window(self, monkeypatch):
        # 22 → 23 = a one-hour window in the evening, no wraparound
        monkeypatch.setattr(goal_pacing, "QUIET_HOURS_START", 22)
        monkeypatch.setattr(goal_pacing, "QUIET_HOURS_END", 23)
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 22, 30)) is True
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 12, 0)) is False
        assert goal_pacing.in_quiet_hours(datetime(2026, 1, 1, 23, 30)) is False


# ── user_recently_active ─────────────────────────────────────────


def test_user_recently_active(db_with_goal):
    from windyfly.memory.episodes import save_episode
    db, gid = db_with_goal
    save_episode(db, "user", "hi", session_id="s1")
    assert goal_pacing.user_recently_active(db, "s1", threshold_seconds=60)
    assert goal_pacing.user_recently_active(db, "s-other", threshold_seconds=60) is False


# ── Scheduler tick ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_tick_fires_due_goal(monkeypatch):
    """A due goal with no recent user activity and a chat_id → fire."""
    monkeypatch.setattr(goal_pacing, "in_quiet_hours", lambda *a, **k: False)

    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="chat-x")
    db.execute(
        "UPDATE goals SET created_at = datetime('now', '-2 hours') WHERE id = ?",
        (gid,),
    )
    db.commit()

    deliver_mock = AsyncMock()

    def fake_agent_respond(**kwargs):
        return "I checked progress: still on it."

    n = await goal_pacing._scheduler_tick(
        db=db, deliver=deliver_mock,
        agent_respond=fake_agent_respond, config={},
        write_queue=MagicMock(),
    )
    assert n == 1
    deliver_mock.assert_called_once()
    args, _ = deliver_mock.call_args
    assert args[0] == "chat-x"
    assert "still on it" in args[1]
    # ignored counter should have been bumped speculatively
    g = goals_mod.get_goal(db, gid)
    assert g["ignored_pace_fires"] == 1


@pytest.mark.asyncio
async def test_scheduler_tick_skips_quiet_hours(monkeypatch):
    """During quiet hours, no fire even if a goal is due."""
    # Force quiet hours to always-on
    monkeypatch.setattr(goal_pacing, "in_quiet_hours", lambda *a, **k: True)
    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="chat-x")
    db.execute(
        "UPDATE goals SET created_at = datetime('now', '-2 hours') WHERE id = ?",
        (gid,),
    )
    db.commit()
    deliver_mock = AsyncMock()
    n = await goal_pacing._scheduler_tick(
        db=db, deliver=deliver_mock, agent_respond=lambda **k: "x",
        config={}, write_queue=MagicMock(),
    )
    assert n == 0
    deliver_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_tick_skips_recent_user_activity(monkeypatch):
    """Recent user activity in the session → skip."""
    monkeypatch.setattr(goal_pacing, "QUIET_HOURS_START", 0)
    monkeypatch.setattr(goal_pacing, "QUIET_HOURS_END", 0)

    from windyfly.memory.episodes import save_episode
    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="chat-x")
    db.execute(
        "UPDATE goals SET created_at = datetime('now', '-2 hours') WHERE id = ?",
        (gid,),
    )
    save_episode(db, "user", "hi just now", session_id="s1")
    db.commit()

    deliver_mock = AsyncMock()
    n = await goal_pacing._scheduler_tick(
        db=db, deliver=deliver_mock, agent_respond=lambda **k: "x",
        config={}, write_queue=MagicMock(),
    )
    assert n == 0
    deliver_mock.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_tick_auto_pauses_after_threshold(monkeypatch):
    """After AUTO_PAUSE_AFTER_IGNORED consecutive ignored fires, the
    scheduler should set pace_seconds=0 to stop pinging."""
    monkeypatch.setattr(goal_pacing, "in_quiet_hours", lambda *a, **k: False)

    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="s1", text="g")
    goals_mod.set_goal_pace(db, gid, pace_seconds=3600, chat_id="chat-x")
    # Pre-load 2 ignored fires; one more should trip auto-pause.
    db.execute(
        "UPDATE goals SET created_at = datetime('now', '-2 hours'), "
        "ignored_pace_fires = 2 WHERE id = ?", (gid,),
    )
    db.commit()
    deliver_mock = AsyncMock()
    await goal_pacing._scheduler_tick(
        db=db, deliver=deliver_mock, agent_respond=lambda **k: "x",
        config={}, write_queue=MagicMock(),
    )
    g = goals_mod.get_goal(db, gid)
    assert g["pace_seconds"] == 0  # auto-paused
    # ignored_pace_fires is RESET to 0 by set_goal_pace(0) as part
    # of the auto-pause flow — that's intentional so when the user
    # re-enables pacing they don't immediately re-trip auto-pause.
    assert g["ignored_pace_fires"] == 0
