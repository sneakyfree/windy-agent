"""Strand v2, Part 4 genes 5.1-5.4: dashboard, personality versioning, events (docs/strand/STRAND_V2.md).
In-memory DBs, no network."""

from __future__ import annotations

from unittest.mock import MagicMock

from windyfly.control_panel import set_slider
from windyfly.memory.database import Database
from windyfly.memory.write_queue import Priority, WriteQueue
from windyfly.observability.events import log_event
from windyfly.personality.versioning import rollback_personality, snapshot_personality


def test_p4_52_a_rollback_is_recorded_as_a_rollback():
    db = Database(":memory:")
    try:
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        set_slider(db, "humor", 9)
        assert rollback_personality(db, "2099-01-01") == 1
        last = db.fetchone("SELECT old_value, new_value, changed_by FROM soul_history "
                           "ORDER BY created_at DESC, rowid DESC LIMIT 1")
    finally:
        db.close()
    assert (last["old_value"], last["new_value"], last["changed_by"]) == ("9", "5", "rollback")


def test_p4_54_events_are_written_at_low_priority():
    wq = WriteQueue()
    wq.enqueue = MagicMock()
    log_event(MagicMock(), wq, "decay.run", {"decayed": 0})
    assert wq.enqueue.call_args.args[0] == Priority.LOW


