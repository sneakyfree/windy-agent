"""Tests for the database and memory layer.

Tests database creation, migrations, episodes CRUD, nodes CRUD,
soul CRUD, cost_ledger, failures, and the write queue.
"""

from __future__ import annotations

import time

from windyfly.memory.cost_ledger import get_daily_spend, get_recent_costs, log_cost
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes, save_episode
from windyfly.memory.failures import (
    check_recurring_failure,
    get_recent_failures,
    log_failure,
    resolve_failure,
)
from windyfly.memory.nodes import get_node, get_nodes_by_type, search_nodes, upsert_node
from windyfly.memory.soul import get_all_soul, get_soul, upsert_soul
from windyfly.memory.write_queue import Priority, WriteQueue


def _make_db() -> Database:
    """Create an in-memory database for testing."""
    return Database(":memory:")


# === Database creation / migrations ===


class TestDatabaseCreation:
    def test_creates_all_tables(self):
        db = _make_db()
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        table_names = {t["name"] for t in tables}
        assert "nodes" in table_names
        assert "episodes" in table_names
        assert "soul" in table_names
        assert "skills" in table_names
        assert "failures" in table_names
        assert "cost_ledger" in table_names
        assert "schema_version" in table_names
        db.close()

    def test_schema_version_is_current(self):
        db = _make_db()
        row = db.fetchone("SELECT MAX(version) as v FROM schema_version")
        assert row is not None
        assert row["v"] == 4
        db.close()

    def test_wal_mode(self):
        """WAL mode is not available for :memory: DBs, but the PRAGMA should not error."""
        db = _make_db()
        # Just verify the db is usable
        db.execute("SELECT 1")
        db.close()


# === Episodes CRUD ===


class TestEpisodes:
    def test_save_and_retrieve(self):
        db = _make_db()
        eid = save_episode(db, "user", "Hello!", session_id="s1")
        assert eid is not None
        episodes = get_recent_episodes(db, limit=10, session_id="s1")
        assert len(episodes) == 1
        assert episodes[0]["content"] == "Hello!"
        assert episodes[0]["role"] == "user"
        db.close()

    def test_multiple_episodes_ordered(self):
        db = _make_db()
        save_episode(db, "user", "First", session_id="s1")
        save_episode(db, "assistant", "Second", session_id="s1")
        save_episode(db, "user", "Third", session_id="s1")
        episodes = get_recent_episodes(db, limit=10, session_id="s1")
        assert len(episodes) == 3
        # Most recent first
        assert episodes[0]["content"] == "Third"
        assert episodes[2]["content"] == "First"
        db.close()

    def test_session_filter(self):
        db = _make_db()
        save_episode(db, "user", "Session A", session_id="a")
        save_episode(db, "user", "Session B", session_id="b")
        a_episodes = get_recent_episodes(db, session_id="a")
        assert len(a_episodes) == 1
        assert a_episodes[0]["content"] == "Session A"
        db.close()

    def test_emotional_context(self):
        db = _make_db()
        eid = save_episode(db, "user", "Ugh!", emotional_context="stressed")
        episodes = get_recent_episodes(db, limit=1)
        assert episodes[0]["emotional_context"] == "stressed"
        db.close()


# === Nodes CRUD ===


class TestNodes:
    def test_upsert_new(self):
        db = _make_db()
        nid = upsert_node(db, "person", "Grant", metadata={"role": "founder"})
        assert nid is not None
        node = get_node(db, nid)
        assert node is not None
        assert node["name"] == "Grant"
        assert node["type"] == "person"
        db.close()

    def test_upsert_existing_updates(self):
        db = _make_db()
        nid1 = upsert_node(db, "preference", "coffee", confidence=0.8)
        nid2 = upsert_node(db, "preference", "coffee", confidence=0.95)
        assert nid1 == nid2
        node = get_node(db, nid1)
        assert node["confidence"] == 0.95
        db.close()

    def test_get_by_type(self):
        db = _make_db()
        upsert_node(db, "person", "Alice")
        upsert_node(db, "person", "Bob")
        upsert_node(db, "location", "NYC")
        people = get_nodes_by_type(db, "person")
        assert len(people) == 2
        db.close()

    def test_search_by_name(self):
        db = _make_db()
        upsert_node(db, "preference", "dark mode", metadata={"category": "ui"})
        upsert_node(db, "preference", "vim keybindings")
        results = search_nodes(db, "dark")
        assert len(results) >= 1
        assert any("dark" in r["name"] for r in results)
        db.close()


