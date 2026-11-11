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
- A backup restore, and its undo, never removes an opt-out. The live record's rows are put into the memory that
  replaces it, and the key they were made with is kept beside the restored one as an EARLIER key
  (``sms-optout-earlier-<id>.key``). A number is opted out if it matches under ANY key the record has a check row
  for; if a check row's key is not on this computer, some fingerprints cannot be matched, so it fails closed.
- A fresh key is made exclusively, and the look-then-make happens under the database's write lock, so two stores
  that start together (two processes after an upgrade) end with ONE key and every row under it.
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
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KEY_FILE = "sms-optout.key"
PENDING_FILE = "sms-optout-pending.jsonl"
_EARLIER_NAME = re.compile(r"^sms-optout-earlier-[0-9a-f]{16}\.key$")
KEY_BYTES = 32

# What a caller learns about one number.
CLEAR = "clear"
OPTED_OUT = "opted_out"
KEY_MISSING = "key_missing"

# Said to the owner when the fingerprint key is missing while the record has entries (LEGAL DECISION 28 (9)(2)).
# Windy Legal's words, word for word (BOARD 2026-11-11T03:24:49Z (a)): a backup made before 0.9.x carried no key.
# It lives here, with the record, so every texting path says the same sentence.
KEY_MISSING_NOTICE = ("Right now I can only text you. The key that protects my do-not-text list is missing from "
                      "this computer, so I can't tell who asked me to stop texting them. Restoring my latest backup "
                      "brings the key back, if that backup has it. Until then I won't text anyone else.")

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


def _read_key_file(path: Path) -> bytes | None:
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


def _write_key_file(path: Path, key: bytes) -> None:
    if len(key) != KEY_BYTES:
        raise ValueError("an opt-out key is 32 bytes")
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        os.fchmod(f.fileno(), 0o600)             # a leftover .tmp keeps its old mode otherwise (Hub on #777)
        f.write(key)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_key(db_path: str | os.PathLike) -> bytes | None:
    """The install's key IN USE (new opt-outs are fingerprinted with it), or None when there is none. A file of
    the wrong size is not a key."""
    if in_memory(db_path):
        return None
    return _read_key_file(key_path(db_path))


def write_key(db_path: str | os.PathLike, key: bytes) -> None:
    """Put ``key`` in place as the key in use (0600, never through a link), replacing whatever is there. Used by
    a backup restore, which brings back the key the restored fingerprints were made with."""
    if len(key) != KEY_BYTES:
        raise ValueError("an opt-out key is 32 bytes")
    if in_memory(db_path):
        raise ValueError("a database with no file has no place for a key file")
    _write_key_file(key_path(db_path), key)


def is_earlier_name(name: str) -> bool:
    return bool(_EARLIER_NAME.match(name))


def earlier_name(key: bytes) -> str:
    """The file name an earlier key is kept under: named after its check, so the same key is one file."""
    return f"sms-optout-earlier-{key_check(key)[len(_CHECK_PREFIX):][:16]}.key"


def load_earlier_keys(db_path: str | os.PathLike) -> list[bytes]:
    """Keys kept from before a restore: fingerprints made with them still have to match."""
    if in_memory(db_path):
        return []
    out: list[bytes] = []
    try:
        names = sorted(n for n in os.listdir(Path(str(db_path)).parent) if is_earlier_name(n))
    except OSError:
        return out
    for name in names:
        key = _read_key_file(Path(str(db_path)).parent / name)
        if key is not None and earlier_name(key) == name and key not in out:
            out.append(key)
    return out


def keep_earlier_key(db_path: str | os.PathLike, key: bytes) -> None:
    if in_memory(db_path):
        return
    _write_key_file(Path(str(db_path)).parent / earlier_name(key), key)


def keyring(db_path: str | os.PathLike, numbers: list[str]) -> tuple[list[bytes], bool]:
    """(the keys this record can be read with, the key in use first; whether EVERY fingerprint can be matched).

    A key counts when the record holds its check row. A check row whose key is not on this computer means some
    fingerprints cannot be matched: not complete. A record with no check row at all (one made before check rows,
    or an empty one) takes the key in use as it is."""
    primary = load_key(db_path)
    on_disk = ([primary] if primary is not None else []) + [k for k in load_earlier_keys(db_path) if k != primary]
    checks = {n for n in numbers if is_check(n)}
    has_fingerprints = any(is_fingerprint(n) for n in numbers)
    if not checks:
        return ([primary] if primary is not None else []), (primary is not None or not has_fingerprints)
    usable = [k for k in on_disk if key_check(k) in checks]
    return usable, ({key_check(k) for k in usable} >= checks or not has_fingerprints)


