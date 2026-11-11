"""LEGAL DECISION 28 (8), (9), (10) (Windy Legal, BOARD 2026-11-09T12:54:11Z, 12:55:05Z, 12:55:42Z): the do-not-text
record holds a keyed fingerprint of a number, never the number. The key is its own 0600 file and travels inside the
owner's encrypted backup. Fingerprints with no key fail closed (only the owner is texted, no override), a STOP
received then is kept in a 0600 pending file, and the activity log keeps only the last four digits."""

from __future__ import annotations

import gzip
import json
import os
import sqlite3
import stat
import tarfile
from unittest.mock import patch

import pytest

from windyfly import backup_restore as br
from windyfly import cloud_backup as cb
from windyfly import sms_optout as so
from windyfly.channels import sms as sms_mod
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

PERSON = "+15559999999"
PERSON_KEY = "15559999999"
OTHER = "+15558888888"
OWNER = "+15550001111"
OWNER_BACKUP_KEY = "o" * 44


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_SMS_ALLOW_NON_OWNER", "1")
    monkeypatch.setenv("WINDY_OWNER_IDS", f"sms:{OWNER}")
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")
    so._warned.clear()
    sms_mod._masked_once.clear()
    (tmp_path / "data").mkdir()
    return tmp_path / "data" / "mem.db"


@pytest.fixture
def chan(env):
    db = Database(str(env))
    wq = WriteQueue()
    wq.start()
    box = {"db": db, "wq": wq, "path": env}

    def make():
        return sms_mod.WindyFlySMS({"agent": {"default_model": "m"}, "personality": {}}, db, wq)

    box["make"] = make
    box["sms"] = make()
    yield box
    wq.stop()
    db.close()


def _rows(db) -> list[str]:
    return [r["number"] for r in db.fetchall("SELECT number FROM sms_optouts")]


def _fingerprints(db) -> list[str]:
    return [n for n in _rows(db) if so.is_fingerprint(n)]


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def _no_network():
    return patch("urllib.request.urlopen", side_effect=AssertionError("Twilio must not be called"))


class _Sent:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b'{"sid": "SM1"}'


# ── (8)(1): a fingerprint, not the number ──

def test_stop_stores_a_fingerprint_and_the_number_is_nowhere_in_the_record(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    key = so.load_key(chan["path"])
    assert key is not None and len(key) == 32
    assert _fingerprints(db) == [so.fingerprint(key, PERSON)]
    assert not any(PERSON_KEY in n or "5559999999" in n for n in _rows(db))
    assert sms.is_opted_out("555-999-9999") and not sms.is_opted_out(OTHER)


def test_the_key_is_its_own_0600_file_beside_the_memory_and_not_in_the_database(chan):
    path = so.key_path(chan["path"])
    assert path.parent == chan["path"].parent and _mode(path) == 0o600
    key = path.read_bytes()
    chan["sms"].handle_inbound(PERSON, "STOP")
    chan["db"].execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = chan["path"].read_bytes()
    assert key not in raw and key.hex().encode() not in raw


def test_a_looser_key_file_is_tightened_when_read(chan):
    path = so.key_path(chan["path"])
    os.chmod(path, 0o644)
    assert so.load_key(chan["path"]) is not None and _mode(path) == 0o600


def test_plain_rows_from_the_old_code_become_fingerprints_at_start_up(env):
    db = Database(str(env))
    wq = WriteQueue()
    wq.start()
    try:
        db.execute(so._SQL)
        db.execute("INSERT INTO sms_optouts VALUES (?, ?)", (PERSON_KEY, "2026-11-09T12:00:00+00:00"))
        db.execute("INSERT INTO sms_optouts VALUES (?, ?)", ("15558887777", "2026-11-09T13:00:00+00:00"))
        db.commit()
        sms = sms_mod.WindyFlySMS({}, db, wq)
        key = so.load_key(env)
        rows = {r["number"]: r["opted_out_at"] for r in db.fetchall("SELECT * FROM sms_optouts")}
        assert rows[so.fingerprint(key, PERSON)] == "2026-11-09T12:00:00+00:00"      # the time is kept
        assert len(_fingerprints(db)) == 2 and PERSON_KEY not in rows and "15558887777" not in rows
        with _no_network():
            assert sms.send_sms(PERSON, "x")["error"] == "recipient_opted_out"
    finally:
        wq.stop()
        db.close()


def test_a_failed_conversion_changes_nothing(env, monkeypatch):
    db = Database(str(env))
    try:
        db.execute(so._SQL)
        db.execute("INSERT INTO sms_optouts VALUES (?, ?)", (PERSON_KEY, "t1"))
        db.execute("INSERT INTO sms_optouts VALUES (?, ?)", ("15558887777", "t2"))
        db.commit()
        calls = {"n": 0}
        real = so.fingerprint

        def flaky(key, number):
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("disk full")
            return real(key, number)

        monkeypatch.setattr(so, "fingerprint", flaky)
        with pytest.raises(sqlite3.OperationalError):
            so.OptOutStore(db)
        assert sorted(n for n in _rows(db) if not so.is_check(n)) == ["15558887777", PERSON_KEY]   # one transaction
    finally:
        db.close()


def test_two_stores_starting_together_after_the_upgrade_end_with_one_key(env):
    """Boss 2026-11-11T03:37:10Z (a): plain rows and no key yet, two processes open at the same moment. Each used
    to make its own key; the second file won and the first one's fingerprints matched nobody."""
    import threading

    con = sqlite3.connect(str(env))
    con.execute(so._SQL)
    numbers = [f"1555000{i:04d}" for i in range(40)]
    con.executemany("INSERT INTO sms_optouts VALUES (?, 't')", [(n,) for n in numbers])
    con.commit()
    con.close()
    dbs = [Database(str(env)) for _ in range(4)]          # four connections = four processes, as SQLite sees it
    gate, stores, errors = threading.Barrier(len(dbs)), {}, []

    def start(i):
        try:
            gate.wait(timeout=10)
            stores[i] = so.OptOutStore(dbs[i])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=start, args=(i,)) for i in range(len(dbs))]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        assert not errors, errors
        key = so.load_key(env)
        assert {st._key for st in stores.values()} == {key}                    # ONE key, held by all of them
        rows = _rows(dbs[0])
        assert sorted(n for n in rows if so.is_fingerprint(n)) == sorted(so.fingerprint(key, n) for n in numbers)
        assert [n for n in rows if so.is_check(n)] == [so.key_check(key)]      # one check row, for that key
        assert all(stores[i].state(n) == so.OPTED_OUT for i in stores for n in (numbers[0], numbers[-1]))
    finally:
        for d in dbs:
            d.close()


