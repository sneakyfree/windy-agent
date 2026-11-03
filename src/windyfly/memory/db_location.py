"""Where the agent's memory database lives: ONE rule for every reader and writer (Boss + Hub 11-03).

The default ``data/windyfly.db`` was RELATIVE, so each process resolved it against its own working directory. On 11-03
``windy home`` imported a handed-over agent's memory into one folder's file while the agent, started from another
folder, read an empty one: it greeted its owner as a stranger and never answered the question (#696 lab C).

A relative path is now resolved against the agent's STATE dir (WINDY_STATE_DIR, default ~/.windy; never the package
folder). Agents alive today keep the file they already use:

  1. an absolute path (config or WINDYFLY_DB_PATH): used as it is
  2. only an old place has a database: that one (logged once). The old places are where the relative path pointed
     before: the working folder, then the install folder (the project root; the same folder for a pip install)
  3. only the state dir has one, or neither does: the state dir
  4. BOTH have one: the one holding a handover record (bring-home's turnover letter); if both or neither do, the OLD
     one, warned once + agent.db_path_ambiguous. Never merged, moved or deleted: that is its own data act with its own
     GO (Boss 12:23Z FINAL).

Only the agent's own run and bring-home ever CREATE a database (Hub's guard, Boss 12:23Z): a read-only command
(status, a backup, a self-test) uses existing_db_path() and opens nothing when there is no file, so a command run from
another folder never leaves an empty database behind for rule 4 to trip over.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path

from windyfly.platform import windy_state_dir

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = "data/windyfly.db"
# agent.db_path_ambiguous is declared at Windy Admin but not live until its next HQ deploy (Admin 11-03 12:34Z: "don't
# emit until I send 'declared and live'"). Turned on by its own one-line PR then; until then the warning log stands.
EMIT_AMBIGUOUS_EVENT = False
_said: set[str] = set()


def _once(key: str, level: int, msg: str, *args: object) -> None:
    if key not in _said:
        _said.add(key)
        logger.log(level, msg, *args)


def has_handover_record(path: Path) -> bool:
    """True when the database holds bring-home's turnover letter. Opened read-only: never creates or changes it."""
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT 1 FROM nodes WHERE type = 'turnover_letter' LIMIT 1").fetchone()
        finally:
            con.close()
        return row is not None
    except sqlite3.Error:
        return False


def resolve_db_path(configured: str | None = None) -> str:
    """The memory database path for ``configured`` (a config's memory.db_path or WINDYFLY_DB_PATH; None = the
    environment, then the default). See the module doc for the rule."""
    raw = (configured or os.environ.get("WINDYFLY_DB_PATH", "") or DEFAULT_DB_PATH).strip()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path)
    new = (windy_state_dir() / path).resolve()
    found = [(label, p) for label, p in _old_places(path) if p != new and p.is_file()]
    if not found:
        return str(new)
    label, old = found[0]
    if not new.is_file():
        _once(f"old:{old}", logging.INFO, "memory: using the database at its old place (%s)", old)
        return str(old)
    old_h, new_h = has_handover_record(old), has_handover_record(new)
    pick = new if new_h and not old_h else old
    if old_h == new_h:
        _once(f"both:{old}:{new}", logging.WARNING,
              "memory: a database exists at its old place (%s) AND in the state folder (%s); using %s. Neither was "
              "changed.", old, new, pick)
        _ambiguous_event("state_folder" if pick == new else label)
    return str(pick)


def _old_places(path: Path) -> list[tuple[str, Path]]:
    """Where the relative path pointed before, labelled as Admin's event names them: the working folder, then the
    install folder (the same folder for a pip install, kept once)."""
    places: list[tuple[str, Path]] = []
    try:
        places.append(("working_folder", (Path.cwd() / path).resolve()))
    except OSError:  # the working folder was deleted under this process (Hub on #699)
        pass
    try:
        from windyfly.platform import get_project_root

        places.append(("install_folder", (get_project_root() / path).resolve()))
    except Exception:  # noqa: BLE001
        pass
    out: list[tuple[str, Path]] = []
    for label, p in places:
        if all(p != q for _, q in out):
            out.append((label, p))
    return out


def existing_db_path(configured: str | None = None) -> str | None:
    """For a READ-ONLY use: the resolved path when a database is there, else None (never create one)."""
    path = resolve_db_path(configured)
    return path if Path(path).is_file() else None


def _ambiguous_event(picked: str) -> None:
    """agent.db_path_ambiguous to the Windy Admin ingest (Boss 11-03): which place won (working_folder,
    install_folder or state_folder, Admin's declared values), never a path or content."""
    try:
        from datetime import UTC, datetime

        from windyfly.observability import admin_telemetry as _at

        if not EMIT_AMBIGUOUS_EVENT or not _at.enabled():
            return
        passport = _at._own_passport()
        if not passport:
            return
        _at.send_event(None, {
            "ts": datetime.now(UTC).isoformat(),
            "platform": "windy-agent",
            "service": "fly",
            "event_type": "agent.db_path_ambiguous",
            "actor_type": "agent",
            "actor_id": passport,
            "session_id": None,
            "metadata": {"picked": picked},
        })
    except Exception:  # noqa: BLE001 — telemetry never breaks the agent
        pass


def _reset_for_tests() -> None:
    _said.clear()