def make_key(db_path: str | os.PathLike) -> bytes:
    """A fresh key, made EXCLUSIVELY: if a key file is already there (another store made it a moment ago), that
    key is returned and nothing is written. A file that still holds no key after a short wait is not a key and is
    replaced; callers hold the database's write lock, so no other store is writing one then."""
    path = key_path(db_path)
    key = secrets.token_bytes(KEY_BYTES)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        for _ in range(20):
            existing = load_key(db_path)
            if existing is not None:
                return existing
            time.sleep(0.05)
        write_key(db_path, key)
        return key
    with os.fdopen(fd, "wb") as f:
        f.write(key)
        f.flush()
        os.fsync(f.fileno())
    return key


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
        self._keys: list[bytes] = []         # the key in use first, then earlier keys; empty = failed closed
        self.db.execute(_SQL)
        self.db.commit()
        self._open()

    @property
    def _key(self) -> bytes | None:
        """The key in use: new opt-outs are fingerprinted with it."""
        return self._keys[0] if self._keys else None

    # ── key and start-up ──

    def _rows(self) -> list[tuple[str, str]]:
        return [(r["number"], r["opted_out_at"]) for r in self.db.fetchall("SELECT number, opted_out_at FROM sms_optouts")]

    def _fail_closed(self) -> None:
        self._keys = []
        if "lost" not in _warned:
            _warned.add("lost")
            logger.error("SMS do-not-text key is missing while the record has entries: texting is limited to the "
                         "owner until the key is restored (a backup restore brings it back)")

    def _open(self) -> None:
        """Find the keys, or make one if that is safe; then convert plain rows and take in pending STOPs."""
        numbers = [n for n, _ in self._rows()]
        if not in_memory(self.db_path):
            primary = load_key(self.db_path)
            usable, complete = keyring(self.db_path, numbers)
            settled = primary is not None and key_check(primary) in numbers and complete
            if settled and not any(not is_fingerprint(n) and not is_check(n) for n in numbers):
                assert primary is not None
                self._keys = [primary] + [k for k in usable if k != primary]   # the usual case: nothing to write
                _warned.discard("lost")
                self._take_in_pending()
                return
        # Something has to be decided or written (first start, an upgrade with plain rows, a key that is gone).
        # ONE store at a time does that, in this process and across processes: the look and the write happen under
        # the database's write lock, and a fresh key is made exclusively. Otherwise two stores starting together
        # could each make a key, and the fingerprints made with the losing key would match nobody.
        with self.db._lock:
            conn = self.db.conn
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            try:
                self._open_locked(conn)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        if self._keys:
            self._take_in_pending()

    def _open_locked(self, conn: Any) -> None:
        rows = [(r[0], r[1]) for r in conn.execute("SELECT number, opted_out_at FROM sms_optouts").fetchall()]
        numbers = [n for n, _ in rows]
        has_fingerprints = any(is_fingerprint(n) for n in numbers)
        key: bytes
        others: list[bytes] = []
        if in_memory(self.db_path):                      # no file, no folder: the key lives and dies with the connection
            held = getattr(self.db, "_sms_optout_key", None)
            key = held if isinstance(held, bytes) else secrets.token_bytes(KEY_BYTES)
            self.db._sms_optout_key = key
        else:
            usable, complete = keyring(self.db_path, numbers)
            if has_fingerprints and not complete:
                self._fail_closed()                      # fingerprints nobody can match
                return
            found = load_key(self.db_path)
            if found is None and has_fingerprints:
                found = usable[0]                        # every fingerprint is matched by an earlier key:
                write_key(self.db_path, found)           # it becomes the key in use again
            elif found is None:
                found = make_key(self.db_path)           # a fresh key: only when no fingerprint exists
            key = found
            others = [k for k in usable if k != key]
        self._keys = [key, *others]
        _warned.discard("lost")
        if not has_fingerprints:                         # check rows of keys that no fingerprint was made with
            conn.execute("DELETE FROM sms_optouts WHERE number LIKE 'k:%' AND number != ?", (key_check(key),))
        if key_check(key) not in numbers:
            conn.execute("INSERT OR IGNORE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                         (key_check(key), _now()))
        plain = [(n, at) for n, at in rows if not is_fingerprint(n) and not is_check(n)]
        for n, at in plain:                              # the same transaction: all of them or none
            conn.execute("INSERT OR IGNORE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                         (fingerprint(key, n), at))
            conn.execute("DELETE FROM sms_optouts WHERE number = ?", (n,))
        if plain:
            logger.info("SMS do-not-text record: %d plain number(s) replaced by fingerprints", len(plain))

    def _take_in_pending(self) -> None:
        rows = _read_pending(self.db_path)
        key = self._key
        if not rows or key is None:
            return
        for row in rows:
            self.db.execute("INSERT OR IGNORE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                            (fingerprint(key, row["number"]), row["at"]))
        self.db.commit()
        _write_pending(self.db_path, [])     # only after the commit: a crash in between keeps the file
        logger.info("SMS do-not-text record: %d STOP(s) kept while the key was missing are now recorded", len(rows))

    @property
    def failed_closed(self) -> bool:
        """True while the record holds fingerprints that no key on this computer can match. Checked again each
        time, so putting the key file back (a restore does it) ends it without a restart."""
        if not self._keys:
            self._open()
        return not self._keys

    # ── the record ──

    def _prints(self, number: str) -> list[str]:
        """This number under every key the record can be read with, plus its plain form (a row 0.9.x wrote)."""
        return [*(fingerprint(k, number) for k in self._keys), number_key(number)]

    def record(self, number: str) -> None:
        if self.failed_closed:
            rows = _read_pending(self.db_path)
            nk = number_key(number)
            if nk and all(r["number"] != nk for r in rows):
                rows.append({"number": nk, "at": _now()})
                _write_pending(self.db_path, rows)
            return
        key = self._key
        assert key is not None
        self.db.execute("INSERT OR REPLACE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
                        (fingerprint(key, number), _now()))
        self.db.commit()

    def clear(self, number: str) -> None:
        """START. Without the keys a fingerprint row cannot be found, so only a pending STOP is dropped; the person
        stays opted out (the safe side) and START works again once the key is back."""
        nk = number_key(number)
        rows = _read_pending(self.db_path)
        kept = [r for r in rows if r["number"] != nk]
        if len(kept) != len(rows):
            _write_pending(self.db_path, kept)
        if self.failed_closed:
            return
        prints = self._prints(number)
        self.db.execute(f"DELETE FROM sms_optouts WHERE number IN ({','.join('?' * len(prints))})", tuple(prints))
        self.db.commit()

    def state(self, number: str) -> str:
        """CLEAR, OPTED_OUT, or KEY_MISSING (failed closed and this number cannot be checked)."""
        nk = number_key(number)
        if any(r["number"] == nk for r in _read_pending(self.db_path)):
            return OPTED_OUT
        if self.failed_closed:
            return KEY_MISSING
        prints = self._prints(number)
        hit = self.db.fetchone(f"SELECT 1 AS x FROM sms_optouts WHERE number IN ({','.join('?' * len(prints))})",
                               tuple(prints))
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
    usable, complete = keyring(db_path, rows)
    if not complete:
        return KEY_MISSING
    return OPTED_OUT if any(fingerprint(k, number) in rows for k in usable) else CLEAR


def owner_numbers(config: dict[str, Any] | None = None) -> set[str]:
    """The owner's own SMS numbers, from EXPLICIT settings only: WINDY_OWNER_IDS and config [trust] owner_ids
    ("sms:+1555..."). A first-contact binding never counts here: a stranger who texted first is not the owner."""
    from windyfly.channels.identity import _parse_pairs

    nums = set(_parse_pairs(os.environ.get("WINDY_OWNER_IDS", "")).get("sms", set()))
    for pair in ((config or {}).get("trust", {}) or {}).get("owner_ids", []) or []:
        nums |= _parse_pairs(str(pair)).get("sms", set())
    return {number_key(n) for n in nums if number_key(n)}


def send_state(number: str, db_path: str, config: dict[str, Any] | None = None) -> str:
    """What ANY path that is about to text ``number`` must ask first (the direct channel, the /sms command, the
    Windy Text tool): CLEAR, OPTED_OUT or KEY_MISSING. KEY_MISSING never applies to the owner's own number: while
    the record cannot be read, the owner is the one person the agent may still text (LEGAL DECISION 28 (9)(2))."""
    state = state_readonly(number, db_path)
    if state == KEY_MISSING and number_key(number) in owner_numbers(config):
        return CLEAR
    return state


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

def is_side_file(name: str) -> bool:
    """A file name this module puts beside the database in a backup."""
    return name in (KEY_FILE, PENDING_FILE) or is_earlier_name(name)


def side_file_paths(db_path: str | os.PathLike) -> list[Path]:
    """The files beside the database that belong to this record (the key in use, earlier keys, the pending
    list), for an export that copies files as they are."""
    if in_memory(db_path):
        return []
    folder = Path(str(db_path)).parent
    try:
        return sorted(folder / n for n in os.listdir(folder) if is_side_file(n) and (folder / n).is_file())
    except OSError:
        return []


def backup_files(db_path: str | os.PathLike) -> dict[str, bytes]:
    """What goes into a backup beside the database, each as its own file: the key in use, any earlier keys, and
    the pending STOPs if any."""
    out: dict[str, bytes] = {}
    key = load_key(db_path)
    if key is not None:
        out[KEY_FILE] = key
    for earlier in load_earlier_keys(db_path):
        if earlier != key:
            out[earlier_name(earlier)] = earlier
    rows = _read_pending(db_path)
    if rows:
        out[PENDING_FILE] = "".join(json.dumps(r) + "\n" for r in rows).encode()
    return out


def restore_files(db_path: str | os.PathLike, files: dict[str, bytes]) -> None:
    """Put a backup's key in place as the key in use (the restored fingerprints were made with it), keep its
    earlier keys, and add its pending STOPs to any kept here. A backup without a key leaves the key in use
    alone; the check rows then decide whether every fingerprint can be matched."""
    for name, data in files.items():
        if is_earlier_name(name) and len(data) == KEY_BYTES and earlier_name(data) == name:
            keep_earlier_key(db_path, data)
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


# ── a restore never removes an opt-out (Hub on #777, BOARD 2026-11-11T04:00:49Z; Boss 03:37:10Z (b)) ──

def live_record(conn: Any, db_path: str | os.PathLike) -> dict[str, Any]:
    """The record as it is NOW in the live database, read just before a restore replaces that database: every
    row, and every key on this computer. Empty when nobody ever opted out."""
    if in_memory(db_path):
        return {"rows": [], "keys": []}
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sms_optouts'").fetchone() is None:
            return {"rows": [], "keys": []}
        rows = [(r[0], r[1]) for r in conn.execute("SELECT number, opted_out_at FROM sms_optouts").fetchall()]
    except sqlite3.Error:
        return {"rows": [], "keys": []}
    if not any(not is_check(n) for n, _ in rows):
        return {"rows": [], "keys": []}                  # check rows only: there is no opt-out to carry
    primary = load_key(db_path)
    keys = ([primary] if primary is not None else []) + [k for k in load_earlier_keys(db_path) if k != primary]
    return {"rows": rows, "keys": keys}


def carry_into(incoming: Any, db_path: str | os.PathLike, live: dict[str, Any]) -> int:
    """Put the live record into ``incoming``, the database a restore (or an undo) is about to copy over the live
    one, and keep the live keys as earlier keys. The copy then lands with BOTH records in it, in one step, so an
    opt-out made after that backup is never removed. Returns how many rows were carried.

    The keys are written BEFORE the copy: if the copy then fails, an extra copy of a key that is still in use is
    all that is left. Opt-outs only ever accumulate here: a person who texted START after the backup and then
    STOP-ed before it stays opted out, the safe side, until they text START again."""
    rows = live.get("rows") or []
    if not rows:
        return 0
    for key in live.get("keys") or []:
        keep_earlier_key(db_path, key)
    incoming.execute(_SQL)
    incoming.executemany("INSERT OR IGNORE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)", rows)
    incoming.commit()
    return len(rows)
