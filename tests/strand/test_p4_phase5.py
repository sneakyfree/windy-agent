"""Strand v2, Part 4 genes 5.1-5.4: dashboard, personality versioning, events (docs/strand/STRAND_V2.md).
In-memory DBs, no network."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_gap_closure import _make_bridge, _run
from windyfly.control_panel import set_slider
from windyfly.dashboard.data import get_dashboard_summary
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.database import Database
from windyfly.memory.intents import create_intent
from windyfly.memory.nodes import upsert_node
from windyfly.memory.skills import save_skill
from windyfly.memory.write_queue import Priority, WriteQueue
from windyfly.observability.events import log_event
from windyfly.personality.versioning import rollback_personality, snapshot_personality


def test_p4_51_memory_is_broken_down_by_status_and_scope():
    db = Database(":memory:")
    try:
        upsert_node(db, "fact", "a", epistemic_status="verified", scope_id="personal")
        upsert_node(db, "fact", "b", epistemic_status="verified", scope_id="personal")
        upsert_node(db, "fact", "c", epistemic_status="inferred", scope_id="work")
        upsert_node(db, "fact", "d", epistemic_status="user_stated", scope_id="family")
        memory = get_dashboard_summary(db)["memory"]
    finally:
        db.close()
    assert memory["by_epistemic_status"] == {"verified": 2, "inferred": 1, "user_stated": 1}
    assert memory["by_scope"] == {"personal": 2, "work": 1, "family": 1}


def test_p4_51_costs_today_week_month_and_by_task_type():
    db = Database(":memory:")
    try:
        log_cost(db, "m", 1, 1, 0.01, task_type="chat")                      # today
        three = log_cost(db, "m", 1, 1, 0.02, task_type="chat")             # 3 days ago
        forty = log_cost(db, "m", 1, 1, 0.04, task_type="intent")           # 40 days ago
        db.execute("UPDATE cost_ledger SET created_at = datetime('now', '-3 days') WHERE id = ?", (three,))
        db.execute("UPDATE cost_ledger SET created_at = datetime('now', '-40 days') WHERE id = ?", (forty,))
        db.commit()
        same_month = db.fetchone(
            "SELECT date('now', '-3 days') >= date('now', 'start of month') AS m")["m"]
        costs = get_dashboard_summary(db)["costs"]
    finally:
        db.close()
    assert costs["today_usd"] == 0.01
    assert costs["this_week_usd"] == 0.03
    assert costs["this_month_usd"] == (0.03 if same_month else 0.01)
    assert costs["by_task_type"] == {"chat": 0.03, "intent": 0.04}


def test_p4_51_top_five_promoted_skills_by_use():
    db = Database(":memory:")
    try:
        for n in range(1, 7):
            sid = save_skill(db, f"s{n}", "Do it.", "playbook")
            db.execute("UPDATE skills SET promoted = TRUE, usage_count = ?, success_count = ? WHERE id = ?",
                       (n * 10, n * 5, sid))
        hidden = save_skill(db, "draft", "Not yet.", "playbook")
        db.execute("UPDATE skills SET usage_count = 1000 WHERE id = ?", (hidden,))
        db.commit()
        skills = get_dashboard_summary(db)["skills"]
    finally:
        db.close()
    assert (skills["total"], skills["promoted"]) == (7, 6)
    top = skills["top_5_by_usage"]
    assert [s["name"] for s in top] == ["s6", "s5", "s4", "s3", "s2"]
    assert top[0] == {"name": "s6", "usage_count": 60, "success_rate": 0.5}


def test_p4_51_goals_counted_by_state():
    db = Database(":memory:")
    try:
        ids = [create_intent(db, f"goal {n}") for n in range(4)]
        db.execute("UPDATE intents SET status = 'completed' WHERE id = ?", (ids[0],))
        db.execute("UPDATE intents SET status = 'paused' WHERE id IN (?, ?)", (ids[1], ids[2]))
        db.commit()
        intents = get_dashboard_summary(db)["intents"]
    finally:
        db.close()
    assert (intents["active"], intents["completed"], intents["abandoned"]) == (1, 1, 2)


def test_p4_51_the_summary_is_served_through_the_bridge():
    bridge, db, _ = _make_bridge()
    try:
        upsert_node(db, "fact", "a")
        result = _run(bridge._dispatch("dashboard.summary", {}))
    finally:
        db.close()
    assert result["dashboard"]["memory"]["total_nodes"] == 1


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


def test_p4_54_events_are_read_through_the_bridge():
    bridge, db, wq = _make_bridge()
    try:
        wq.start()
        log_event(db, wq, "decay.run", {"decayed": 3})
        log_event(db, wq, "agent.respond", {"tokens": 1})
        wq.stop()                                           # stop() drains the queue first
        result = _run(bridge._dispatch("events.list", {"event_type": "decay.run", "limit": 10}))
    finally:
        db.close()
    assert [e["event_type"] for e in result["events"]] == ["decay.run"]
