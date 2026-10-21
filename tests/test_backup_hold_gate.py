"""Scheduled-backup safety (Hub 10-21, after Zero's memory collapse sent near-empty backups to Cloud):
a 6 h backoff after a failure; a memory under half the last good backup's episodes is HELD (one notice a day)
until the owner accepts it, or after 72 h uploads marked suspect ONLY when that switch is on."""
import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from windyfly import cloud_backup as cb

HOUR = 3600


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "memory.db"
    state_file = tmp_path / "backup_state.json"
    monkeypatch.setattr(cb, "_BACKUP_STATE_FILE", state_file)
    monkeypatch.setattr(cb, "_get_cloud_token", lambda: "t")
    monkeypatch.delenv(cb.SUSPECT_UPLOAD_ENV, raising=False)
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(cb.time, "time", lambda: clock["now"])
    uploads, notices = [], []

    async def fake_upload(config=None, *, suspect=False):
        uploads.append(suspect)
        if fake_upload.fail:
            return {"success": False, "error": "Upload failed: 503"}
        cb._save_backup_state({"last_backup": datetime.now(timezone.utc).isoformat(), "backup_id": "x",
                               "episodes": cb.episode_count(db), **({"suspect": True} if suspect else {})})
        return {"success": True}
    fake_upload.fail = False
    monkeypatch.setattr(cb, "backup_to_cloud", fake_upload)

    async def fake_send(subject, body):
        notices.append(body)
        return True
    monkeypatch.setattr("windyfly.agent.owner_mail.send", fake_send)
    config = {"memory": {"db_path": str(db)}}

    def memory(n):
        con = sqlite3.connect(db)
        con.execute("DROP TABLE IF EXISTS episodes")
        con.execute("CREATE TABLE episodes (id INTEGER PRIMARY KEY)")
        con.executemany("INSERT INTO episodes VALUES (?)", [(i,) for i in range(n)])
        con.commit()
        con.close()

    def last_backup(episodes, hours_ago=30):
        when = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
        state_file.write_text(json.dumps({"last_backup": when, "backup_id": "old", "episodes": episodes}))

    def run():
        return asyncio.run(cb.run_backup_if_due(config))

    return {"memory": memory, "last_backup": last_backup, "run": run, "uploads": uploads, "notices": notices,
            "clock": clock, "upload": fake_upload, "config": config}


def test_small_agents_are_never_held(env):
    env["memory"](1)
    env["last_backup"](10)  # under the 20-episode floor
    env["run"]()
    assert env["uploads"] == [False] and env["notices"] == []


def test_a_collapse_is_held_with_one_notice_a_day(env):
    env["memory"](10)
    env["last_backup"](100)
    assert env["run"]() is None and env["uploads"] == []
    env["clock"]["now"] += HOUR
    env["run"]()
    assert len(env["notices"]) == 1 and "10 episodes" in env["notices"][0] and "had 100" in env["notices"][0]
    env["clock"]["now"] += 24 * HOUR
    env["run"]()
    assert len(env["notices"]) == 2 and env["uploads"] == []
    assert cb.hold_status()["baseline"] == 100


def test_hold_then_accept_then_upload_becomes_the_new_baseline(env):
    env["memory"](10)
    env["last_backup"](100)
    env["run"]()
    assert cb.accept_memory(env["config"]) == 10
    env["run"]()
    assert env["uploads"] == [False]
    state = cb.get_backup_state()
    assert state["episodes"] == 10 and "hold" not in state and "accept_episodes" not in state


def test_after_72h_it_stays_held_unless_the_suspect_switch_is_on(env, monkeypatch):
    env["memory"](10)
    env["last_backup"](100)
    env["run"]()
    env["clock"]["now"] += 73 * HOUR
    env["run"]()
    assert env["uploads"] == []  # Cloud's suspect rule not live: never send suspect=true
    monkeypatch.setenv(cb.SUSPECT_UPLOAD_ENV, "1")
    env["run"]()
    assert env["uploads"] == [True] and cb.get_backup_state().get("suspect") is True


def test_a_failed_upload_backs_off_six_hours(env):
    env["memory"](50)
    env["last_backup"](50)
    env["upload"].fail = True
    env["run"]()
    env["clock"]["now"] += 5 * HOUR
    env["run"]()
    assert env["uploads"] == [False]  # still backing off
    env["clock"]["now"] += 2 * HOUR
    env["run"]()
    assert env["uploads"] == [False, False]


def test_a_healthy_memory_uploads_and_clears_an_old_hold(env):
    env["memory"](90)
    env["last_backup"](100)
    env["run"]()
    assert env["uploads"] == [False] and cb.hold_status() is None


@pytest.fixture
def commands(env, monkeypatch):
    from windyfly.commands.core import wire_runtime
    from windyfly.commands.setup import init_all_commands
    from windyfly.memory.database import Database

    database = Database(":memory:")
    init_all_commands(db=database, config=env["config"])
    wire_runtime(db=database)
    yield
    database.close()


def _cmd(text):
    from windyfly.channels.base import handle_incoming

    ok, out = asyncio.run(handle_incoming(text, {"platform": "cli", "channel_id": "x"}))  # /backup: owner's terminal
    assert ok is True
    return out


def test_backup_now_backs_up_the_configured_memory_not_the_default_file(env, commands, monkeypatch):
    seen = []

    async def upload(config=None, *, suspect=False):
        seen.append(config)
        return {"success": True, "backup_id": "x", "size_bytes": 1}

    monkeypatch.setattr(cb, "backup_to_cloud", upload)
    _cmd("/backup now")
    assert seen == [env["config"]] and cb._db_path(seen[0]).name == "memory.db"


def test_the_owner_sees_the_hold_and_accepts_it(env, commands):
    env["memory"](10)
    env["last_backup"](100)
    env["run"]()
    assert "ON HOLD" in _cmd("/backup") and "10 episodes" in _cmd("/backup")
    assert "Accepted: 10 episodes" in _cmd("/backup accept")
    env["run"]()
    assert env["uploads"] == [False] and "ON HOLD" not in _cmd("/backup")


def test_accept_without_a_hold_does_nothing(env, commands):
    assert "No backup is on hold" in _cmd("/backup accept")
