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

import contextlib
import os
import shutil
import sqlite3
import stat
import tarfile
import tempfile
from collections.abc import Iterator
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


@contextlib.contextmanager
def private_tar(path: str | os.PathLike) -> Iterator[tarfile.TarFile]:
    """A new ``.tar.gz`` that only the owner's account can read (0600), whatever the umask.

    An export holds the whole memory in plain SQLite AND the ``.env`` secrets; with the usual 0022 umask
    it came out 0644, readable by every account on the machine. The mode is set when the file is created
    (and again with fchmod, for a file that already existed), never after the secrets are in it. A symlink
    at ``path`` is refused (O_NOFOLLOW)."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "wb") as fh, tarfile.open(fileobj=fh, mode="w:gz") as tar:
        yield tar


def tighten_old_exports(folder: str | os.PathLike) -> int:
    """Make export archives written before archives were private (they came out 0644) owner-only too.

    Only ``windyfly-backup-*.tar.gz`` regular files this account owns are touched, and only their mode: the file is
    opened with O_NOFOLLOW (a symlink is skipped, never followed) and fchmod'ed, so nothing is read or rewritten.
    Never raises; returns how many were tightened."""
    if not hasattr(os, "fchmod"):
        return 0
    done = 0
    for p in Path(folder).glob("windyfly-backup-*.tar.gz"):
        # lstat FIRST: never open a FIFO or a device (an O_RDONLY open of a FIFO blocks until a writer comes, and
        # /export also scans the cwd, which may be a shared folder anyone can plant one in; Hub on #686).
        try:
            pre = os.lstat(p)
        except OSError:
            continue
        if not stat.S_ISREG(pre.st_mode) or pre.st_uid != os.getuid() or not pre.st_mode & 0o077:
            continue
        try:
            fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
        except OSError:
            continue
        try:
            st = os.fstat(fd)
            # The same file we checked (not swapped in between), still a regular file of ours.
            if (st.st_ino, st.st_dev) == (pre.st_ino, pre.st_dev) and stat.S_ISREG(st.st_mode) \
                    and st.st_uid == os.getuid() and st.st_mode & 0o077:
                os.fchmod(fd, 0o600)
                done += 1
        except OSError:
            pass
        finally:
            os.close(fd)
    return done