# === Soul CRUD ===


class TestSoul:
    def test_upsert_and_get(self):
        db = _make_db()
        sid = upsert_soul(db, "humor_level", "7")
        row = get_soul(db, "humor_level")
        assert row is not None
        assert row["value"] == "7"
        assert row["version"] == 1
        db.close()

    def test_upsert_increments_version(self):
        db = _make_db()
        upsert_soul(db, "humor_level", "7")
        upsert_soul(db, "humor_level", "9")
        row = get_soul(db, "humor_level")
        assert row["value"] == "9"
        assert row["version"] == 2
        db.close()

    def test_get_all(self):
        db = _make_db()
        upsert_soul(db, "humor_level", "7")
        upsert_soul(db, "formality", "4")
        all_rows = get_all_soul(db)
        assert len(all_rows) == 2
        db.close()


# === Cost Ledger ===


class TestCostLedger:
    def test_log_and_retrieve(self):
        db = _make_db()
        lid = log_cost(db, "gpt-4o-mini", 100, 50, 0.001)
        costs = get_recent_costs(db, limit=5)
        assert len(costs) == 1
        assert costs[0]["model"] == "gpt-4o-mini"
        db.close()

    def test_daily_spend(self):
        db = _make_db()
        log_cost(db, "gpt-4o-mini", 100, 50, 0.001)
        log_cost(db, "gpt-4o-mini", 200, 100, 0.002)
        daily = get_daily_spend(db)
        assert abs(daily - 0.003) < 0.0001
        db.close()


# === Failures ===


class TestFailures:
    def test_log_and_retrieve(self):
        db = _make_db()
        fid = log_failure(db, "factual_error", "Said Paris is in Germany")
        failures = get_recent_failures(db)
        assert len(failures) == 1
        assert failures[0]["fault_type"] == "factual_error"
        db.close()

    def test_resolve(self):
        db = _make_db()
        fid = log_failure(db, "preference_miss", "Forgot dark mode preference")
        resolve_failure(db, fid, correction_action="Added to soul preferences")
        failures = get_recent_failures(db, unresolved_only=True)
        assert len(failures) == 0
        db.close()

    def test_recurring_check(self):
        db = _make_db()
        log_failure(db, "factual_error", "Paris is in Germany")
        assert check_recurring_failure(db, "factual_error", "Paris is in Germany")
        assert not check_recurring_failure(db, "preference_miss", "Something else")
        db.close()


# === Write Queue ===


class TestWriteQueue:
    def test_processes_items(self):
        results = []

        def append_val(val):
            results.append(val)

        wq = WriteQueue()
        wq.start()
        wq.enqueue(Priority.HIGH, append_val, "high")
        wq.enqueue(Priority.MEDIUM, append_val, "medium")
        wq.enqueue(Priority.LOW, append_val, "low")

        # Give the worker time to process
        time.sleep(1.0)
        wq.stop()

        assert "high" in results
        assert "medium" in results
        assert "low" in results

    def test_high_priority_first(self):
        """HIGH should be processed before MEDIUM and LOW."""
        results = []

        def append_val(val):
            results.append(val)

        wq = WriteQueue()
        # Enqueue before starting so priority ordering matters
        wq.enqueue(Priority.LOW, append_val, "low")
        wq.enqueue(Priority.MEDIUM, append_val, "medium")
        wq.enqueue(Priority.HIGH, append_val, "high")
        wq.start()

        time.sleep(1.0)
        wq.stop()

        assert results[0] == "high"
