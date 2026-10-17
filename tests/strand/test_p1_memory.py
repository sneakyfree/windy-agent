"""Strand v2, Part 1: §4 DB schema and gene 0.3 database layer (docs/strand/STRAND_V2.md).

Each test proves one codon's promise on a real temp-file database. No network, no home dir.
"""

from __future__ import annotations

import sqlite3
import threading
import uuid

import pytest

from windyfly.memory.database import _MIGRATIONS, Database
from windyfly.memory.write_queue import Priority, WriteQueue

# The columns the strand promised in Part 1 §4. Later migrations may add columns, never drop these.
SPEC_COLUMNS = {
    "nodes": {"id", "user_id", "scope_id", "type", "name", "metadata", "epistemic_status", "confidence",
              "source", "verification_method", "last_verified_at", "valid_from", "valid_until",
              "decay_score", "created_at", "updated_at"},
    "episodes": {"id", "user_id", "session_id", "role", "content", "summary", "token_count", "cost_usd",
                 "emotional_context", "embedding", "embedding_model", "embedding_version",
                 "last_accessed_at", "created_at"},
    "soul": {"id", "user_id", "key", "value", "version", "source", "created_at", "updated_at"},
    "skills": {"id", "name", "version", "code", "language", "description", "permissions_required",
               "risk_level", "eval_score", "eval_results", "promoted", "usage_count", "success_count",
               "failure_count", "parent_skill_id", "created_at", "last_used"},
    "failures": {"id", "fault_type", "description", "root_cause", "correction_action",
                 "correction_skill_id", "improvement_verified", "created_at", "resolved_at"},
    "cost_ledger": {"id", "model", "input_tokens", "output_tokens", "cost_usd", "task_type", "created_at"},
}


@pytest.fixture
def db(tmp_path):
    d = Database(str(tmp_path / "nested" / "dir" / "strand.db"))
    yield d
    d.close()


def pragma(db: Database, name: str):
    return next(iter(db.fetchone(f"PRAGMA {name}").values()))


def test_p1_4_journal_mode_wal(db):
    assert str(pragma(db, "journal_mode")).lower() == "wal"


def test_p1_4_synchronous_normal(db):
    assert pragma(db, "synchronous") == 1


def test_p1_4_foreign_keys_on(db):
    assert pragma(db, "foreign_keys") == 1


def test_p1_4_busy_timeout_5000(db):
    assert pragma(db, "busy_timeout") == 5000


@pytest.mark.parametrize("table", sorted(SPEC_COLUMNS))
def test_p1_4_spec_columns(db, table):
    cols = {r["name"] for r in db.fetchall(f"PRAGMA table_info({table})")}
    assert SPEC_COLUMNS[table] <= cols, f"{table} lost {sorted(SPEC_COLUMNS[table] - cols)}"


def test_p1_4_episodes_fts_finds_saved_text(db):
    from windyfly.memory.episodes import save_episode, search_episodes

    save_episode(db, role="user", content="the heron landed on the quay", session_id="s1")
    save_episode(db, role="user", content="nothing to see here", session_id="s1")
    sql = db.fetchone("SELECT sql FROM sqlite_master WHERE name = 'episodes_fts'")["sql"]
    assert "fts5" in sql.lower()
    hits = search_episodes(db, "heron")
    assert [h["content"] for h in hits] == ["the heron landed on the quay"]


def test_p1_4_schema_version_tracks_every_migration(db):
    versions = {r["version"] for r in db.fetchall("SELECT version FROM schema_version")}
    assert 1 in versions
    assert max(versions) == max(_MIGRATIONS)


def test_p1_03_creates_the_data_dir(tmp_path):
    path = tmp_path / "a" / "b" / "c.db"
    Database(str(path)).close()
    assert path.exists()


def test_p1_03_parse_decltypes(db):
    db.execute("CREATE TABLE t (at TIMESTAMP)")
    db.execute("INSERT INTO t VALUES ('2026-10-11 12:00:00')")
    value = db.fetchone("SELECT at FROM t")["at"]
    assert value.year == 2026 and value.hour == 12   # a datetime, not a string


