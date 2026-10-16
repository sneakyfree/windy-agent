"""Notice when this process is writing to a WAL nobody else can see.

If another process deletes ``<db>-wal`` while we hold it open (SQLite
does exactly that when a closing connection believes it is the last
one), our connection keeps writing to the deleted file and every other
process starts a fresh WAL. Both sides keep working, so nothing fails
loudly: memory silently splits in two (Windy 0, 2026-10-15). On Linux
the kernel shows such a handle as ``... (deleted)`` in /proc/self/fd,
so we look for it and log an ERROR. Elsewhere the check is a no-op.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_INTERVAL_SECONDS = 15 * 60
_FD_DIR = Path("/proc/self/fd")


def deleted_side_files(db_path: str | os.PathLike, fd_dir: Path = _FD_DIR) -> list[str]:
    """Names of this database's -wal/-shm files that we hold but are deleted."""
    if not fd_dir.is_dir():
        return []
    real = os.path.realpath(db_path)
    wanted = {real + "-wal", real + "-shm"}
    found: set[str] = set()
    try:
        entries = list(fd_dir.iterdir())
    except OSError:
        return []
    for fd in entries:
        try:
            target = os.readlink(fd)
        except OSError:
            continue
        if target.endswith(" (deleted)") and target[: -len(" (deleted)")] in wanted:
            found.add(os.path.basename(target[: -len(" (deleted)")]))
    return sorted(found)


def start_split_watch(db_path: str, interval: float = _INTERVAL_SECONDS) -> threading.Thread:
    """Start a daemon thread that checks for a memory split every ``interval`` s."""
    def _watch() -> None:
        stop = threading.Event()
        while not stop.wait(interval):
            try:
                gone = deleted_side_files(db_path)
            except Exception as e:  # noqa: BLE001 - a watchdog must never crash the agent
                logger.debug("memory split check skipped: %s", e)
                continue
            if gone:
                logger.error(
                    "memory split: this process is writing to deleted %s of %s; "
                    "other processes on this database cannot see its new memories. "
                    "Do not restart anything until the files are recovered.",
                    ", ".join(gone), db_path,
                )

    t = threading.Thread(target=_watch, daemon=True, name="memory-split-watch")
    t.start()
    return t
