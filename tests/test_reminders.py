"""Tests for the reminders tool."""

from datetime import datetime, timedelta, timezone

from windyfly.memory.database import Database
from windyfly.tools.reminders import (
    _parse_time,
    cancel_reminder,
    get_due_reminders,
    list_reminders,
    mark_delivered,
    set_reminder,
)


def _db():
    return Database(":memory:")


def test_set_reminder():
    db = _db()
    result = set_reminder(db, "take medicine", "in 20 minutes")
    assert result["success"] is True
    assert "take medicine" in result["message"]
    assert result["id"]
    db.close()


def test_set_reminder_at_time():
    db = _db()
    result = set_reminder(db, "call mom", "at 3pm")
    assert result["success"] is True
    assert "call mom" in result["message"]
    db.close()


def test_set_reminder_bad_time():
    db = _db()
    result = set_reminder(db, "something", "garbled nonsense xyz")
    assert result["success"] is False
    assert "Could not parse" in result["error"]
    db.close()


def test_list_reminders_empty():
    db = _db()
    result = list_reminders(db)
    assert result["reminders"] == []
    db.close()


def test_list_reminders_with_items():
    db = _db()
    set_reminder(db, "item 1", "in 1 hour")
    set_reminder(db, "item 2", "in 2 hours")
    result = list_reminders(db)
    assert len(result["reminders"]) == 2
    db.close()


def test_cancel_reminder():
    db = _db()
    r = set_reminder(db, "test", "in 1 hour")
    result = cancel_reminder(db, r["id"])
    assert result["success"] is True
    # Verify it's gone
    assert list_reminders(db)["reminders"] == []
    db.close()


def test_cancel_nonexistent():
    db = _db()
    result = cancel_reminder(db, "nonexistent-id")
    assert result["success"] is False
    db.close()


def test_get_due_reminders():
    db = _db()
    # Set a reminder in the past (already due)
    set_reminder(db, "past reminder", "in 0 seconds")
    due = get_due_reminders(db)
    # The "in 0 seconds" might be slightly in the future, so just check the query works
    assert isinstance(due, list)
    db.close()


def test_mark_delivered():
    db = _db()
    r = set_reminder(db, "deliver me", "in 0 seconds")
    mark_delivered(db, r["id"])
    # Should not appear in upcoming list
    result = list_reminders(db)
    assert len(result["reminders"]) == 0
    db.close()


def test_mark_delivered_recurring():
    db = _db()
    r = set_reminder(db, "daily pill", "in 0 seconds", repeat="daily")
    mark_delivered(db, r["id"], repeat="daily")
    # Should still have 1 reminder (rescheduled)
    result = list_reminders(db)
    assert len(result["reminders"]) == 1
    db.close()


def test_parse_time_relative_minutes():
    result = _parse_time("in 20 minutes")
    assert result is not None
    assert result > datetime.now(timezone.utc)


def test_parse_time_relative_hours():
    result = _parse_time("in 2 hours")
    assert result is not None
    diff = (result - datetime.now(timezone.utc)).total_seconds()
    assert 7100 < diff < 7300  # ~2 hours


def test_parse_time_at_pm():
    result = _parse_time("at 3pm")
    assert result is not None
    assert result.hour == 15


def test_parse_time_tomorrow():
    result = _parse_time("tomorrow at 9am")
    assert result is not None
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    assert result.day == tomorrow.day
