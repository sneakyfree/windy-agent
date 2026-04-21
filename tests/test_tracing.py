"""Tests for Wave 14 tracing spine.

Covers the contextvar primitive, the log filter, the migration that
adds request_id columns, and end-to-end propagation through
save_episode / log_event / log_cost / record_action_start so any
DB row created during a request carries the request_id.
"""

from __future__ import annotations

import logging
import re
import tempfile
import time
from pathlib import Path

import pytest

from windyfly.agent import tracing
from windyfly.memory.database import Database


# ── ContextVar primitive ─────────────────────────────────────────────


class TestRequestIdPrimitive:
    def test_set_returns_uuid_hex(self):
        rid = tracing.set_request_id()
        assert re.fullmatch(r"[0-9a-f]{32}", rid)

    def test_set_with_explicit_value_uses_it(self):
        rid = tracing.set_request_id("abcd1234" * 4)
        assert rid == "abcd1234" * 4

    def test_get_returns_set_value(self):
        rid = tracing.set_request_id()
        assert tracing.get_request_id() == rid

    def test_short_form_is_eight_chars(self):
        tracing.set_request_id("a" * 32)
        assert tracing.request_id_short() == "aaaaaaaa"

    def test_user_form_is_six_chars(self):
        tracing.set_request_id("a" * 32)
        assert tracing.request_id_for_user() == "aaaaaa"

    def test_short_form_when_unset_returns_dashes(self):
        # Reset the contextvar to its default
        tracing.request_id_var.set(None)
        assert tracing.request_id_short() == "--------"
        assert tracing.request_id_for_user() == "------"


# ── Log filter ───────────────────────────────────────────────────────


class TestLogFilter:
    def test_filter_attaches_request_id_to_record(self):
        flt = tracing.RequestIdLogFilter()
        tracing.set_request_id("deadbeef" * 4)
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x", lineno=1,
            msg="hi", args=(), exc_info=None,
        )
        assert flt.filter(rec) is True
        assert rec.request_id == "deadbeef"

    def test_filter_uses_dashes_when_no_request(self):
        flt = tracing.RequestIdLogFilter()
        tracing.request_id_var.set(None)
        rec = logging.LogRecord(
            name="test", level=logging.INFO, pathname="x", lineno=1,
            msg="hi", args=(), exc_info=None,
        )
        flt.filter(rec)
        assert rec.request_id == "--------"

    def test_install_is_idempotent(self):
        # Calling install twice shouldn't stack duplicate filters
        tracing.install_log_filter()
        tracing.install_log_filter()
        root = logging.getLogger()
        count = sum(
            1 for f in root.filters
            if isinstance(f, tracing.RequestIdLogFilter)
        )
        assert count == 1


# ── Migration applies cleanly + idempotent ───────────────────────────


@pytest.fixture
def fresh_db():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "t.db"))
        try:
            yield db
        finally:
            db.close()


class TestMigration:
    def test_request_id_columns_present(self, fresh_db):
        # PRAGMA table_info returns rows with name field
        for table in ("events", "agent_actions", "episodes", "cost_ledger"):
            cols = fresh_db.fetchall(f"SELECT name FROM pragma_table_info('{table}')")
            names = {c["name"] for c in cols}
            assert "request_id" in names, f"{table} missing request_id"

    def test_request_id_indices_present(self, fresh_db):
        idx = fresh_db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%_request_id'"
        )
        names = {r["name"] for r in idx}
        for table in ("events", "agent_actions", "episodes", "cost_ledger"):
            assert f"idx_{table}_request_id" in names

    def test_migration_idempotent_on_reopen(self, fresh_db):
        # Closing and reopening on the same path must not re-run the
        # ALTER TABLE statements (which would fail on duplicate column).
        path = fresh_db.db_path
        fresh_db.close()
        # Reopen
        db2 = Database(path)
        try:
            cols = db2.fetchall("SELECT name FROM pragma_table_info('events')")
            names = {c["name"] for c in cols}
            assert "request_id" in names
        finally:
            db2.close()


# ── End-to-end propagation through write functions ───────────────────


