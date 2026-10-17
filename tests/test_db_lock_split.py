"""The memory database must never split between processes.

Windy 0, 2026-10-15: the agent's Database() read the file header with a
plain open()/close(). Closing any descriptor on a file drops every POSIX
lock the process holds on it, SQLite's included, so the agent ran with
no visible locks. When the WindyTalk bridge restarted, its closing
connection believed it was the last one, checkpointed and unlinked the
WAL, and from then on the agent wrote to a deleted WAL while the bridge
used a fresh one. The daily backup's shutil.copy2 had the same effect
(and left out the WAL). These tests pin the fix.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="POSIX lock semantics checked on Linux"
)


def _other_process_opens_and_closes(db: Path) -> None:
    """What the bridge (or any CLI tool) does: open, read, close."""
    code = (
        "import sqlite3, sys\n"
        "c = sqlite3.connect(sys.argv[1])\n"
        "c.execute('select count(*) from sqlite_master').fetchall()\n"
        "c.close()\n"
    )
    subprocess.run([sys.executable, "-c", code, str(db)], check=True, timeout=60)


def _wal_inode(db: Path) -> int:
    return os.stat(str(db) + "-wal").st_ino


def _holder(db: Path):
    from windyfly.memory.database import Database

    d = Database(str(db))
    d.execute("INSERT INTO soul (id, key, value) VALUES (?, ?, ?)", ("a", "k", "v"))
    d.commit()
    return d


def test_second_database_in_process_keeps_the_wal(tmp_path):
    """The exact Windy 0 sequence: a second Database() in the same process
    (decay thread, trust check, journal job), then another process closes."""
    from windyfly.memory.database import Database
    from windyfly.memory.split_check import deleted_side_files

    db = tmp_path / "agent.db"
    first = _holder(db)
    ino = _wal_inode(db)

    Database(str(db))
    _other_process_opens_and_closes(db)

    assert (Path(str(db) + "-wal")).exists(), "another process unlinked our WAL"
    assert _wal_inode(db) == ino, "the WAL was replaced under a live connection"
    assert deleted_side_files(db) == []
    first.close()


def test_backup_snapshot_keeps_locks_and_includes_wal_rows(tmp_path):
    """The daily backup must not drop locks, and must contain rows that so
    far exist only in the WAL (the old file copy silently left them out)."""
    from windyfly.memory.snapshot import snapshot_bytes

    db = tmp_path / "agent.db"
    holder = _holder(db)
    ino = _wal_inode(db)

    data = snapshot_bytes(db)
    _other_process_opens_and_closes(db)

    assert _wal_inode(db) == ino
    snap = tmp_path / "snap.db"
    snap.write_bytes(data)
    c = sqlite3.connect(str(snap))
    try:
        assert c.execute("select count(*) from soul where id='a'").fetchone()[0] == 1
        assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        c.close()
    holder.close()


def test_snapshot_db_writes_a_standalone_file(tmp_path):
    from windyfly.memory.snapshot import snapshot_db

    db = tmp_path / "agent.db"
    holder = _holder(db)
    dest = tmp_path / "copy.db"
    snapshot_db(db, dest)
    assert not Path(str(dest) + "-wal").exists()
    c = sqlite3.connect(str(dest))
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert c.execute("select count(*) from soul").fetchone()[0] == 1
    finally:
        c.close()
    holder.close()


def test_snapshot_of_missing_db_raises(tmp_path):
    from windyfly.memory.snapshot import snapshot_db

    with pytest.raises(FileNotFoundError):
        snapshot_db(tmp_path / "nope.db", tmp_path / "out.db")
    assert not (tmp_path / "nope.db").exists(), "snapshot must not create the source"


def test_split_check_sees_a_deleted_wal_we_still_hold(tmp_path):
    from windyfly.memory.split_check import deleted_side_files

    if not Path("/proc/self/fd").is_dir():
        pytest.skip("no /proc")
    db = tmp_path / "agent.db"
    db.write_bytes(b"")
    wal = Path(str(db) + "-wal")
    wal.write_bytes(b"x")
    fh = open(wal, "rb")
    try:
        assert deleted_side_files(db) == []
        wal.unlink()
        assert deleted_side_files(db) == ["agent.db-wal"]
    finally:
        fh.close()


def test_split_check_matches_only_this_database(tmp_path):
    from windyfly.memory.split_check import deleted_side_files

    fake = tmp_path / "fd"
    fake.mkdir()
    db = tmp_path / "agent.db"
    real = os.path.realpath(db)
    (fake / "3").symlink_to(real + "-wal (deleted)")
    (fake / "4").symlink_to(real + "-shm (deleted)")
    (fake / "5").symlink_to(real + "-wal")                       # live, fine
    (fake / "6").symlink_to(real + ".other-wal (deleted)")       # not ours
    (fake / "7").symlink_to("/elsewhere/agent.db-wal (deleted)")  # other db
    assert deleted_side_files(db, fd_dir=fake) == ["agent.db-shm", "agent.db-wal"]
    assert deleted_side_files(db, fd_dir=tmp_path / "missing") == []


def test_copy_db_keeps_the_bytes_of_a_file_that_is_not_a_database(tmp_path):
    """Exports and the pre-restore copy keep a broken file as it is
    (it may be recoverable); the strict cloud snapshot refuses it."""
    from windyfly.memory.snapshot import copy_db, snapshot_bytes

    bad = tmp_path / "agent.db"
    bad.write_bytes(b"x" * 64)
    out = tmp_path / "out.db"
    copy_db(bad, out)
    assert out.read_bytes() == b"x" * 64
    with pytest.raises(sqlite3.DatabaseError):
        snapshot_bytes(bad)


def test_cloud_backup_of_a_non_database_is_a_typed_failure(tmp_path, monkeypatch):
    """Never upload garbage over good backups (Cloud keeps only a few)."""
    import asyncio
    from unittest.mock import AsyncMock

    import windyfly.cloud_backup as cb

    monkeypatch.setenv("WINDY_BACKUP_KEY", "owner-key")  # uploads need an owner-held key (Boss 10-17)
    monkeypatch.setattr("windyfly.trust.gate.require_trust", AsyncMock(return_value=None))
    monkeypatch.setattr(
        "windyfly.auth.bot_credentials.ecosystem_auth_header",
        AsyncMock(return_value={"Authorization": "Bearer t"}),
    )
    monkeypatch.setattr("windyfly.auth.bot_credentials.get_bot_key", AsyncMock(return_value=None))
    bad = tmp_path / "agent.db"
    bad.write_bytes(b"x" * 64)
    res = asyncio.run(cb.backup_to_cloud({"memory": {"db_path": str(bad)}}))
    assert res["success"] is False
    assert "snapshot" in res["error"]
