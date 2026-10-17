"""The safe restore (strand P4.11.16, N6.1, N6.12; Hub 10-17; specs/AGENT_RESTORE_REVIEW_P4_11_16_2026-10-17.md).

A fake Windy Cloud runs behind ``httpx.MockTransport`` so the real streaming download is used. The live
memory is a real file database that stays open the whole time, as on a running agent.
"""

from __future__ import annotations

import asyncio
import gzip
import os
import sqlite3
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

import windyfly.cloud_backup as cb
from windyfly import backup_restore as br
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node

KEY = "cli:restore"
OWNER_KEY = "restore-test-owner-key"


class FakeCloud:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.status: int | None = None
        self.calls: list[str] = []
        self.redirect_to: str | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        if "/list/" in url:
            files = [{"filename": n, "size_bytes": len(b), "created_at": "2026-10-01T09:30:00Z"}
                     for n, b in sorted(self.store.items(), reverse=True)]
            return httpx.Response(200, json={"product": "windy_fly", "count": len(files), "files": files})
        if self.redirect_to:
            return httpx.Response(302, headers={"Location": self.redirect_to})
        if self.status:
            return httpx.Response(self.status, json={"detail": "x"})
        name = url.rsplit("/", 1)[-1]
        if name not in self.store:
            return httpx.Response(404, json={"detail": "File not found"})
        return httpx.Response(200, content=self.store[name])


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    fake = FakeCloud()
    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(fake.handler)}))
    monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setenv("WINDY_BACKUP_KEY", OWNER_KEY)
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
    monkeypatch.setattr("windyfly.auth.bot_credentials.ecosystem_auth_header",
                        AsyncMock(return_value={"Authorization": "Bearer t"}))
    monkeypatch.setattr("windyfly.auth.bot_credentials.get_bot_key", AsyncMock(return_value=None))
    monkeypatch.setattr("windyfly.agent.cloud_auth.agent_headers", lambda *a, **k: None)
    monkeypatch.setattr(cb, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cb, "_BACKUP_STATE_FILE", tmp_path / "backup_state.json")
    br._questions.clear()
    yield fake
    br._questions.clear()


def _memory(path: Path, canary: str) -> None:
    d = Database(str(path))
    try:
        upsert_node(d, "fact", canary)
        d.commit()
        d.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        d.close()


def _backup_blob(tmp_path: Path, canary: str, *, key: str = OWNER_KEY, raw: bytes | None = None) -> bytes:
    if raw is None:
        src = tmp_path / f"src-{canary}.db"
        _memory(src, canary)
        raw = src.read_bytes()
        src.unlink()
    os.environ["WINDY_BACKUP_KEY"] = key
    try:
        return cb._encrypt_data(gzip.compress(raw), cb._get_encryption_key())
    finally:
        os.environ["WINDY_BACKUP_KEY"] = OWNER_KEY


def _yes(db: Database) -> str:
    return asyncio.run(br.confirm(db, KEY))


def _names(db: Database) -> list[str]:
    return sorted(r["name"] for r in db.fetchall("SELECT name FROM nodes"))


@pytest.fixture
def live(tmp_path):
    path = tmp_path / "data" / "windyfly.db"
    path.parent.mkdir()
    db = Database(str(path))
    upsert_node(db, "fact", "zqxtoday")
    db.commit()
    yield db
    db.close()


class TestListAndRestoreRoundTrip:
    def test_backup_list_restore(self, cloud, live, tmp_path):
        """N6.1 / P4.11.16: restore "latest" brings back exactly the backed-up memory, after a yes."""
        cloud.store["windyfly-20261001.enc"] = _backup_blob(tmp_path, "zqxbackedup")
        asked = asyncio.run(br.ask_restore(live, KEY))
        assert "2026-10-01 09:30 UTC" in asked and "comes back" in asked and "/backup yes" in asked
        assert _names(live) == ["zqxtoday"]                              # nothing before the yes
        done = _yes(live)
        assert done.startswith("Restored from windyfly-20261001.enc")
        assert _names(live) == ["zqxbackedup"]                           # through the open connection
        assert live.fetchone("PRAGMA integrity_check")["integrity_check"] == "ok"
        assert live.fetchone("PRAGMA journal_mode")["journal_mode"] == "wal"       # still WAL


