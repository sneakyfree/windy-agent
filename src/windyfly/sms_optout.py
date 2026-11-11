"""The do-not-text record, kept as fingerprints (LEGAL DECISION 28 (8), (9), (10); Windy Legal, BOARD 2026-11-09).

A person who texts STOP is never texted again until they text START. To keep that promise the agent has to remember
who they are, but it does not have to keep their number. So ``sms_optouts`` holds a keyed fingerprint
(HMAC-SHA256, hex) of the normalised number, and the 32-byte key lives in its own 0600 file beside the memory
database, never in the database.

Rules, each pinned by a test:

- Plain rows written by 0.9.x are converted in ONE transaction the first time the store opens with a key, and the
  plain values are gone after it.
- A fresh key is made only when the table holds no fingerprints. Fingerprints with no key cannot be matched, so the
  store FAILS CLOSED: every number but the owner's is refused. There is no override that empties the table.
- While failed closed, a STOP is still kept: it goes to a 0600 pending file (normalised number + time), and is
  fingerprinted and removed from that file as soon as the key is back.
- An opt-out row never expires.
- The table also holds ONE check row made from the key ("k:" + a fingerprint of a fixed word). A key file that does
  not produce that row is the wrong key (a restored older memory, a copied file): it counts as missing, so a wrong
  key can never make the record silently match nobody.

This module has no channel in it on purpose: the record stays in front of every texting path whatever becomes of
the direct Twilio channel.
"""

from __future__ import annotations

import datetime
import hashlib
import hmac
import json
import logging
import os
import secrets
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KEY_FILE = "sms-optout.key"
PENDING_FILE = "sms-optout-pending.jsonl"
KEY_BYTES = 32

# What a caller learns about one number.
CLEAR = "clear"
OPTED_OUT = "opted_out"
KEY_MISSING = "key_missing"

_SQL = """
CREATE TABLE IF NOT EXISTS sms_optouts (
    number TEXT PRIMARY KEY,
    opted_out_at TEXT NOT NULL
);
"""

_CHECK_PREFIX = "k:"
_warned: set[str] = set()