def test_a_fresh_key_is_made_exclusively(env):
    first = so.make_key(env)
    assert so.make_key(env) == first and so.load_key(env) == first             # the second caller takes the first's
    assert _mode(so.key_path(env)) == 0o600
    so.key_path(env).write_bytes(b"short")                                     # a file that is not a key
    again = so.make_key(env)
    assert len(again) == 32 and so.load_key(env) == again


def test_an_opt_out_row_never_expires(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    db.execute("UPDATE sms_optouts SET opted_out_at = '2001-01-01T00:00:00+00:00'")
    db.commit()
    chan["wq"].stop()            # every queued event write (and its 30-day prune of the events table) has run
    assert chan["make"]().is_opted_out(PERSON)


def test_start_removes_the_fingerprint(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    sms.handle_inbound(PERSON, "START")
    assert _fingerprints(db) == [] and not sms.is_opted_out(PERSON)


# ── (8)(1): the activity log keeps the last four digits only ──

def test_mask_keeps_the_last_four_digits():
    assert so.mask("+1 (555) 123-0101") == "+1 •••-•••-0101"
    assert so.mask("5551230101") == "+1 •••-•••-0101"
    assert so.mask("+44 20 7946 0958") == "•••-0958"
    assert so.mask("12") == "•••-••••" and so.mask("") == "•••-••••"


def test_no_sms_event_holds_a_whole_number(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "HELP")
    sms.handle_inbound(PERSON, "STOP")
    sms.handle_inbound(PERSON, "hello?")
    with _no_network():
        sms.send_sms(PERSON, "x")
    with patch("urllib.request.urlopen", return_value=_Sent()):
        sms.send_sms(OTHER, "x")
    with patch("windyfly.channels.sms.agent_respond", return_value="ok"):
        sms.handle_inbound(OTHER, "hi")
    sms.handle_inbound(PERSON, "START")
    chan["wq"].stop()
    events = db.fetchall("SELECT event_type, data FROM events WHERE event_type LIKE 'sms.%'")
    kinds = {e["event_type"] for e in events}
    assert {"sms.help", "sms.optout", "sms.inbound_opted_out", "sms.outbound_refused", "sms.outbound",
            "sms.inbound", "sms.optin"} <= kinds
    blob = " ".join(e["data"] for e in events)
    for whole in ("5559999999", "5558888888"):
        assert whole not in blob
    assert json.dumps("+1 •••-•••-9999") in blob and json.dumps("+1 •••-•••-8888") in blob


def test_lines_the_old_code_wrote_are_masked_once_at_start_up(env):
    db = Database(str(env))
    wq = WriteQueue()
    wq.start()
    try:
        for kind, data in (("sms.optout", {"phone": PERSON}), ("sms.inbound", {"from": OTHER, "body_length": 2}),
                           ("sms.outbound", {"to": OTHER, "sid": "SM1"}), ("agent.respond", {"to": OTHER})):
            db.execute("INSERT INTO events (event_type, data, request_id) VALUES (?, ?, '')", (kind, json.dumps(data)))
        db.commit()
        sms_mod.WindyFlySMS({}, db, wq)
        got = {e["event_type"]: json.loads(e["data"]) for e in db.fetchall("SELECT event_type, data FROM events")}
        assert got["sms.optout"] == {"phone": "+1 •••-•••-9999"}
        assert got["sms.inbound"] == {"from": "+1 •••-•••-8888", "body_length": 2}
        assert got["sms.outbound"] == {"to": "+1 •••-•••-8888", "sid": "SM1"}
        assert got["agent.respond"] == {"to": OTHER}                 # only sms.* lines are touched
        assert so.mask_old_events(db) == 0                           # nothing left to change
    finally:
        wq.stop()
        db.close()


# ── (9)(2): fingerprints with no key fail closed; no override ──

def _lose_the_key(chan) -> bytes:
    path = so.key_path(chan["path"])
    key = path.read_bytes()
    path.unlink()
    return key


def test_a_fresh_key_is_made_only_when_the_record_holds_no_fingerprints(chan):
    sms, db = chan["sms"], chan["db"]
    first = _lose_the_key(chan)
    again = chan["make"]()                                   # an empty record: a new key is fine
    assert so.load_key(chan["path"]) not in (None, first) and not again.optouts.failed_closed
    again.handle_inbound(PERSON, "STOP")
    _lose_the_key(chan)
    lost = chan["make"]()
    assert lost.optouts.failed_closed and so.load_key(chan["path"]) is None      # never a new key over fingerprints
    assert len(_fingerprints(db)) == 1
    del sms


def test_with_the_key_gone_only_the_owner_is_texted(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    _lose_the_key(chan)
    sms = chan["make"]()
    with _no_network(), patch("windyfly.agent.owner_mail.send_sync") as mail:
        first = sms.send_sms(OTHER, "x")                     # a number that never opted out: nobody can tell
        second = sms.send_sms(PERSON, "x")
    assert first["status"] == "refused" and first["error"] == "optout_key_missing"
    assert first["owner_notice"] == sms_mod.KEY_MISSING_NOTICE
    assert second == {"status": "refused", "error": "optout_key_missing"}       # the owner is told ONCE
    assert mail.call_count == 1
    with patch("urllib.request.urlopen", return_value=_Sent()):
        assert sms.send_sms(OWNER, "x") == {"status": "sent", "message_sid": "SM1"}


def test_the_owner_notice_is_legals_sentence_word_for_word():
    # Windy Legal, BOARD 2026-11-11T03:24:49Z (a).
    assert sms_mod.KEY_MISSING_NOTICE == (
        "Right now I can only text you. The key that protects my do-not-text list is missing from this computer, "
        "so I can't tell who asked me to stop texting them. Restoring my latest backup brings the key back, if "
        "that backup has it. Until then I won't text anyone else.")


def test_a_new_channel_object_does_not_tell_the_owner_again(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    _lose_the_key(chan)
    with _no_network(), patch("windyfly.agent.owner_mail.send_sync") as mail:
        assert "owner_notice" in chan["make"]().send_sms(OTHER, "x")
        assert "owner_notice" not in chan["make"]().send_sms(OTHER, "x")
    assert mail.call_count == 1


def test_nothing_clears_the_record_to_get_texting_back(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    before = _rows(db)
    _lose_the_key(chan)
    lost = chan["make"]()
    with _no_network(), patch("windyfly.agent.owner_mail.send_sync"):
        lost.send_sms(OTHER, "x")
        lost.handle_inbound(OTHER, "hello")
        lost.handle_inbound(PERSON, "START")                 # cannot be matched without the key: stays opted out
        lost.clear_opt_out(PERSON)
    assert _rows(db) == before and so.load_key(chan["path"]) is None
    public = {n for n in dir(so.OptOutStore) if not n.startswith("_")}
    assert public == {"clear", "failed_closed", "record", "state"}             # no reset, no wipe, no force


def test_with_the_key_gone_a_stranger_gets_no_reply_and_the_agent_is_not_asked(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    _lose_the_key(chan)
    sms = chan["make"]()
    with patch("windyfly.channels.sms.agent_respond", return_value="ok") as agent:
        assert sms.handle_inbound(OTHER, "hello") == ""
        assert not agent.called
        assert sms.handle_inbound(OWNER, "hello") == "ok"    # the owner still talks to their agent


def test_the_wrong_key_counts_as_missing(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    so.write_key(chan["path"], b"\x07" * 32)                 # a key file from some other memory
    sms = chan["make"]()
    assert sms.optouts.failed_closed
    with _no_network(), patch("windyfly.agent.owner_mail.send_sync"):
        assert sms.send_sms(OTHER, "x")["error"] == "optout_key_missing"
    assert so.key_path(chan["path"]).read_bytes() == b"\x07" * 32             # and it is not overwritten


# ── (9)(3) + (10): STOP and HELP still answer; a STOP then is kept and recorded when the key is back ──

def test_stop_while_failed_closed_is_confirmed_kept_0600_and_recorded_when_the_key_returns(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    key = _lose_the_key(chan)
    sms = chan["make"]()
    assert "unsubscribed" in sms.handle_inbound(OTHER, "stop").lower()
    assert sms.handle_inbound(OTHER, "HELP").startswith("This is ")
    pending = so.pending_path(chan["path"])
    assert _mode(pending) == 0o600
    (row,) = [json.loads(line) for line in pending.read_text().splitlines()]
    assert row["number"] == "15558888888" and row["at"]
    assert sms.is_opted_out(OTHER)                           # known even now: it is in the pending list
    assert len(_fingerprints(chan["db"])) == 1               # nothing could be fingerprinted yet
    sms.handle_inbound(OTHER, "STOP")                        # twice is one line
    assert len(pending.read_text().splitlines()) == 1

    so.write_key(chan["path"], key)                          # the key is back (a restore does this)
    back = chan["make"]()
    assert not back.optouts.failed_closed and not pending.exists()
    assert sorted(_fingerprints(chan["db"])) == sorted([so.fingerprint(key, PERSON), so.fingerprint(key, OTHER)])
    with _no_network():
        assert back.send_sms(OTHER, "x")["error"] == "recipient_opted_out"
        assert back.send_sms(PERSON, "x")["error"] == "recipient_opted_out"


def test_the_same_channel_object_recovers_when_the_key_file_returns(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    key = _lose_the_key(chan)
    sms = chan["make"]()
    with _no_network(), patch("windyfly.agent.owner_mail.send_sync"):
        assert sms.send_sms(OTHER, "x")["error"] == "optout_key_missing"
    so.write_key(chan["path"], key)
    with patch("urllib.request.urlopen", return_value=_Sent()):
        assert sms.send_sms(OTHER, "x")["status"] == "sent"
    assert not (chan["path"].parent / sms_mod._TOLD_FILE).exists()   # a later loss tells the owner again


def test_start_while_failed_closed_drops_a_pending_stop_only(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    _lose_the_key(chan)
    sms = chan["make"]()
    sms.handle_inbound(OTHER, "STOP")
    sms.handle_inbound(OTHER, "START")
    assert not so.pending_path(chan["path"]).exists() and len(_fingerprints(chan["db"])) == 1


# ── the /sms command reads the same record ──

def test_the_read_only_path_gives_the_same_three_answers(chan):
    path = str(chan["path"])
    assert so.state_readonly(PERSON, path) == so.CLEAR
    chan["sms"].handle_inbound(PERSON, "STOP")
    assert so.state_readonly("555 999 9999", path) == so.OPTED_OUT
    assert so.state_readonly(OTHER, path) == so.CLEAR
    key = _lose_the_key(chan)
    assert so.state_readonly(OTHER, path) == so.KEY_MISSING
    assert sms_mod.number_opted_out(OTHER, path) is True
    assert sms_mod.optout_state(OWNER, path, {}) == so.CLEAR                      # WINDY_OWNER_IDS names the owner
    so.write_key(path, b"\x07" * 32)
    assert so.state_readonly(OTHER, path) == so.KEY_MISSING                       # the wrong key
    so.write_key(path, key)
    assert so.state_readonly(OTHER, path) == so.CLEAR


def test_a_plain_row_not_yet_converted_still_counts_on_the_read_only_path(env):
    con = sqlite3.connect(str(env))
    con.execute(so._SQL)
    con.execute("INSERT INTO sms_optouts VALUES (?, 't')", (PERSON_KEY,))
    con.commit()
    con.close()
    assert so.state_readonly(PERSON, str(env)) == so.OPTED_OUT
    assert so.state_readonly(OTHER, str(env)) == so.CLEAR and so.load_key(env) is None   # reading makes no key


def test_slash_sms_says_why_when_the_key_is_gone(chan, monkeypatch):
    import asyncio

    from windyfly.commands import ecosystem
    from windyfly.commands.registry import registry

    chan["sms"].handle_inbound(PERSON, "STOP")
    _lose_the_key(chan)
    monkeypatch.setattr(ecosystem, "_config", None)
    ecosystem.init_ecosystem(config={"memory": {"db_path": str(chan["path"])}})
    cmd = {c.name: c for c in registry.all()}["sms"].handler
    with patch.dict("sys.modules", {"twilio": None, "twilio.rest": None}):
        assert asyncio.run(cmd({"_args": [OTHER, "hi"]})) == sms_mod.KEY_MISSING_NOTICE
        assert "Twilio SDK not installed" in asyncio.run(cmd({"_args": [OWNER, "hi"]}))   # the owner gets through


def test_a_database_with_no_file_writes_no_key_file_anywhere(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    try:
        sms = sms_mod.WindyFlySMS({}, db, wq)
        sms.handle_inbound(PERSON, "STOP")
        again = sms_mod.WindyFlySMS({}, db, wq)
        assert again.is_opted_out(PERSON) and len(_fingerprints(db)) == 1
        assert not list(tmp_path.glob("sms-optout*"))
    finally:
        wq.stop()
        db.close()


# ── (9)(1): the key travels inside the owner's encrypted backup, as its own file ──

def test_a_database_alone_is_packed_as_it_always_was():
    raw = b"SQLite format 3\x00" + b"x" * 100
    assert cb.pack_bundle(raw, {}) == raw
    assert cb.split_bundle(raw) == (raw, {})
    assert cb.split_bundle(b"neither a database nor a bundle") == (b"neither a database nor a bundle", {})


def test_the_key_is_its_own_file_in_the_bundle_and_only_known_plain_files_are_read():
    import io

    raw = b"SQLite format 3\x00" + b"x" * 100
    packed = cb.pack_bundle(raw, {so.KEY_FILE: b"k" * 32, so.PENDING_FILE: b'{"number": "1"}\n'})
    with tarfile.open(fileobj=io.BytesIO(packed)) as tar:
        assert sorted(tar.getnames()) == sorted([cb.BUNDLE_DB, so.KEY_FILE, so.PENDING_FILE])
        assert all(m.mode == 0o600 for m in tar)
    assert cb.split_bundle(packed) == (raw, {so.KEY_FILE: b"k" * 32, so.PENDING_FILE: b'{"number": "1"}\n'})

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, data in ((cb.BUNDLE_DB, raw), ("../../.ssh/authorized_keys", b"x"), (".env", b"SECRET=1")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(so.KEY_FILE)
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tar.addfile(link)
    assert cb.split_bundle(buf.getvalue()) == (raw, {})


@pytest.fixture
def cloud(monkeypatch, tmp_path):
    import httpx
    from unittest.mock import AsyncMock

    sent: dict = {}

    def handler(request):
        sent["body"] = request.read()
        return httpx.Response(200, json={"file_id": "f1"})

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient",
                        lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.example.test")
    monkeypatch.setenv("WINDY_BACKUP_KEY", OWNER_BACKUP_KEY)
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
    monkeypatch.setattr("windyfly.auth.bot_credentials.ecosystem_auth_header",
                        AsyncMock(return_value={"Authorization": "Bearer t"}))
    monkeypatch.setattr("windyfly.auth.bot_credentials.get_bot_key", AsyncMock(return_value=None))
    monkeypatch.setattr("windyfly.agent.cloud_auth.agent_headers", lambda *a, **k: None)
    monkeypatch.setattr("windyfly.trust.gate.require_trust", AsyncMock(return_value=None))
    monkeypatch.setattr(cb, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(cb, "_BACKUP_STATE_FILE", tmp_path / "backup_state.json")
    return sent


def _uploaded_plain(sent: dict) -> tuple[bytes, dict]:
    """The uploaded file opened with the owner's key, and the metadata sent beside it."""
    import re

    body = sent["body"]
    meta = json.loads(re.search(rb'name="metadata"\r\n\r\n(.*?)\r\n--', body, re.S).group(1))
    start = body.index(b"application/octet-stream\r\n\r\n") + len(b"application/octet-stream\r\n\r\n")
    blob = body[start:body.index(b"\r\n--", start)]
    return gzip.decompress(cb._decrypt_data(blob, cb._get_encryption_key())), meta


def test_a_cloud_backup_carries_the_key_inside_the_encryption(chan, cloud):
    import asyncio

    chan["sms"].handle_inbound(PERSON, "STOP")
    key = so.load_key(chan["path"])
    out = asyncio.run(cb.backup_to_cloud({"memory": {"db_path": str(chan["path"])}}))
    assert out["success"], out
    assert key not in cloud["body"]                              # nowhere outside the encryption
    plain, meta = _uploaded_plain(cloud)
    db_bytes, side = cb.split_bundle(plain)
    assert side == {so.KEY_FILE: key} and db_bytes.startswith(b"SQLite format 3\x00")
    assert key not in db_bytes                                   # its own file, never a database column
    assert meta["bundle"] == cb.BUNDLE_FORMAT and meta["size_bytes"] == len(db_bytes)


def test_an_install_that_never_texted_uploads_the_database_alone(env, cloud):
    import asyncio

    db = Database(str(env))
    db.close()
    out = asyncio.run(cb.backup_to_cloud({"memory": {"db_path": str(env)}}))
    assert out["success"], out
    plain, meta = _uploaded_plain(cloud)
    assert plain.startswith(b"SQLite format 3\x00") and "bundle" not in meta


def test_a_restore_brings_the_key_back_so_every_opt_out_still_matches(chan, tmp_path):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    key = so.load_key(chan["path"])
    bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))

    so.key_path(chan["path"]).unlink()                          # a new computer: the memory comes back, no key
    db.execute("DELETE FROM sms_optouts")
    db.commit()
    assert br._restore_into(db, bundle) is None
    assert so.load_key(chan["path"]) == key and _mode(so.key_path(chan["path"])) == 0o600
    back = chan["make"]()
    assert not back.optouts.failed_closed
    with _no_network():
        assert back.send_sms(PERSON, "x")["error"] == "recipient_opted_out"


# ── a restore and an undo never remove an opt-out (Hub on #777, BOARD 2026-11-11T04:00:49Z; Boss 03:37:10Z (b)) ──

THIRD = "+15557776666"


def _earlier_files(chan) -> list[str]:
    return sorted(p.name for p in chan["path"].parent.iterdir() if so.is_earlier_name(p.name))


def test_a_stop_recorded_after_the_backup_survives_the_restore(chan, monkeypatch):
    """The Hub's probe: STOP, back up, another STOP, restore the backup. Both people stay opted out."""
    monkeypatch.setenv("WINDY_BACKUP_KEY", OWNER_BACKUP_KEY)
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    sms.handle_inbound(OTHER, "STOP")
    key = so.load_key(chan["path"])
    br._write_undo(db, cb._get_encryption_key())
    assert br._restore_into(db, old_bundle) is None
    back = chan["make"]()
    assert back.is_opted_out(OTHER) and back.is_opted_out(PERSON) and not back.optouts.failed_closed
    assert len(_fingerprints(db)) == 2                                    # by count: nothing was dropped
    assert so.load_key(chan["path"]) == key                               # the same key: still the one in use
    with _no_network():
        assert back.send_sms(OTHER, "x")["error"] == "recipient_opted_out"


def test_a_stop_recorded_between_a_restore_and_its_undo_survives_the_undo(chan, monkeypatch):
    monkeypatch.setenv("WINDY_BACKUP_KEY", OWNER_BACKUP_KEY)
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    sms.handle_inbound(OTHER, "STOP")
    owner_key = cb._get_encryption_key()
    br._write_undo(db, owner_key)
    assert br._restore_into(db, old_bundle) is None
    chan["make"]().handle_inbound(THIRD, "STOP")                          # after the restore, before the undo
    _at, blob = br._read_undo(db)
    assert br._restore_into(db, cb._decrypt_data(blob, owner_key)) is None
    undone = chan["make"]()
    assert all(undone.is_opted_out(n) for n in (PERSON, OTHER, THIRD)) and len(_fingerprints(db)) == 3


def test_a_restore_under_another_key_keeps_both_keys_and_undo_keeps_every_opt_out(chan, monkeypatch):
    monkeypatch.setenv("WINDY_BACKUP_KEY", OWNER_BACKUP_KEY)
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_key = so.load_key(chan["path"])
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))

    sms.handle_inbound(PERSON, "START")                         # today: an empty record under a NEW key
    so.key_path(chan["path"]).unlink()
    today = chan["make"]()
    today.handle_inbound(OTHER, "STOP")
    new_key = so.load_key(chan["path"])
    assert new_key != old_key

    owner_key = cb._get_encryption_key()
    br._write_undo(db, owner_key)                               # what /backup yes does before it restores
    assert br._restore_into(db, old_bundle) is None
    assert so.load_key(chan["path"]) == old_key                 # the restored memory's key is the one in use
    assert _earlier_files(chan) == [so.earlier_name(new_key)]   # and today's key is kept beside it
    restored = chan["make"]()
    assert restored.is_opted_out(PERSON) and restored.is_opted_out(OTHER) and not restored.optouts.failed_closed
    assert sorted(n for n in _rows(db) if so.is_check(n)) == sorted([so.key_check(old_key), so.key_check(new_key)])
    assert so.state_readonly(OTHER, str(chan["path"])) == so.OPTED_OUT     # the /sms path reads both keys too

    _at, blob = br._read_undo(db)
    undo = cb._decrypt_data(blob, owner_key)
    assert cb.split_bundle(undo)[1] == {so.KEY_FILE: new_key}   # the undo copy holds the key of ITS memory
    assert br._restore_into(db, undo) is None
    assert so.load_key(chan["path"]) == new_key
    undone = chan["make"]()
    assert undone.is_opted_out(OTHER) and undone.is_opted_out(PERSON) and not undone.optouts.failed_closed
    assert _earlier_files(chan) == sorted([so.earlier_name(new_key), so.earlier_name(old_key)])


def test_start_clears_an_opt_out_made_under_an_earlier_key(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    sms.handle_inbound(PERSON, "START")
    so.key_path(chan["path"]).unlink()
    chan["make"]().handle_inbound(OTHER, "STOP")                # under a new key
    assert br._restore_into(db, old_bundle) is None             # OTHER's fingerprint now sits under an earlier key
    back = chan["make"]()
    assert back.is_opted_out(OTHER)
    back.handle_inbound(OTHER, "START")
    assert not back.is_opted_out(OTHER) and back.is_opted_out(PERSON)
    back.handle_inbound(THIRD, "STOP")                          # new opt-outs use the key in use
    assert so.fingerprint(so.load_key(chan["path"]), THIRD) in _rows(db)


def test_losing_an_earlier_key_fails_closed(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    sms.handle_inbound(PERSON, "START")
    so.key_path(chan["path"]).unlink()
    chan["make"]().handle_inbound(OTHER, "STOP")
    assert br._restore_into(db, old_bundle) is None
    (name,) = _earlier_files(chan)
    (chan["path"].parent / name).unlink()                       # some fingerprints can no longer be matched
    lost = chan["make"]()
    assert lost.optouts.failed_closed
    assert so.state_readonly(THIRD, str(chan["path"])) == so.KEY_MISSING
    with _no_network(), patch("windyfly.agent.owner_mail.send_sync"):
        assert lost.send_sms(THIRD, "x")["error"] == "optout_key_missing"


def test_earlier_keys_travel_in_the_backup_and_come_back_on_a_new_computer(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    sms.handle_inbound(PERSON, "START")
    so.key_path(chan["path"]).unlink()
    chan["make"]().handle_inbound(OTHER, "STOP")
    assert br._restore_into(db, old_bundle) is None
    side = so.backup_files(chan["path"])
    (earlier,) = [n for n in side if so.is_earlier_name(n)]
    bundle = cb.pack_bundle(br._serialize(db), side)
    assert cb.split_bundle(bundle)[1] == side                   # the bundle reader takes the earlier-key name

    for p in list(chan["path"].parent.iterdir()):               # a new computer: no key file of any kind
        if p.name.startswith("sms-optout"):
            p.unlink()
    db.execute("DELETE FROM sms_optouts")
    db.commit()
    assert br._restore_into(db, bundle) is None
    back = chan["make"]()
    assert back.is_opted_out(PERSON) and back.is_opted_out(OTHER) and not back.optouts.failed_closed
    assert _earlier_files(chan) == [earlier]


def test_only_real_earlier_key_names_are_read_from_a_bundle():
    import io

    raw = b"SQLite format 3\x00" + b"x" * 100
    key = b"e" * 32
    good = so.earlier_name(key)
    buf = io.BytesIO()
    with tarfile.TarFile(fileobj=buf, mode="w") as tar:
        for name, data in ((cb.BUNDLE_DB, raw), (good, key), ("sms-optout-earlier-zzzz.key", key),
                           ("x/sms-optout-earlier-0123456789abcdef.key", key),
                           ("sms-optout-earlier-0123456789abcdef.key.sh", key)):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    assert cb.split_bundle(buf.getvalue()) == (raw, {good: key})
    assert so.is_earlier_name(good) and not so.is_earlier_name("sms-optout-earlier-0123456789ABCDEF.key")


def test_an_earlier_key_file_whose_name_does_not_fit_its_key_is_ignored(chan, tmp_path):
    other = b"q" * 32
    so.restore_files(chan["path"], {so.earlier_name(b"e" * 32): other})   # a file named after another key
    assert _earlier_files(chan) == [] and so.load_earlier_keys(chan["path"]) == []


def test_a_restore_with_no_opt_outs_on_either_side_writes_no_key_file(env, tmp_path):
    db = Database(str(env))
    try:
        raw = br._serialize(db)
        assert br._restore_into(db, raw) is None
        assert not [p for p in env.parent.iterdir() if p.name.startswith("sms-optout")]
    finally:
        db.close()


def test_if_the_live_record_cannot_be_carried_nothing_is_restored(chan, monkeypatch):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    sms.handle_inbound(OTHER, "STOP")
    before = sorted(_rows(db))
    monkeypatch.setattr(so, "keep_earlier_key", lambda *a: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(br._CopyFailed):
        br._restore_into(db, old_bundle)
    assert sorted(_rows(db)) == before and chan["make"]().is_opted_out(OTHER)


def test_a_key_file_that_cannot_be_written_after_the_copy_is_reported_not_raised(chan, monkeypatch):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    bundle = cb.pack_bundle(br._serialize(db), so.backup_files(chan["path"]))
    monkeypatch.setattr(so, "restore_files", lambda *a: (_ for _ in ()).throw(OSError("read-only")))
    assert br._restore_into(db, bundle) == "do-not-text key"


def test_a_leftover_tmp_file_does_not_loosen_the_key(chan):
    path = so.key_path(chan["path"])
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(b"old")
    os.chmod(tmp, 0o644)
    so.write_key(chan["path"], b"n" * 32)
    assert _mode(path) == 0o600 and path.read_bytes() == b"n" * 32


def test_a_restored_older_memory_without_its_key_fails_closed_rather_than_matching_nobody(chan):
    sms, db = chan["sms"], chan["db"]
    sms.handle_inbound(PERSON, "STOP")
    old_memory = br._serialize(db)                              # a backup made by code that did not carry the key
    sms.handle_inbound(PERSON, "START")
    so.key_path(chan["path"]).unlink()
    chan["make"]()                                              # a new key today
    assert br._restore_into(db, old_memory) is None
    assert chan["make"]().optouts.failed_closed                 # the check row says: not this key


def test_pending_stops_travel_with_a_backup_and_are_merged(chan):
    chan["sms"].handle_inbound(PERSON, "STOP")
    key = _lose_the_key(chan)
    chan["make"]().handle_inbound(OTHER, "STOP")
    side = so.backup_files(chan["path"])
    assert set(side) == {so.PENDING_FILE}                       # no key to carry, but the kept STOP goes along
    so.pending_path(chan["path"]).unlink()
    chan["make"]().handle_inbound("+15557776666", "STOP")
    so.restore_files(chan["path"], {**side, so.KEY_FILE: key})
    back = chan["make"]()
    assert back.is_opted_out(OTHER) and back.is_opted_out("+15557776666") and back.is_opted_out(PERSON)
    assert not so.pending_path(chan["path"]).exists()


@pytest.mark.asyncio
async def test_a_local_export_holds_the_key_beside_the_memory(chan, tmp_path, monkeypatch):
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry

    chan["sms"].handle_inbound(PERSON, "STOP")
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(core_mod, "PROJECT_ROOT", install)
    monkeypatch.setattr(core_mod, "_config", {"memory": {"db_path": str(chan["path"])}})
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    monkeypatch.chdir(tmp_path)
    assert "Backup saved" in await registry.get("export").handler({})
    (archive,) = tmp_path.glob("windyfly-backup-*.tar.gz")
    with tarfile.open(archive) as tar:
        assert "data/windyfly.db" in tar.getnames()
        member = tar.getmember(f"data/{so.KEY_FILE}")
        assert member.mode == 0o600 and tar.extractfile(member).read() == so.load_key(chan["path"])


@pytest.mark.asyncio
async def test_a_local_export_also_holds_earlier_keys_and_the_pending_list(chan, tmp_path, monkeypatch):
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry

    earlier = b"e" * 32
    so.keep_earlier_key(chan["path"], earlier)
    so._write_pending(chan["path"], [{"number": "15557776666", "at": "t"}])
    (chan["path"].parent / "sms-optout-notes.txt").write_text("not ours", encoding="utf-8")
    install = tmp_path / "install"
    install.mkdir()
    monkeypatch.setattr(core_mod, "PROJECT_ROOT", install)
    monkeypatch.setattr(core_mod, "_config", {"memory": {"db_path": str(chan["path"])}})
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    monkeypatch.chdir(tmp_path)
    assert "Backup saved" in await registry.get("export").handler({})
    (archive,) = tmp_path.glob("windyfly-backup-*.tar.gz")
    with tarfile.open(archive) as tar:
        names = set(tar.getnames())
        assert {f"data/{so.KEY_FILE}", f"data/{so.earlier_name(earlier)}", f"data/{so.PENDING_FILE}"} <= names
        assert "data/sms-optout-notes.txt" not in names
        assert tar.extractfile(f"data/{so.earlier_name(earlier)}").read() == earlier


def test_the_cli_export_holds_the_same_side_files(chan, tmp_path, monkeypatch):
    from argparse import Namespace

    from windyfly.commands import _legacy

    chan["sms"].handle_inbound(PERSON, "STOP")
    so.keep_earlier_key(chan["path"], b"e" * 32)
    chan["db"].execute("PRAGMA wal_checkpoint(TRUNCATE)")
    out = tmp_path / "cli"
    out.mkdir()
    monkeypatch.setattr(_legacy, "PROJECT_ROOT", out)
    monkeypatch.setattr(_legacy, "_get_db_path", lambda: chan["path"])
    _legacy.cmd_export(Namespace())
    (archive,) = out.glob("windyfly-backup-*.tar.gz")
    with tarfile.open(archive) as tar:
        assert {f"data/{so.KEY_FILE}", f"data/{so.earlier_name(b'e' * 32)}"} <= set(tar.getnames())
