"""Strand-to-green Wave 4, Gene 5.1: Trust Dashboard summary fields.

Seeds a real (in-memory) database and checks the numbers
``get_dashboard_summary`` reports for:

* memory.by_epistemic_status / memory.by_scope (per user)
* costs.this_week_usd / this_month_usd / by_task_type
* skills.top_5_by_usage (promoted only, highest usage first, success rate)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from windyfly.dashboard.data import get_dashboard_summary
from windyfly.memory.cost_ledger import log_cost
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node
from windyfly.memory.skills import save_skill


@pytest.fixture
def db(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    d = Database(":memory:")
    yield d
    d.close()


def _backdate_cost(db: Database, entry_id: str, days: int) -> None:
    db.execute(
        "UPDATE cost_ledger SET created_at = datetime('now', ?) WHERE id = ?",
        (f"-{days} days", entry_id),
    )
    db.commit()


def test_Gene_5_1_memory_by_epistemic_status_and_scope(db):
    upsert_node(db, "fact", "a", epistemic_status="verified", scope_id="personal")
    upsert_node(db, "fact", "b", epistemic_status="verified", scope_id="work")
    upsert_node(db, "fact", "c", epistemic_status="user_stated", scope_id="personal")
    upsert_node(db, "fact", "d", epistemic_status="inferred", scope_id="family")
    upsert_node(db, "fact", "e", epistemic_status="speculative", scope_id="personal")
    # Another user's node must not be counted for the default user.
    db.execute(
        "INSERT INTO nodes (id, user_id, type, name, epistemic_status, scope_id) "
        "VALUES ('other-1', 'someone-else', 'fact', 'x', 'verified', 'personal')"
    )
    db.commit()

    mem = get_dashboard_summary(db)["memory"]
    assert mem["total_nodes"] == 5
    assert mem["by_epistemic_status"] == {
        "verified": 2, "user_stated": 1, "inferred": 1, "speculative": 1,
    }
    assert mem["by_scope"] == {"personal": 3, "work": 1, "family": 1}

    other = get_dashboard_summary(db, user_id="someone-else")["memory"]
    assert other["by_epistemic_status"] == {"verified": 1}
    assert other["by_scope"] == {"personal": 1}


def test_Gene_5_1_costs_week_month_and_by_task_type(db):
    log_cost(db, "m", 10, 10, 1.00, task_type="chat")
    recent = log_cost(db, "m", 10, 10, 2.00, task_type="journal")
    old = log_cost(db, "m", 10, 10, 4.00, task_type="chat")
    _backdate_cost(db, recent, 3)
    _backdate_cost(db, old, 40)

    costs = get_dashboard_summary(db)["costs"]

    assert costs["today_usd"] == 1.0
    # Week: today + 3 days ago; the 40-day-old row is outside.
    assert costs["this_week_usd"] == 3.0
    # Month: today always; 3 days ago only if it is still this calendar
    # month (UTC, which is what SQLite's 'now' uses); 40 days ago never.
    now = datetime.now(timezone.utc)
    same_month = (now - timedelta(days=3)).month == now.month
    assert costs["this_month_usd"] == (3.0 if same_month else 1.0)
    # By task type is all-time.
    assert costs["by_task_type"]["chat"] == 5.0
    assert costs["by_task_type"] == {"chat": 5.0, "journal": 2.0}


# Was a strict xfail: the REAL BUG Wave 4 found (round(None) on unpriced-only rows); fixed in #500.
def test_Gene_5_1_unpriced_only_task_type_does_not_crash_summary(db):
    log_cost(db, "m", 10, 10, 1.00, task_type="chat")
    log_cost(db, "local-model", 10, 10, None, task_type="intent")
    costs = get_dashboard_summary(db)["costs"]
    assert costs["by_task_type"]["chat"] == 1.0
    assert "intent" in costs["by_task_type"]


def test_Gene_5_1_skills_top_5_by_usage(db):
    usage = {
        "s1": (3, 3), "s2": (50, 25), "s3": (10, 9), "s4": (0, 0),
        "s5": (7, 7), "s6": (20, 10), "s7": (1, 0),
    }
    for name, (used, ok) in usage.items():
        sid = save_skill(db, name, "When asked, do x.", "playbook")
        db.execute(
            "UPDATE skills SET promoted = TRUE, usage_count = ?, success_count = ? "
            "WHERE id = ?", (used, ok, sid))
    # The most-used skill of all is NOT promoted: it must not appear.
    hidden = save_skill(db, "unpromoted", "When asked, do y.", "playbook")
    db.execute("UPDATE skills SET usage_count = 999 WHERE id = ?", (hidden,))
    db.commit()

    skills = get_dashboard_summary(db)["skills"]
    assert skills["total"] == 8
    assert skills["promoted"] == 7
    top = skills["top_5_by_usage"]
    assert [s["name"] for s in top] == ["s2", "s6", "s3", "s5", "s1"]
    assert [s["usage_count"] for s in top] == [50, 20, 10, 7, 3]
    assert [s["success_rate"] for s in top] == [0.5, 0.5, 0.9, 1.0, 1.0]


def test_Gene_5_1_empty_database_has_honest_zero_shapes(db):
    s = get_dashboard_summary(db)
    assert s["memory"]["by_epistemic_status"] == {}
    assert s["memory"]["by_scope"] == {}
    assert s["costs"]["this_week_usd"] == 0.0
    assert s["costs"]["this_month_usd"] == 0.0
    assert s["costs"]["by_task_type"] == {}
    assert s["skills"]["top_5_by_usage"] == []