def test_p1_03_rows_are_addressed_by_column_name(db):
    """execute / executemany / fetchone / fetchall: the connection uses sqlite3.Row and the
    helpers hand back plain dicts keyed by column name."""
    assert db.conn.row_factory is sqlite3.Row
    assert db.fetchone("SELECT 1 AS one, 'x' AS two") == {"one": 1, "two": "x"}
    assert db.fetchall("SELECT 2 AS n") == [{"n": 2}]
    db.execute("CREATE TABLE m (n INTEGER)")
    db.executemany("INSERT INTO m VALUES (?)", [(1,), (2,), (3,)])
    assert db.fetchone("SELECT COUNT(*) AS c FROM m") == {"c": 3}


def test_p1_03_reopen_does_not_rerun_migrations(tmp_path):
    path = str(tmp_path / "re.db")
    first = Database(path)
    first.execute("INSERT INTO soul (id, key, value) VALUES (?, 'k', 'v')", (str(uuid.uuid4()),))
    first.commit()
    first.close()
    second = Database(path)
    try:
        assert second.fetchone("SELECT value FROM soul WHERE key = 'k'")["value"] == "v"
        rows = second.fetchall("SELECT version FROM schema_version")
        assert len(rows) == len({r["version"] for r in rows})   # each migration recorded once
    finally:
        second.close()


def test_p1_03_write_queue_runs_on_a_daemon_thread():
    wq = WriteQueue()
    seen = []
    done = threading.Event()
    wq.start()
    try:
        wq.enqueue(Priority.HIGH, lambda: (seen.append(threading.current_thread()), done.set()))
        assert done.wait(5)
    finally:
        wq.stop()
    assert seen[0] is not threading.main_thread() and seen[0].daemon


def test_p1_03_timestamps_parse_without_the_deprecated_defaults():
    import datetime as dt
    import warnings

    from windyfly.memory.database import Database

    db = Database(":memory:")
    try:
        db.conn.execute("CREATE TABLE t_p1_03_22 (at TIMESTAMP, on_day DATE)")
        when, day = dt.datetime(2026, 10, 16, 23, 5, 7, 123456), dt.date(2026, 10, 16)
        with warnings.catch_warnings():
            warnings.simplefilter("error", DeprecationWarning)
            db.conn.execute("INSERT INTO t_p1_03_22 VALUES (?, ?)", (when, day))
            db.conn.execute("INSERT INTO t_p1_03_22 VALUES (CURRENT_TIMESTAMP, CURRENT_DATE)")
            rows = db.conn.execute("SELECT at, on_day FROM t_p1_03_22").fetchall()
    finally:
        db.close()
    assert (rows[0][0], rows[0][1]) == (when, day)
    assert isinstance(rows[1][0], dt.datetime) and isinstance(rows[1][1], dt.date)


def test_p1_03_a_failed_migration_run_leaves_nothing_behind(tmp_path, monkeypatch):
    from windyfly.memory import database as dbmod

    path = str(tmp_path / "m.db")
    dbmod.Database(path).close()                     # fully migrated
    top = max(dbmod._MIGRATIONS)
    good = (f"CREATE TABLE t_good (x INTEGER);\n"
            f"INSERT INTO schema_version (version, description) VALUES ({top + 1}, 'good');\n")
    bad = (f"CREATE TABLE t_half (x INTEGER);\n"
           f"INSERT INTO no_such_table VALUES (1);\n"
           f"INSERT INTO schema_version (version, description) VALUES ({top + 2}, 'bad');\n")
    monkeypatch.setitem(dbmod._MIGRATIONS, top + 1, ("good", good))
    monkeypatch.setitem(dbmod._MIGRATIONS, top + 2, ("bad", bad))
    with pytest.raises(sqlite3.OperationalError):
        dbmod.Database(path)
    conn = sqlite3.connect(path)
    try:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    finally:
        conn.close()
    assert "t_good" not in names and "t_half" not in names
    assert version == top
