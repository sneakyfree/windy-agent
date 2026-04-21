"""Undo journal — append-only log of reversible destructive actions.

The unique architectural property: every destructive capability writes
its "I am about to do X to Y" record HERE before doing the work. If
the work succeeds, the record stays in the journal for ``retention``
days and the user can reverse it via fs.undo_last_action. If the work
fails mid-flight, the record's already on disk so partial state can
be cleaned up.

Why a file journal (not a DB table — Decision W4-1):

  - One uniform mechanism for every undoable action, including future
    non-file actions like email_send (whose "original_state" is
    "no message was sent" → undo posts a retraction).
  - Survives a corrupted SQLite (the journal is plain NDJSON).
  - Trivially auditable by humans with `cat ~/.windy/undo-journal.ndjson`.
  - Sweepable by a simple cron / launchd timer with mtime checks.

What the competition has: nothing publicly. OpenClaw's shell-based
"undo" is "type the inverse command." Hermes' write_file has no undo
log. Our agent literally rolls back delete-of-47-files via a single
fs.undo_last_action call — that's the marketing moment.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_JOURNAL_PATH = Path.home() / ".windy" / "undo-journal.ndjson"
DEFAULT_RETENTION_DAYS = 30
# Cap original_state contents at 5MB per record. Bigger files don't
# get journaled — the operator gets a warning and the action proceeds
# with undo_supported=False on that specific call.
MAX_ORIGINAL_STATE_BYTES = 5 * 1024 * 1024

# Append-write lock so concurrent capability invocations don't
# interleave half-records into the journal.
_journal_lock = threading.Lock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _expires_iso(retention_days: int = DEFAULT_RETENTION_DAYS) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(days=retention_days)
    ).isoformat(timespec="seconds")


def _journal_path(override: str | None = None) -> Path:
    if override:
        return Path(override).expanduser()
    return DEFAULT_JOURNAL_PATH


def _ensure_journal_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)


def capture_file_state(target: Path) -> dict[str, Any] | None:
    """Build the original_state envelope for a file that's about to be
    overwritten or deleted.

    Returns None if the file doesn't exist (the action is "create new",
    so there's nothing to preserve and nothing to undo via this record).

    Returns a dict with:
      - kind: "file" | "directory" | "symlink"
      - content_b64: base64 of the file contents (only for kind=="file")
      - size: bytes
      - mode: stat mode
      - mtime: int seconds-since-epoch
      - link_target: only for symlink
      - too_big: True if size > MAX_ORIGINAL_STATE_BYTES
        (in which case content_b64 is omitted and undo can't restore
        the file — only record that it was deleted)
    """
    try:
        st = target.lstat()
    except FileNotFoundError:
        return None

    base = {
        "size": st.st_size,
        "mode": st.st_mode,
        "mtime": int(st.st_mtime),
    }

    if target.is_symlink():
        return {**base, "kind": "symlink", "link_target": os.readlink(target)}
    if target.is_dir():
        # We don't snapshot directory contents — that's a
        # delete_directory capability concern (Wave 4 #2). For now,
        # reject directory-targeted undoable ops at the capability
        # layer; this function returns the marker so the caller knows
        # to refuse.
        return {**base, "kind": "directory"}

    # Regular file
    if st.st_size > MAX_ORIGINAL_STATE_BYTES:
        return {
            **base, "kind": "file",
            "too_big": True,
            "content_b64": None,
        }

    with open(target, "rb") as f:
        content = f.read()
    return {
        **base, "kind": "file",
        "content_b64": base64.b64encode(content).decode("ascii"),
    }


def append_record(
    *,
    capability_id: str,
    action: str,
    target: str,
    original_state: dict[str, Any] | None,
    extra: dict[str, Any] | None = None,
    journal_path: str | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    action_id: str | None = None,
) -> str:
    """Append a journal record for a destructive action about to be (or
    just) performed. Returns the record id.

    Called by destructive capability handlers BEFORE the actual work
    so a mid-flight crash leaves enough breadcrumbs to recover. The
    handler is responsible for calling this at the right moment (after
    the dry-run plan is computed, before the irreversible step).
    """
    record_id = action_id or uuid.uuid4().hex
    record: dict[str, Any] = {
        "id": record_id,
        "capability_id": capability_id,
        "action": action,
        "target": target,
        "original_state": original_state,
        "applied_at": _now_iso(),
        "expires_at": _expires_iso(retention_days),
        "undone": False,
    }
    if extra:
        record["extra"] = extra

    path = _journal_path(journal_path)
    _ensure_journal_dir(path)
    line = json.dumps(record, default=str) + "\n"
    with _journal_lock:
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
    return record_id


def read_records(
    journal_path: str | None = None,
    *,
    include_undone: bool = False,
) -> list[dict[str, Any]]:
    """Read all journal records, oldest first."""
    path = _journal_path(journal_path)
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Skipping malformed journal line: %s", line[:80])
                continue
            if not include_undone and rec.get("undone"):
                continue
            out.append(rec)
    return out


def find_record(
    record_id: str,
    journal_path: str | None = None,
) -> dict[str, Any] | None:
    """Find a specific record by id. None if not found or already undone."""
    for rec in read_records(journal_path, include_undone=True):
        if rec["id"] == record_id:
            return rec
    return None


def latest_undoable_record(
    journal_path: str | None = None,
) -> dict[str, Any] | None:
    """Return the most recent record that hasn't been undone yet.

    Used by fs.undo_last_action when no specific id is given — the
    user just says "undo the last thing."
    """
    records = read_records(journal_path, include_undone=False)
    return records[-1] if records else None


def mark_undone(
    record_id: str,
    journal_path: str | None = None,
) -> bool:
    """Mark a record as undone. Returns True if found + marked.

    Doesn't delete the record — keeps the audit trail intact. The
    sweeper (future) will GC truly old undone records.
    """
    path = _journal_path(journal_path)
    if not path.exists():
        return False
    found = False
    with _journal_lock:
        records = []
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec["id"] == record_id and not rec.get("undone"):
                    rec["undone"] = True
                    rec["undone_at"] = _now_iso()
                    found = True
                records.append(rec)
        if found:
            with open(path, "w", encoding="utf-8") as f:
                for rec in records:
                    f.write(json.dumps(rec, default=str) + "\n")
                f.flush()
                os.fsync(f.fileno())
    return found


def restore_from_record(record: dict[str, Any]) -> dict[str, Any]:
    """Reverse the action recorded in ``record``.

    Returns a dict describing what was restored. Raises on failure
    (the caller is the undo capability handler and surfaces the error
    to the LLM via the typed-error classifier).
    """
    action = record["action"]
    target = Path(record["target"])
    state = record.get("original_state")

    if action == "delete":
        if state is None:
            raise FileNotFoundError(
                f"cannot undo delete of {target}: no original_state recorded"
            )
        if state.get("too_big"):
            raise ValueError(
                f"cannot undo delete of {target}: original was too big "
                f"({state['size']} bytes > {MAX_ORIGINAL_STATE_BYTES})"
            )
        kind = state.get("kind")
        if kind == "symlink":
            link_target = state.get("link_target")
            if link_target is None:
                raise ValueError(f"cannot undo delete of symlink {target}: no link_target")
            target.symlink_to(link_target)
            return {"restored": str(target), "kind": "symlink"}
        if kind == "file":
            content_b64 = state.get("content_b64")
            if content_b64 is None:
                raise ValueError(f"cannot undo delete of {target}: no content recorded")
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "wb") as f:
                f.write(base64.b64decode(content_b64))
            try:
                os.chmod(target, state["mode"] & 0o777)
            except OSError:
                pass
            return {"restored": str(target), "kind": "file", "size": len(base64.b64decode(content_b64))}
        raise ValueError(f"cannot undo delete: unknown original kind {kind!r}")

    if action == "overwrite":
        # Same restoration logic as delete — write the original bytes
        # back, replacing the new file.
        if state is None or state.get("kind") != "file":
            raise ValueError(f"cannot undo overwrite of {target}: bad state")
        if state.get("too_big") or state.get("content_b64") is None:
            raise ValueError(f"cannot undo overwrite of {target}: original not preserved")
        with open(target, "wb") as f:
            f.write(base64.b64decode(state["content_b64"]))
        try:
            os.chmod(target, state["mode"] & 0o777)
        except OSError:
            pass
        return {"restored": str(target), "kind": "file", "action": "overwrite"}

    if action == "move":
        original_source = record.get("extra", {}).get("source")
        if not original_source:
            raise ValueError(f"cannot undo move to {target}: no source recorded")
        src = Path(original_source)
        if src.exists():
            raise FileExistsError(
                f"cannot undo move: original source {src} now exists "
                f"(was something written there since the move?)"
            )
        target.rename(src)
        return {"restored": str(src), "from": str(target), "action": "move"}

    raise ValueError(f"cannot undo: unknown action {action!r}")
