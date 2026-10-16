"""Consistent copies of a live memory database.

Never copy, hash or read a live SQLite file with plain file I/O
(``shutil.copy2``, ``read_bytes``, ``tarfile.add``). Two reasons:

1. **It drops this process's locks.** Closing any file descriptor on a
   file releases every POSIX lock the process holds on it, SQLite's
   included. The agent kept running with no visible locks, so the next
   other process to close its connection believed it was the last one,
   checkpointed and unlinked the WAL, and the agent went on writing to
   a deleted WAL nobody else could see (Windy 0, 2026-10-15).
2. **It misses recent writes.** In WAL mode the newest commits live in
   ``<db>-wal`` until a checkpoint. A copy of the main file alone is a
   backup of the database as it was at the last checkpoint.

``sqlite3.Connection.backup`` goes through SQLite's own VFS, which keeps
the process's locks intact, and it reads committed WAL frames, so the
copy is the database as every connection currently sees it.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path


def snapshot_db(db_path: str | os.PathLike, dest: str | os.PathLike) -> None:
    """Write a consistent copy of the database at ``db_path`` to ``dest``.

    ``dest`` is overwritten. The copy is a standalone rollback-journal
    file (no -wal/-shm needed to read it).
    """
    src_path = Path(db_path)
    if not src_path.exists():
        raise FileNotFoundError(f"Database not found: {src_path}")
    src = sqlite3.connect(str(src_path))
    try:
        src.execute("PRAGMA busy_timeout=5000;")
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE;")
        finally:
            dst.close()
    finally:
        src.close()


def copy_db(db_path: str | os.PathLike, dest: str | os.PathLike) -> None:
    """Like :func:`snapshot_db`, but keeps the bytes of a file that is not
    a database at all.

    For exports and the copy made before a restore, where a broken file
    is still worth keeping (it may be recoverable with ``.recover``).
    Only SQLite's own "not a database" verdict falls back to a file copy:
    such a file has no healthy connection whose locks a copy could drop.
    Any other error (busy, I/O) is raised, never papered over with a raw
    copy of a live database.
    """
    try:
        snapshot_db(db_path, dest)
    except sqlite3.DatabaseError as e:
        if getattr(e, "sqlite_errorcode", None) != sqlite3.SQLITE_NOTADB:
            raise
        shutil.copyfile(str(db_path), str(dest))


def snapshot_bytes(db_path: str | os.PathLike) -> bytes:
    """Return the bytes of a consistent copy of the database."""
    with tempfile.TemporaryDirectory(prefix="windyfly-snap-") as tmp:
        dest = Path(tmp) / "snapshot.db"
        snapshot_db(db_path, dest)
        return dest.read_bytes()