def number_key(number: str) -> str:
    """One key per phone number, whatever its spelling: digits only, and a 10-digit North American number gets its
    country code, so "+1 (555) 123-4567", "15551234567" and "5551234567" are the same person."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    return "1" + digits if len(digits) == 10 else digits


def fingerprint(key: bytes, number: str) -> str:
    return hmac.new(key, number_key(number).encode(), hashlib.sha256).hexdigest()


def key_check(key: bytes) -> str:
    """The check row for ``key``: proves a key file belongs to this table without saying anything about a number."""
    return _CHECK_PREFIX + hmac.new(key, b"windyfly sms-optout key check", hashlib.sha256).hexdigest()


def is_check(value: str) -> bool:
    return value.startswith(_CHECK_PREFIX)


def key_fits(key: bytes | None, numbers: list[str]) -> bool:
    """True when ``key`` is the key these rows were made with: no check row yet, or its own check row."""
    if key is None:
        return False
    checks = [n for n in numbers if is_check(n)]
    return not checks or key_check(key) in checks


def is_fingerprint(value: str) -> bool:
    """A stored fingerprint is 64 hex characters. A plain row from 0.9.x is digits only and at most 15 long, so the
    two can never be confused."""
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def mask(number: str) -> str:
    """What an activity-log line may hold of a number: its last four digits (Privacy 4e: "+1 •••-•••-0101")."""
    digits = number_key(number)
    last = digits[-4:] if len(digits) >= 4 else "••••"
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 •••-•••-{last}"
    return f"•••-{last}"


def in_memory(db_path: str | os.PathLike) -> bool:
    """A database with no file (":memory:") has no folder to keep a key in: its key lives with the connection."""
    p = str(db_path)
    return p == "" or p.startswith(":") or p.startswith("file::memory:")


def key_path(db_path: str | os.PathLike) -> Path:
    return Path(str(db_path)).parent / KEY_FILE


def pending_path(db_path: str | os.PathLike) -> Path:
    return Path(str(db_path)).parent / PENDING_FILE


def load_key(db_path: str | os.PathLike) -> bytes | None:
    """The install's key, or None when there is none. A file of the wrong size is not a key."""
    if in_memory(db_path):
        return None
    path = key_path(db_path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        data = f.read(KEY_BYTES + 1)
        if os.fstat(f.fileno()).st_mode & 0o077:
            try:
                os.fchmod(f.fileno(), 0o600)
            except OSError:
                pass
    return data if len(data) == KEY_BYTES else None


def write_key(db_path: str | os.PathLike, key: bytes) -> None:
    """Put ``key`` in place (0600, never through a link), replacing whatever is there. Used for a fresh key and by a
    backup restore, which brings back the key the restored fingerprints were made with."""
    if len(key) != KEY_BYTES:
        raise ValueError("an opt-out key is 32 bytes")
    if in_memory(db_path):
        raise ValueError("a database with no file has no place for a key file")
    path = key_path(db_path)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _read_pending(db_path: str | os.PathLike) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if in_memory(db_path):
        return out
    path = pending_path(db_path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return out
    with os.fdopen(fd, "r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("number"):
                out.append({"number": str(row["number"]), "at": str(row.get("at") or _now())})
    return out


def _write_pending(db_path: str | os.PathLike, rows: list[dict[str, str]]) -> None:
    path = pending_path(db_path)
    if not rows:
        try:
            os.unlink(path)
        except FileNotFoundError:
            pass
        return
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class OptOutStore:
    """The record, on the agent's live database. Cheap to build: the channel makes one per request."""

    def __init__(self, db: Any) -> None:
        self.db = db
        self.db_path = str(db.db_path)
        self._key: bytes | None = None
        self.db.execute(_SQL)
        self.db.commit()
        self._open()

    # ── key and start-up ──

    def _rows(self) -> list[tuple[str, str]]:
        return [(r["number"], r["opted_out_at"]) for r in self.db.fetchall("SELECT number, opted_out_at FROM sms_optouts")]

    def _open(self) -> None:
        """Find the key, or make one if that is safe; then convert plain rows and take in pending STOPs."""
        numbers = [n for n, _ in self._rows()]
        if in_memory(self.db_path):                      # no file, no folder: the key lives and dies with the connection
            key = getattr(self.db, "_sms_optout_key", None) or secrets.token_bytes(KEY_BYTES)
            self.db._sms_optout_key = key
            self._key = key
            self._convert_plain_rows()
            return
        key = load_key(self.db_path)
        has_fingerprints = any(is_fingerprint(n) for n in numbers)
        if key is not None and not key_fits(key, numbers):
            key = None if has_fingerprints else key      # the wrong key for these fingerprints: as good as missing
        if key is None:
            if has_fingerprints:
                self._key = None            # fingerprints nobody can match: fail closed
                if "lost" not in _warned:
                    _warned.add("lost")
                    logger.error("SMS do-not-text key is missing while the record has entries: texting is limited "
                                 "to the owner until the key is restored (a backup restore brings it back)")
                return
            key = secrets.token_bytes(KEY_BYTES)
            write_key(self.db_path, key)
        self._key = key
        _warned.discard("lost")
        if key_check(key) not in numbers:               # first use of this key on this table (no fingerprints yet)
            self.db.execute("DELETE FROM sms_optouts WHERE number LIKE 'k:%'")
            self.db.execute("INSERT INTO sms_optouts (number, opted_out_at) VALUES (?, ?)", (key_check(key), _now()))
            self.db.commit()
        self._convert_plain_rows()
        self._take_in_pending()

    def _convert_plain_rows(self) -> None:
        plain = [(n, at) for n, at in self._rows() if not is_fingerprint(n) and not is_check(n)]
        if not plain or self._key is None:
            return
        with self.db._lock:
            conn = self.db.conn
            conn.commit()
            try:
                conn.execute("BEGIN IMMEDIATE")
                for n, at in plain:
                    conn.execute("INSERT OR IGNORE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                                 (fingerprint(self._key, n), at))
                    conn.execute("DELETE FROM sms_optouts WHERE number = ?", (n,))
                conn.commit()
            except sqlite3.Error:
                conn.rollback()
                raise
        logger.info("SMS do-not-text record: %d plain number(s) replaced by fingerprints", len(plain))

    def _take_in_pending(self) -> None:
        rows = _read_pending(self.db_path)
        if not rows or self._key is None:
            return
        for row in rows:
            self.db.execute("INSERT OR IGNORE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                            (fingerprint(self._key, row["number"]), row["at"]))
        self.db.commit()
        _write_pending(self.db_path, [])     # only after the commit: a crash in between keeps the file
        logger.info("SMS do-not-text record: %d STOP(s) kept while the key was missing are now recorded", len(rows))

    @property
    def failed_closed(self) -> bool:
        """True while the record holds fingerprints and there is no key. Checked again each time, so putting the key
        file back (a restore does it) ends it without a restart."""
        if self._key is None:
            self._open()
        return self._key is None

    # ── the record ──

    def record(self, number: str) -> None:
        if self.failed_closed:
            rows = _read_pending(self.db_path)
            nk = number_key(number)
            if nk and all(r["number"] != nk for r in rows):
                rows.append({"number": nk, "at": _now()})
                _write_pending(self.db_path, rows)
            return
        assert self._key is not None
        self.db.execute("INSERT OR REPLACE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                        (fingerprint(self._key, number), _now()))
        self.db.commit()

    def clear(self, number: str) -> None:
        """START. Without the key a fingerprint row cannot be found, so only a pending STOP is dropped; the person
        stays opted out (the safe side) and START works again once the key is back."""
        nk = number_key(number)
        rows = _read_pending(self.db_path)
        kept = [r for r in rows if r["number"] != nk]
        if len(kept) != len(rows):
            _write_pending(self.db_path, kept)
        if self.failed_closed:
            return
        assert self._key is not None
        self.db.execute("DELETE FROM sms_optouts WHERE number IN (?, ?)", (fingerprint(self._key, number), nk))
        self.db.commit()

    def state(self, number: str) -> str:
        """CLEAR, OPTED_OUT, or KEY_MISSING (failed closed and this number cannot be checked)."""
        nk = number_key(number)
        if any(r["number"] == nk for r in _read_pending(self.db_path)):
            return OPTED_OUT
        if self.failed_closed:
            return KEY_MISSING
        assert self._key is not None
        hit = self.db.fetchone("SELECT 1 AS x FROM sms_optouts WHERE number IN (?, ?)",
                               (fingerprint(self._key, number), nk))
        return OPTED_OUT if hit is not None else CLEAR


def state_readonly(number: str, db_path: str) -> str:
    """The same answer for a send path with no channel object (the /sms command), from a read-only connection.
    No database or no table yet = nothing recorded = CLEAR. A read error = OPTED_OUT: when in doubt, do not text."""
    nk = number_key(number)
    if any(r["number"] == nk for r in _read_pending(db_path)):
        return OPTED_OUT
    if not os.path.isfile(db_path):
        return CLEAR
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
        try:
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sms_optouts'").fetchone() is None:
                return CLEAR
            rows = [r[0] for r in con.execute("SELECT number FROM sms_optouts")]
        finally:
            con.close()
    except sqlite3.Error:
        return OPTED_OUT
    if nk in rows:                           # a plain row not converted yet still counts
        return OPTED_OUT
    if not any(is_fingerprint(n) for n in rows):
        return CLEAR
    key = load_key(db_path)
    if key is None or not key_fits(key, rows):
        return KEY_MISSING
    return OPTED_OUT if fingerprint(key, number) in rows else CLEAR


def mask_old_events(db: Any) -> int:
    """Activity-log lines written by 0.9.x hold whole numbers for up to 30 days. Rewrite them once to the last four
    digits. Returns how many lines changed."""
    changed = 0
    for row in db.fetchall("SELECT id, data FROM events WHERE event_type LIKE 'sms.%'"):
        try:
            data = json.loads(row["data"] or "{}")
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        new = dict(data)
        for field in ("phone", "from", "to"):
            v = new.get(field)
            if isinstance(v, str) and v and "•" not in v:
                new[field] = mask(v)
        if new != data:
            db.execute("UPDATE events SET data = ? WHERE id = ?", (json.dumps(new), row["id"]))
            changed += 1
    if changed:
        db.commit()
    return changed


# ── travelling inside the owner's encrypted backup (LEGAL DECISION 28 (9)(1)) ──

def backup_files(db_path: str | os.PathLike) -> dict[str, bytes]:
    """What goes into a backup beside the database, each as its own file: the key, and the pending STOPs if any."""
    out: dict[str, bytes] = {}
    key = load_key(db_path)
    if key is not None:
        out[KEY_FILE] = key
    rows = _read_pending(db_path)
    if rows:
        out[PENDING_FILE] = "".join(json.dumps(r) + "\n" for r in rows).encode()
    return out


def restore_files(db_path: str | os.PathLike, files: dict[str, bytes]) -> None:
    """Put a backup's key in place (the restored fingerprints were made with it) and add its pending STOPs to any
    kept here. A backup without a key leaves the key file alone; the check row then decides whether it fits."""
    key = files.get(KEY_FILE)
    if key is not None and len(key) == KEY_BYTES:
        write_key(db_path, key)
    blob = files.get(PENDING_FILE)
    if blob:
        rows = _read_pending(db_path)
        seen = {r["number"] for r in rows}
        for line in blob.decode("utf-8", "replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and row.get("number") and str(row["number"]) not in seen:
                seen.add(str(row["number"]))
                rows.append({"number": str(row["number"]), "at": str(row.get("at") or _now())})
        _write_pending(db_path, rows)