class TestPropagation:
    def test_save_episode_picks_up_contextvar(self, fresh_db):
        from windyfly.memory.episodes import save_episode

        tracing.set_request_id("c0" * 16)  # 32-char hex
        eid = save_episode(fresh_db, "user", "hello world", session_id="s1")
        row = fresh_db.fetchone("SELECT request_id FROM episodes WHERE id = ?", (eid,))
        assert row["request_id"] == "c0" * 16

    def test_save_episode_explicit_request_id_wins(self, fresh_db):
        from windyfly.memory.episodes import save_episode

        tracing.set_request_id("a" * 32)
        eid = save_episode(
            fresh_db, "user", "hi", session_id="s1",
            request_id="b" * 32,
        )
        row = fresh_db.fetchone("SELECT request_id FROM episodes WHERE id = ?", (eid,))
        assert row["request_id"] == "b" * 32

    def test_save_episode_unset_context_stores_null(self, fresh_db):
        from windyfly.memory.episodes import save_episode

        tracing.request_id_var.set(None)
        eid = save_episode(fresh_db, "user", "hi", session_id="s1")
        row = fresh_db.fetchone("SELECT request_id FROM episodes WHERE id = ?", (eid,))
        assert row["request_id"] is None

    def test_log_cost_picks_up_contextvar(self, fresh_db):
        from windyfly.memory.cost_ledger import log_cost

        tracing.set_request_id("d" * 32)
        eid = log_cost(fresh_db, "claude-sonnet-4-6", 100, 50, 0.01)
        row = fresh_db.fetchone("SELECT request_id FROM cost_ledger WHERE id = ?", (eid,))
        assert row["request_id"] == "d" * 32

    def test_log_event_propagates_through_write_queue(self, fresh_db):
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.observability.events import log_event

        tracing.set_request_id("e" * 32)
        wq = WriteQueue()
        wq.start()
        try:
            log_event(fresh_db, wq, "agent.respond", {"k": "v"})
            # WriteQueue is async; spin briefly for the row to land
            for _ in range(50):
                row = fresh_db.fetchone(
                    "SELECT request_id FROM events WHERE event_type = ?",
                    ("agent.respond",),
                )
                if row:
                    break
                time.sleep(0.02)
            assert row is not None
            assert row["request_id"] == "e" * 32
        finally:
            wq.stop()

    def test_record_action_start_propagates_through_write_queue(self, fresh_db):
        from windyfly.memory.agent_actions import record_action_start
        from windyfly.memory.write_queue import WriteQueue

        tracing.set_request_id("f" * 32)
        wq = WriteQueue()
        wq.start()
        try:
            import uuid
            aid = str(uuid.uuid4())
            record_action_start(
                fresh_db, wq,
                action_id=aid, capability_id="fs.read_file",
                tier=1, band="OWNER", sandbox_tier="host_readonly",
                args_json='{"path":"/tmp/x"}',
                started_at="2026-04-21 16:00:00",
            )
            for _ in range(50):
                row = fresh_db.fetchone(
                    "SELECT request_id FROM agent_actions WHERE id = ?", (aid,),
                )
                if row:
                    break
                time.sleep(0.02)
            assert row is not None
            assert row["request_id"] == "f" * 32
        finally:
            wq.stop()


# ── Integration: errors classifier uses request_id ───────────────────


class TestErrorReportIdTiesToRequestId:
    def test_classify_uses_in_flight_request_id(self):
        from windyfly.channels import errors

        tracing.set_request_id("abc123" + "0" * 26)
        ce = errors.classify(RuntimeError("boom"))
        # The user_message should contain the same 6 chars as
        # request_id_for_user() — proving the trace is unified.
        assert "err:abc123" in ce.user_message
        assert "err:abc123" in ce.log_message

    def test_classify_falls_back_when_no_request_context(self):
        from windyfly.channels import errors

        tracing.request_id_var.set(None)
        ce = errors.classify(RuntimeError("boom"))
        # Should still emit a 6-hex token (just freshly generated)
        m = re.search(r"err:([0-9a-f]{6})", ce.log_message)
        assert m is not None
