"""Restore the agent's memory from a Windy Cloud backup, safely (strand P4.11.16; Hub 10-17).

The old ``/backup restore`` wrote the backup over the memory file while the agent held it open in WAL
mode, with no question first, and left a plaintext copy beside it
(specs/AGENT_RESTORE_REVIEW_P4_11_16_2026-10-17.md). This replaces it:

1. **Check, then ask** (``prepare``):
   - Requires the owner-held ``WINDY_BACKUP_KEY``. The passport-derived key is never tried, and a missing
     key is said plainly.
   - The download is streamed with a running byte cap and never follows a redirect.
   - It is decrypted, then decompressed with a cap.
   - It is loaded as a database **in this process's memory only** (``sqlite3.deserialize``): never a
     plaintext file on disk.
   - It must pass ``PRAGMA integrity_check``, and its schema version must not be newer than this code's.
   - The owner then sees the backup's date and size, what the memory holds now, and that items deleted
     since the backup come back.
2. **The yes** (``apply``), within 10 minutes:
   - First an **encrypted** snapshot of the current memory is taken (owner key, AES-GCM, 0600, beside
     the database), for ``/backup undo`` within 24 hours. It is never a plaintext copy.
   - Then the backup is copied **into the live connection** with SQLite's online backup API, under the
     database lock, so the agent's open connection and its WAL stay consistent.
   - Then migrations run (an older backup is brought up to this schema) and ``wal_checkpoint(TRUNCATE)``.
3. ``/backup undo`` puts the snapshot back the same way, after its own yes. The snapshot is wiped after
   use, or once it is 24 hours old.

Logs carry sizes, counts and dates only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 1024 * 1024 * 1024          # an encrypted, gzipped backup (Windy 0's: ~30 MB)
MAX_RESTORED_BYTES = 4 * 1024 * 1024 * 1024      # the database once decompressed
QUESTION_FOR_S = 10 * 60
UNDO_FOR_S = 24 * 60 * 60
_SQLITE_MAGIC = b"SQLite format 3\x00"
_UNDO_SUFFIX = ".undo.enc"
# A Windy Fly memory has these tables; a backup without them is something else (Hub on #595).
REQUIRED_TABLES = ("schema_version", "nodes", "episodes", "soul")

NO_OWNER_KEY = ("This agent has no backup key of its own (WINDY_BACKUP_KEY), so I can't open a backup. "
                "Set WINDY_BACKUP_KEY to the key the backups were made with, then try again. Nothing was changed.")


class RestoreError(Exception):
    """A refusal with a message safe to show the owner. Nothing was changed."""


@dataclass
class _Question:
    """What a yes will do. It holds no backup data (Hub, 10-17): the yes downloads (or re-reads the undo
    copy) and checks again, and restores only if the bytes hash to what the owner was shown."""
    kind: str                        # "restore" or "undo"
    name: str                        # the backup's filename ("" for undo)
    digest: str                      # sha256 of the encrypted bytes the owner was asked about
    label: str
    asked: float = field(default_factory=time.time)


_questions: dict[str, _Question] = {}
_lock = threading.Lock()


# ── checks ────────────────────────────────────────────────────────────────────────────────────

def _owner_key() -> bytes:
    from windyfly.cloud_backup import _get_encryption_key, owner_key_missing

    if owner_key_missing():
        raise RestoreError(NO_OWNER_KEY)
    return _get_encryption_key()          # with WINDY_BACKUP_KEY set this is the owner key, never derived


def _gunzip_capped(data: bytes) -> bytes:
    if data[:2] != b"\x1f\x8b":
        if len(data) > MAX_RESTORED_BYTES:
            raise RestoreError("That backup is too large to restore here. Nothing was changed.")
        return data
    d = zlib.decompressobj(wbits=31)
    out = bytearray()
    chunk = d.decompress(data, 64 * 1024 * 1024)
    while True:
        out += chunk
        if len(out) > MAX_RESTORED_BYTES:
            raise RestoreError("That backup unpacks to more than I can restore here. Nothing was changed.")
        if not d.unconsumed_tail:
            break
        chunk = d.decompress(d.unconsumed_tail, 64 * 1024 * 1024)
    out += d.flush()
    return bytes(out)


def code_schema_version() -> int:
    from windyfly.memory.database import _MIGRATIONS

    return max(_MIGRATIONS.keys(), default=0)


def _loadable(data: bytes) -> bytes:
    """A WAL-mode file says so in header bytes 18-19 (value 2), and SQLite will not open such bytes from
    memory ("unable to open database file"). Mark the in-memory copy as rollback-journal (1); the live
    database keeps its own WAL mode, which the online backup copy does not change."""
    if data[18:20] == b"\x02\x02":
        b = bytearray(data)
        b[18:20] = b"\x01\x01"
        return bytes(b)
    return data


def check_database_bytes(data: bytes) -> dict[str, Any]:
    """Open ``data`` as a database in memory and check it. Returns facts about it; raises RestoreError."""
    if not data.startswith(_SQLITE_MAGIC):
        raise RestoreError("That backup is not a memory database. Nothing was changed.")
    conn = sqlite3.connect(":memory:")
    try:
        try:
            conn.deserialize(_loadable(data))
            ok = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as e:
            raise RestoreError("That backup is damaged and can't be opened. Nothing was changed.") from e
        if not ok or ok[0] != "ok":
            raise RestoreError("That backup failed its integrity check. Nothing was changed.")
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = [t for t in REQUIRED_TABLES if t not in names]
        if missing:
            raise RestoreError("That backup is not a Windy Fly memory (it is missing "
                               f"{len(missing)} of its core tables). Nothing was changed.")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            raise RestoreError("That backup failed its foreign-key check, so I won't restore it (there is no "
                               "override; an older backup may restore cleanly). Nothing was changed.")
        try:
            version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
        except sqlite3.DatabaseError:
            version = None
        if not isinstance(version, int) or version < 1:     # four empty tables of the right names are not one
            raise RestoreError("That backup is not a Windy Fly memory (it has no schema version). "
                               "Nothing was changed.")
        if version > code_schema_version():
            raise RestoreError("That backup was made by a newer version of the agent. Update the agent, "
                               "then restore. Nothing was changed.")
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return {"schema_version": version, "page_size": page_size, **_memory_facts(conn)}
    finally:
        conn.close()


def _memory_facts(conn: sqlite3.Connection) -> dict[str, Any]:
    facts: dict[str, Any] = {}
    for table, key in (("episodes", "messages"), ("nodes", "memories")):
        try:
            facts[key] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.DatabaseError:
            facts[key] = 0
    try:
        facts["newest"] = str(conn.execute("SELECT MAX(created_at) FROM episodes").fetchone()[0] or "")[:16]
    except sqlite3.DatabaseError:
        facts["newest"] = ""
    return facts


# ── download ──────────────────────────────────────────────────────────────────────────────────

_PLAIN = {
    401: "Windy Cloud refused this agent's credentials.",
    403: "Windy Cloud refused: this account is frozen or suspended, or the credentials are not allowed.",
    404: "There is no backup by that name. /backup list shows them.",
    410: "That backup is in the Windy Cloud trash.",
    423: "Windy Cloud has locked this agent's storage (agent_locked).",
    429: "Windy Cloud's monthly allowance for archive reads is used up.",
}


async def _download(url: str, auth_header: dict) -> bytes:
    from windyfly.agent import cloud_auth
    from windyfly.cloud_backup import _TIMEOUT

    agent = await asyncio.to_thread(cloud_auth.agent_headers, "GET", url)
    tries = [h for h in (agent, auth_header) if h]
    if not tries:
        raise RestoreError("No Windy Cloud credential is configured. Nothing was changed.")
    async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
        for i, headers in enumerate(tries):
            async with client.stream("GET", url, headers=headers) as resp:
                if i + 1 < len(tries) and agent and cloud_auth.refused(resp.status_code):
                    cloud_auth.fallback_used("backup restore", resp.status_code)
                    continue
                if resp.status_code != 200:
                    raise RestoreError(_PLAIN.get(resp.status_code, f"Windy Cloud answered {resp.status_code}.")
                                       + " Nothing was changed.")
                declared = int(resp.headers.get("content-length") or 0)
                if declared > MAX_DOWNLOAD_BYTES:
                    raise RestoreError("That backup is too large to download here. Nothing was changed.")
                buf = bytearray()
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_DOWNLOAD_BYTES:
                        raise RestoreError("That backup is too large to download here. Nothing was changed.")
                return bytes(buf)
    raise RestoreError("Windy Cloud refused this agent's credentials. Nothing was changed.")


async def _fetch(name: str, config: dict | None) -> tuple[bytes, dict[str, Any]]:
    from windyfly.auth.bot_credentials import ecosystem_auth_header
    from windyfly.cloud_backup import _get_cloud_token, _get_cloud_url, list_backups

    listed = await list_backups(config)
    if not listed.get("success"):
        raise RestoreError(f"I couldn't list the backups ({listed.get('error') or 'no answer'}). Nothing was changed.")
    backups = listed.get("backups") or []
    if not backups:
        raise RestoreError("There are no backups on Windy Cloud yet. Nothing was changed.")
    pick = backups[0] if name in ("", "latest") else next((b for b in backups if b.get("filename") == name), None)
    if pick is None:
        raise RestoreError(f"There is no backup called {name}. /backup list shows them. Nothing was changed.")
    filename = str(pick.get("filename") or "")
    if not filename or "/" in filename or filename.startswith("."):
        raise RestoreError("Windy Cloud listed a backup with an unusable name. Nothing was changed.")
    url = f"{_get_cloud_url(config)}/api/v1/archive/retrieve/windy_fly/{filename}"
    auth = await ecosystem_auth_header(fallback_token=_get_cloud_token())
    return await _download(url, auth), pick


# ── the question and the yes ───────────────────────────────────────────────────────────────────

def _guest() -> bool:
    from windyfly.agent.guest_mode import is_guest_active

    return is_guest_active()


def _live_facts(db: Any) -> dict[str, Any]:
    with db._lock:
        facts = _memory_facts(db.conn)
    try:
        facts["bytes"] = Path(db.db_path).stat().st_size
    except OSError:
        facts["bytes"] = 0
    return facts


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.1f} MB"


def _decrypt_checked(blob: bytes, owner_key: bytes, db: Any) -> tuple[bytes, dict[str, Any]]:
    from windyfly.cloud_backup import _decrypt_data

    try:
        data = _gunzip_capped(_decrypt_data(blob, owner_key))
    except RestoreError:
        raise
    except Exception as e:
        raise RestoreError("That backup can't be opened with this agent's WINDY_BACKUP_KEY: it was made with "
                           "a different key, or it is damaged. Nothing was changed.") from e
    facts = check_database_bytes(data)
    with db._lock:
        live_page = db.conn.execute("PRAGMA page_size").fetchone()[0]
    if facts["page_size"] != live_page:
        # SQLite's online backup cannot copy into a WAL database with a different page size (Hub, 10-17).
        raise RestoreError("That backup was made with a different database page size, so it can't be restored "
                           "into the running agent. Nothing was changed.")
    return data, facts


def _digest(blob: bytes) -> str:
    import hashlib

    return hashlib.sha256(blob).hexdigest()


async def ask_restore(db: Any, key: str, name: str = "latest", config: dict | None = None) -> str:
    """/backup restore [name]: fetch, decrypt and check the backup, then ask. Changes nothing."""
    if _guest():
        return "Restoring a backup is off while guest mode is on."
    try:
        owner_key = _owner_key()
        blob, meta = await _fetch(name, config)
        data, facts = _decrypt_checked(blob, owner_key, db)
    except RestoreError as e:
        return str(e)
    now = _live_facts(db)
    when = str(meta.get("created_at") or "")[:16].replace("T", " ")
    filename = str(meta.get("filename"))
    with _lock:
        _questions[key] = _Question("restore", filename, _digest(blob), f"{filename} ({when} UTC)")
    logger.info("backup restore: asked (backup %d bytes, schema %s)", len(data), facts["schema_version"])
    size = len(data)
    del data, blob                      # nothing decrypted is kept while the owner decides
    return (f"Restore my memory from the backup of {when} UTC ({_mb(size)}: {facts['messages']} messages, "
            f"{facts['memories']} memories, newest message {facts['newest'] or 'none'})?\n"
            f"It replaces what I remember now ({_mb(now['bytes'])}: {now['messages']} messages, "
            f"{now['memories']} memories, newest message {now['newest'] or 'none'}). Everything since that "
            "backup is lost, and anything you deleted since then (/forget, /history, /soul drop) comes back.\n"
            "/backup yes restores it. I keep an encrypted copy of my memory as it is now, so /backup undo can "
            "put it back within 24 hours. /backup cancels.")


def cancel(key: str) -> bool:
    with _lock:
        return _questions.pop(key, None) is not None


def _undo_path(db: Any) -> Path:
    """``db`` is a Database or a database path (the startup sweep has only the path)."""
    return Path(str(getattr(db, "db_path", db)) + _UNDO_SUFFIX)


def _serialize(db: Any) -> bytes:
    mem = sqlite3.connect(":memory:")
    try:
        with db._lock:
            db.conn.commit()
            db.conn.backup(mem)
        return mem.serialize()
    finally:
        mem.close()


def _write_undo(db: Any, owner_key: bytes) -> None:
    from windyfly.cloud_backup import _encrypt_data

    blob = _encrypt_data(_serialize(db), owner_key)
    path = _undo_path(db)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(json.dumps({"at": time.time()}).encode() + b"\n" + blob)
    os.replace(tmp, path)


class _CopyFailed(Exception):
    """The online backup copy failed; SQLite rolled the live database back, so nothing changed."""


class _MigrateFailed(Exception):
    """The copy landed but bringing it up to this schema failed; /backup undo is the way back."""


def _copy_pages(src: sqlite3.Connection, dst: sqlite3.Connection) -> None:
    src.backup(dst)                      # one step: SQLite applies all of it or none of it


def _restore_into(db: Any, data: bytes) -> str | None:
    """Copy ``data`` (a checked database) into the live connection, under the database lock. Returns None
    when the restored memory passes its checks, else which check failed ("integrity" or "foreign-key")."""
    src = sqlite3.connect(":memory:")
    try:
        src.deserialize(_loadable(data))        # the header edit is on an in-memory copy only
        with db._lock:
            db.conn.commit()
            try:
                _copy_pages(src, db.conn)
            except sqlite3.Error as e:
                raise _CopyFailed(type(e).__name__) from e
            try:
                db._run_migrations()           # an older backup is brought up to this code's schema
            except Exception as e:
                raise _MigrateFailed(type(e).__name__) from e
            db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            ok = db.conn.execute("PRAGMA integrity_check").fetchone()
            fk = db.conn.execute("PRAGMA foreign_key_check").fetchall()
        failed = "integrity" if not ok or ok[0] != "ok" else ("foreign-key" if fk else None)
        if failed:
            logger.error("backup restore: check after restore failed (integrity=%s, fk=%d)",
                         ok[0] if ok else None, len(fk))
        return failed
    finally:
        src.close()


def _apply(db: Any, data: bytes, owner_key: bytes, label: str) -> str:
    try:
        _write_undo(db, owner_key)
    except Exception as e:
        logger.error("backup restore: undo copy failed (%s); nothing restored", type(e).__name__)
        return "I couldn't keep a copy of my memory as it is now, so I restored nothing."
    try:
        failed = _restore_into(db, data)
    except _CopyFailed as e:
        _wipe(_undo_path(db))                    # memory is unchanged: the undo copy has nothing to undo
        logger.error("backup restore: copy failed (%s); nothing changed", e)
        return "The restore failed and nothing was changed. My memory is as it was."
    except _MigrateFailed as e:
        logger.error("backup restore: restored but migration failed (%s)", e)
        return (f"I restored {label}, but couldn't bring it up to date with this version of me. "
                "/backup undo puts back my memory as it was before, within 24 hours.")
    # Wiped at 24 hours even if nobody asks (the startup sweep covers a restart in between).
    timer = threading.Timer(UNDO_FOR_S + 5, sweep_undo, args=(db.db_path,))
    timer.daemon = True
    timer.start()
    logger.info("backup restore: restored (%d bytes)", len(data))
    if failed:
        return (f"I restored {label}, but my memory failed its {failed} check afterwards. /backup undo puts "
                "back what I remembered before, within 24 hours.")
    return f"Restored from {label}. /backup undo puts back what I remembered before, within 24 hours."


def _read_undo(db: Any) -> tuple[float, bytes]:
    head, _, blob = _undo_path(db).read_bytes().partition(b"\n")
    return float(json.loads(head).get("at", 0)), blob


_restoring = threading.Lock()      # one restore or undo at a time (Hub on #595)


async def confirm(db: Any, key: str, config: dict | None = None) -> str:
    """/backup yes: do what the last question asked, after fetching and checking the bytes again.

    The lock is taken here and handed to the thread that changes the memory, which releases it when it is
    done. If this task is cancelled while that thread runs (Hub on #597), the lock stays held until the
    thread really ends, so a fresh ask and yes can't start a second restore beside it."""
    if _guest():
        return "Restoring a backup is off while guest mode is on."
    if not _restoring.acquire(blocking=False):
        return "A restore is already running. Wait for it to finish; this yes did nothing."
    handed = False
    try:
        job = await _prepare(db, key, config)
        if isinstance(job, str):
            return job
        fut = asyncio.get_running_loop().run_in_executor(None, _run_locked, job)
        handed = True
        return await fut
    finally:
        if not handed:
            _restoring.release()


def _run_locked(job: tuple[str, Any, bytes, bytes, str]) -> str:
    """The memory-changing half of /backup yes, on a worker thread that owns the restore lock."""
    try:
        kind, db, data, owner_key, label = job
        if kind == "restore":
            return _apply(db, data, owner_key, label)
        return _undo_apply(db, data)
    finally:
        _restoring.release()


async def _prepare(db: Any, key: str, config: dict | None) -> str | tuple[str, Any, bytes, bytes, str]:
    """Fetch and check again what the question named. A str is the answer (nothing to do)."""
    with _lock:
        q = _questions.pop(key, None)
    if q is None or time.time() - q.asked > QUESTION_FOR_S:
        return "Nothing is waiting for a yes. /backup restore starts again."
    try:
        owner_key = _owner_key()
        if q.kind == "restore":
            blob, _meta = await _fetch(q.name, config)
        else:
            _at, blob = await asyncio.to_thread(_read_undo, db)
        if _digest(blob) != q.digest:
            raise RestoreError("That backup changed since you were asked, so I restored nothing. "
                               "Ask again to see what it holds now.")
        if q.kind == "restore":
            data, _facts = _decrypt_checked(blob, owner_key, db)
        else:
            from windyfly.cloud_backup import _decrypt_data

            data = _decrypt_data(blob, owner_key)
            check_database_bytes(data)
    except RestoreError as e:
        return str(e)
    except (OSError, ValueError):
        return "The undo copy is unreadable. Nothing was changed."
    return (q.kind, db, data, owner_key, q.label)


def _undo_apply(db: Any, data: bytes) -> str:
    try:
        failed = _restore_into(db, data)
    except _CopyFailed:
        return "Putting back the earlier memory failed and nothing was changed. The undo copy is kept."
    except _MigrateFailed:
        return "I put back the earlier memory, but couldn't bring it up to date with this version of me."
    if failed:
        return (f"I put back the earlier memory, but it failed its {failed} check afterwards. The undo copy "
                "is kept, so /backup undo can try again.")
    _wipe(_undo_path(db))
    logger.info("backup restore: undone (%d bytes)", len(data))
    return "Put back my memory as it was before the restore. The undo copy is now deleted."


def ask_undo(db: Any, key: str) -> str:
    """/backup undo: open the encrypted snapshot, check it, and ask."""
    if _guest():
        return "Restoring a backup is off while guest mode is on."
    path = _undo_path(db)
    sweep_undo(db)
    if not path.is_file():
        return "There is nothing to undo: no restore in the last 24 hours."
    try:
        owner_key = _owner_key()
        at, blob = _read_undo(db)
        from windyfly.cloud_backup import _decrypt_data

        try:
            data = _decrypt_data(blob, owner_key)
        except Exception as e:
            raise RestoreError("The undo copy can't be opened with this agent's WINDY_BACKUP_KEY. "
                               "Nothing was changed.") from e
        facts = check_database_bytes(data)
        del data
    except RestoreError as e:
        return str(e)
    except (OSError, ValueError):
        return "The undo copy is unreadable. Nothing was changed."
    when = datetime.fromtimestamp(at, timezone.utc).strftime("%Y-%m-%d %H:%M")
    with _lock:
        _questions[key] = _Question("undo", "", _digest(blob), f"my memory as of {when} UTC")
    return (f"Put back my memory as it was before the restore ({when} UTC: {facts['messages']} messages, "
            f"{facts['memories']} memories)? Anything since the restore is lost. /backup yes does it; "
            "/backup cancels.")


def _wipe(path: Path) -> None:
    try:
        size = path.stat().st_size
        with open(path, "r+b") as f:
            f.write(b"\0" * size)
            f.flush()
            os.fsync(f.fileno())
        path.unlink()
    except OSError:
        pass


def sweep_undo(db: Any) -> bool:
    """Wipe the undo copy once it is 24 hours old. True when one was removed."""
    path = _undo_path(db)
    try:
        head = path.open("rb").readline()
        at = float(json.loads(head).get("at", 0))
    except (OSError, ValueError):
        return False
    if time.time() - at > UNDO_FOR_S:
        _wipe(path)
        logger.info("backup restore: undo copy expired and wiped")
        return True
    return False