def test_a_second_open_connection_sees_the_restore_and_the_file_stays_whole(cloud, live, tmp_path):
    other = Database(live.db_path)                                       # like the decay job's own handle
    try:
        assert [r["name"] for r in other.fetchall("SELECT name FROM nodes")] == ["zqxtoday"]
        cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
        asyncio.run(br.ask_restore(live, KEY))
        _yes(live)
        assert [r["name"] for r in other.fetchall("SELECT name FROM nodes")] == ["zqxbackedup"]
        upsert_node(live, "fact", "zqxafter")                            # the agent keeps writing
        live.commit()
    finally:
        other.close()
    check = sqlite3.connect(live.db_path)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert sorted(r[0] for r in check.execute("SELECT name FROM nodes")) == ["zqxafter", "zqxbackedup"]
    finally:
        check.close()


def test_undo_puts_the_memory_back_and_no_plaintext_copy_is_ever_left(cloud, live, tmp_path):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))
    _yes(live)
    undo = Path(live.db_path + ".undo.enc")
    assert undo.is_file() and (undo.stat().st_mode & 0o777) == 0o600
    assert b"zqxtoday" not in undo.read_bytes()                          # encrypted
    for p in Path(live.db_path).parent.iterdir():
        assert not p.name.endswith(".pre-restore")
        if p != undo and not p.name.startswith("windyfly.db"):
            raise AssertionError(f"unexpected file {p.name}")
    assert "Put back my memory" in br.ask_undo(live, KEY)
    assert _names(live) == ["zqxbackedup"]                               # nothing before the yes
    assert "undo copy is now deleted" in _yes(live)
    assert _names(live) == ["zqxtoday"] and not undo.exists()


def test_without_the_owner_key_nothing_is_fetched_and_the_derived_key_is_never_tried(cloud, live, tmp_path,
                                                                                         monkeypatch):
    monkeypatch.delenv("WINDY_BACKUP_KEY")
    derived = cb._encrypt_data(gzip.compress(b"x"), cb._get_encryption_key())   # what the derived key would open
    cloud.store["b.enc"] = derived
    assert asyncio.run(br.ask_restore(live, KEY)) == br.NO_OWNER_KEY
    assert cloud.calls == []
    assert _names(live) == ["zqxtoday"]


@pytest.mark.parametrize("raw,word", [
    (b"not a database at all", "not a memory database"),
    (b"SQLite format 3\x00" + b"\x00" * 200, "damaged"),
])
def test_a_bad_backup_is_refused_and_the_memory_is_untouched(cloud, live, tmp_path, raw, word):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=raw)
    out = asyncio.run(br.ask_restore(live, KEY))
    assert word in out and "Nothing was changed" in out
    assert "Nothing is waiting" in _yes(live)
    assert _names(live) == ["zqxtoday"]


def test_a_backup_from_a_newer_agent_is_refused(cloud, live, tmp_path):
    src = tmp_path / "newer.db"
    _memory(src, "x")
    c = sqlite3.connect(src)
    c.execute("INSERT INTO schema_version (version, description) VALUES (?, 'future')", (br.code_schema_version() + 1,))
    c.commit()
    c.close()
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=src.read_bytes())
    assert "newer version" in asyncio.run(br.ask_restore(live, KEY))


def test_a_backup_made_with_another_key_is_refused(cloud, live, tmp_path):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", key="someone-elses-key")
    assert "different key" in asyncio.run(br.ask_restore(live, KEY))


def test_size_caps_stop_a_big_download_and_a_gzip_bomb(cloud, live, tmp_path, monkeypatch):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    monkeypatch.setattr(br, "MAX_DOWNLOAD_BYTES", 100)
    assert "too large to download" in asyncio.run(br.ask_restore(live, KEY))
    monkeypatch.setattr(br, "MAX_DOWNLOAD_BYTES", 10**9)
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=b"SQLite format 3\x00" + b"\x00" * 5_000_000)
    monkeypatch.setattr(br, "MAX_RESTORED_BYTES", 1_000_000)
    assert "unpacks to more" in asyncio.run(br.ask_restore(live, KEY))


