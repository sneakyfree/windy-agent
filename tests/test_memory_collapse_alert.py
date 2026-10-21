"""A memory file set aside at startup is announced AT ONCE (Windy 0 10-19: two days unnoticed): a marker at the
moment of quarantine (names and sizes only), claimed by ONE process, which logs ERROR, mails the owner once and
appends one BOARD line when WINDY_INCIDENT_BOARD_FILE is set."""
import json
import logging
import threading

from windyfly.memory import collapse_alert as ca
from windyfly.memory.database import Database

SECRET = b"owner diary: do not leak this sentence"


def _collapse(tmp_path):
    db = tmp_path / "windy-0.db"
    db.write_bytes(SECRET * 200)  # not a database
    d = Database(str(db))
    d.conn.close()
    return db


def test_quarantine_leaves_a_marker_with_names_and_sizes_only(tmp_path):
    db = _collapse(tmp_path)
    m = ca.marker_path(db)
    data = json.loads(m.read_text())
    assert data["file"] == "windy-0.db" and data["moved_to"].startswith("windy-0.db.corrupt-")
    assert data["moved_bytes"] == len(SECRET) * 200
    assert SECRET.decode() not in m.read_text()


def test_announce_once_board_line_and_one_mail(tmp_path, monkeypatch, caplog):
    db = _collapse(tmp_path)
    board = tmp_path / "BOARD.md"
    board.write_text("")
    monkeypatch.setenv(ca.ENV_BOARD, str(board))
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.invalid")
    monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.example.invalid")
    mails = []
    monkeypatch.setattr("windyfly.agent.owner_mail.send_sync", lambda subject, body: mails.append((subject, body)) or True)
    monkeypatch.setattr(ca, "_last_backup", lambda: {"last_backup": "2026-10-18T15:05:37", "episodes": 31257})
    with caplog.at_level(logging.ERROR, logger="windyfly.memory.collapse_alert"):
        assert ca.announce(db) is True
        assert ca.announce(db) is False  # claimed: nobody announces twice
    lines = board.read_text().splitlines()
    assert len(lines) == 1 and "memory collapse" in lines[0] and "windy-0.db.corrupt-" in lines[0]
    assert "episodes=31257" in lines[0] and SECRET.decode() not in board.read_text()
    assert len(mails) == 1 and "FRESH, empty memory" in mails[0][1]
    assert sum("MEMORY COLLAPSE" in r.getMessage() for r in caplog.records) == 1
    assert not ca.marker_path(db).exists()


def test_no_marker_means_nothing_happens(tmp_path, monkeypatch):
    mails = []
    monkeypatch.setattr("windyfly.agent.owner_mail.send_sync", lambda s, b: mails.append(1))
    assert ca.announce(tmp_path / "windy-0.db") is False and mails == []


def test_writing_the_marker_never_stops_the_agent(tmp_path):
    ca.write_marker(tmp_path / "nodir" / "x.db", tmp_path / "missing", "boom")  # unwritable path: no raise


def test_the_maintenance_loop_announces_right_after_opening_the_db(tmp_path, monkeypatch):
    from windyfly import main as wf_main

    seen = []
    monkeypatch.setattr(ca, "announce", lambda p: seen.append(str(p)) or False)
    for name in ("windyfly.memory.decay.run_decay", "windyfly.skills.curator.run_curation",
                 "windyfly.memory.retention.run_retention"):
        monkeypatch.setattr(name, lambda *a, **k: {})
    monkeypatch.setattr("windyfly.personality.versioning.run_periodic_drift_check", lambda *a, **k: {"drift_detected": False})

    class Stop(Exception):
        pass

    def stop(_s):
        raise Stop()

    monkeypatch.setattr(wf_main.time, "sleep", stop)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    db = str(tmp_path / "t.db")
    t = wf_main._start_decay_scheduler({}, db)
    t.join(timeout=10)
    assert seen == [db]


def test_a_configured_mail_that_fails_is_retried_at_the_next_start(tmp_path, monkeypatch):
    db = _collapse(tmp_path)
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.invalid")
    monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.example.invalid")
    tries = []
    monkeypatch.setattr("windyfly.agent.owner_mail.send_sync", lambda s, b: tries.append(1) or len(tries) > 1)
    assert ca.announce(db) is True and ca.marker_path(db).exists()  # mail failed: the marker is back
    assert ca.announce(db) is True and not ca.marker_path(db).exists()  # next start: sent, claimed for good
    assert len(tries) == 2


def test_without_mail_configured_the_marker_is_not_put_back(tmp_path, monkeypatch):
    db = _collapse(tmp_path)
    monkeypatch.delenv("OWNER_EMAIL", raising=False)
    assert ca.announce(db) is True and not ca.marker_path(db).exists()


def test_a_mail_retry_does_not_repeat_the_board_line(tmp_path, monkeypatch):
    db = _collapse(tmp_path)
    board = tmp_path / "BOARD.md"
    board.write_text("")
    monkeypatch.setenv(ca.ENV_BOARD, str(board))
    monkeypatch.setenv("OWNER_EMAIL", "owner@example.invalid")
    monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.example.invalid")
    tries = []
    monkeypatch.setattr("windyfly.agent.owner_mail.send_sync", lambda s, b: tries.append(1) or len(tries) > 2)
    for _ in range(3):
        ca.announce(db)
    assert len(tries) == 3 and len(board.read_text().splitlines()) == 1