@pytest.mark.parametrize("status,words", [
    (423, "locked this agent's storage"), (401, "refused this agent's credentials"), (410, "trash"),
])
def test_cloud_answers_are_said_plainly(cloud, live, tmp_path, status, words):
    cloud.store["b.enc"] = b"x"
    cloud.status = status
    out = asyncio.run(br.ask_restore(live, KEY))
    assert words in out and "Nothing was changed" in out


def test_a_named_backup_that_is_not_there_says_so(cloud, live, tmp_path):
    cloud.store["b.enc"] = b"x"
    assert "no backup called nope.enc" in asyncio.run(br.ask_restore(live, KEY, "nope.enc"))
    cloud.store.clear()
    assert "no backups on Windy Cloud yet" in asyncio.run(br.ask_restore(live, KEY))


def test_a_redirect_is_not_followed_with_the_token(cloud, live, tmp_path):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x")
    cloud.redirect_to = "https://elsewhere.example.test/steal"
    out = asyncio.run(br.ask_restore(live, KEY))
    assert "answered 302" in out
    assert not any("elsewhere" in u for u in cloud.calls)


def test_guest_mode_refuses(cloud, live, tmp_path, monkeypatch):
    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    for out in (asyncio.run(br.ask_restore(live, KEY)), _yes(live), br.ask_undo(live, KEY)):
        assert "guest mode" in out
    assert cloud.calls == []


def test_the_question_expires_and_cancel_drops_it(cloud, live, tmp_path, monkeypatch):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))
    assert br.cancel(KEY) and "Nothing is waiting" in _yes(live)
    asyncio.run(br.ask_restore(live, KEY))
    later = time.time() + br.QUESTION_FOR_S + 1
    monkeypatch.setattr(br.time, "time", lambda: later)
    assert "Nothing is waiting" in _yes(live)
    assert _names(live) == ["zqxtoday"]


def test_the_undo_copy_is_wiped_after_24_hours(cloud, live, tmp_path, monkeypatch):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))
    _yes(live)
    later = time.time() + br.UNDO_FOR_S + 1
    monkeypatch.setattr(br.time, "time", lambda: later)
    assert br.sweep_undo(live.db_path) is True                          # the startup sweep, by path
    assert not Path(live.db_path + ".undo.enc").exists()
    assert "nothing to undo" in br.ask_undo(live, KEY)


def test_an_older_backup_is_brought_up_to_this_schema(cloud, live, tmp_path):
    src = tmp_path / "older.db"
    _memory(src, "zqxold")
    c = sqlite3.connect(src)
    c.execute("DELETE FROM schema_version WHERE version = ?", (br.code_schema_version(),))
    c.commit()
    c.close()
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=src.read_bytes())
    asyncio.run(br.ask_restore(live, KEY))
    assert _yes(live).startswith("Restored")
    assert live.fetchone("SELECT MAX(version) AS v FROM schema_version")["v"] == br.code_schema_version()
    assert _names(live) == ["zqxold"]


def test_the_backup_commands_end_to_end(cloud, live, tmp_path):
    from windyfly.channels.base import handle_incoming
    from windyfly.commands.core import wire_runtime
    from windyfly.commands.setup import init_all_commands

    init_all_commands(db=live, config={})
    wire_runtime(db=live)

    def say(text):
        ok, out = asyncio.run(handle_incoming(text, {"platform": "cli", "channel_id": "restore"}))
        assert ok
        return out

    cloud.store["windyfly-20261001.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    listed = say("/backup list")
    assert "windyfly-20261001.enc" in listed and "2026-10-01 09:30 UTC" in listed and "?" not in listed
    assert "/backup yes" in say("/backup restore latest")
    assert "Cancelled" in say("/backup cancel")
    say("/backup restore windyfly-20261001.enc")
    assert say("/backup yes").startswith("Restored")
    assert _names(live) == ["zqxbackedup"]
    assert "/backup yes" in say("/backup undo")
    say("/backup yes")
    assert _names(live) == ["zqxtoday"]
    assert "stop the agent first" in say("/import old.tar.gz")


def test_the_question_holds_no_backup_data(cloud, live, tmp_path):
    """Hub: while the owner decides, only the backup's name and the ciphertext's sha256 are kept."""
    import hashlib

    blob = _backup_blob(tmp_path, "zqxbackedup")
    cloud.store["b.enc"] = blob
    asyncio.run(br.ask_restore(live, KEY))
    q = br._questions[KEY]
    assert set(vars(q)) == {"kind", "name", "digest", "label", "asked"}
    assert q.name == "b.enc" and q.digest == hashlib.sha256(blob).hexdigest()
    assert cloud.store["b.enc"] == blob                                  # the header edit never touched it


def test_a_backup_that_changed_since_the_question_is_not_restored(cloud, live, tmp_path):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxfirst")
    asyncio.run(br.ask_restore(live, KEY))
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxswapped")
    assert "changed since you were asked" in _yes(live)
    assert _names(live) == ["zqxtoday"]


def test_a_backup_with_another_page_size_is_refused(cloud, live, tmp_path):
    src = tmp_path / "big-pages.db"
    _memory(src, "x")                                   # a real memory, then rewritten with 8 KiB pages
    c = sqlite3.connect(src)
    c.execute("PRAGMA journal_mode = DELETE")
    c.execute("PRAGMA page_size = 8192")
    c.execute("VACUUM")
    c.close()
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=src.read_bytes())
    assert "different database page size" in asyncio.run(br.ask_restore(live, KEY))


def test_a_copy_that_fails_changes_nothing_and_leaves_no_undo(cloud, live, tmp_path, monkeypatch):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))

    def broken(src, dst):
        raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(br, "_copy_pages", broken)
    assert "nothing was changed" in _yes(live)
    assert _names(live) == ["zqxtoday"]
    assert not Path(live.db_path + ".undo.enc").exists()


def test_a_failed_migration_after_the_copy_points_to_undo(cloud, live, tmp_path, monkeypatch):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))

    def boom():
        raise RuntimeError("migration 99 failed")

    monkeypatch.setattr(live, "_run_migrations", boom)
    out = _yes(live)
    assert "/backup undo" in out and "couldn't bring it up to date" in out
    assert Path(live.db_path + ".undo.enc").exists()


def test_a_database_that_is_not_a_windy_fly_memory_is_refused(cloud, live, tmp_path):
    other = tmp_path / "other.db"
    c = sqlite3.connect(other)
    c.execute("CREATE TABLE notes (x)")
    c.commit()
    c.close()
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=other.read_bytes())
    assert "not a Windy Fly memory" in asyncio.run(br.ask_restore(live, KEY))


def test_one_restore_at_a_time(cloud, live, tmp_path):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))
    assert br._restoring.acquire(blocking=False)
    try:
        assert "already running" in _yes(live)
    finally:
        br._restoring.release()
    assert _yes(live).startswith("Restored")                      # once the first one is done


def test_the_owner_is_told_when_the_restored_memory_fails_its_check(cloud, live, tmp_path, monkeypatch):
    cloud.store["b.enc"] = _backup_blob(tmp_path, "zqxbackedup")
    asyncio.run(br.ask_restore(live, KEY))
    real = br._restore_into
    monkeypatch.setattr(br, "_restore_into", lambda db, data: (real(db, data), False)[1])
    out = _yes(live)
    assert "failed its check afterwards" in out and "/backup undo" in out




def test_a_backup_that_fails_its_foreign_key_check_is_refused(cloud, live, tmp_path):
    src = tmp_path / "fk.db"
    _memory(src, "x")
    c = sqlite3.connect(src)
    c.execute("PRAGMA foreign_keys = OFF")
    c.execute("CREATE TABLE extra (node TEXT REFERENCES nodes(id))")      # the schema declares none itself
    c.execute("INSERT INTO extra VALUES ('no-such-node')")
    c.commit()
    c.close()
    cloud.store["b.enc"] = _backup_blob(tmp_path, "x", raw=src.read_bytes())
    out = asyncio.run(br.ask_restore(live, KEY))
    assert "foreign-key check" in out and "no override" in out
