"""The cloud-backup due-check runs hourly (a restart no longer resets a 24 h sleep) and only in the agent's
maintenance-runner process (telegram + matrix share one DB: one backup must not be uploaded twice).
Windy 0, 10-21: gaps of 34-48 h, and the same backup twice in Cloud's 5 slots."""
import threading

import pytest

from windyfly import main as wf_main


@pytest.mark.parametrize("channel,token,expected", [
    ("telegram", "t", True), ("matrix", "t", False),     # Zero: telegram is the runner
    ("matrix", None, True), ("telegram", None, False),   # matrix-only agent
    ("cli", "t", True), ("discord", None, True),         # other processes unchanged
])
def test_only_the_maintenance_runner_backs_up(monkeypatch, channel, token, expected):
    monkeypatch.delenv("WINDY_MAINTENANCE_RUNNER", raising=False)
    if token:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    else:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert wf_main._backs_up(channel) is expected


class _Stop(Exception):
    pass


def _run_loop(monkeypatch, tmp_path, channel, iterations=3):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.delenv("WINDY_MAINTENANCE_RUNNER", raising=False)
    calls = {"decay": 0, "backup": 0, "sleeps": []}
    monkeypatch.setattr("windyfly.memory.decay.run_decay", lambda *a, **k: calls.__setitem__("decay", calls["decay"] + 1) or {})
    monkeypatch.setattr("windyfly.personality.versioning.run_periodic_drift_check", lambda *a, **k: {"drift_detected": False})
    monkeypatch.setattr("windyfly.skills.curator.run_curation", lambda *a, **k: {})
    monkeypatch.setattr("windyfly.memory.retention.run_retention", lambda *a, **k: {})

    async def backup(config):
        calls["backup"] += 1
        return {"success": True}

    monkeypatch.setattr("windyfly.cloud_backup.run_backup_if_due", backup)

    def sleep(seconds):
        calls["sleeps"].append(seconds)
        if len(calls["sleeps"]) >= iterations:
            raise _Stop()

    monkeypatch.setattr(wf_main.time, "sleep", sleep)
    monkeypatch.setattr(threading, "excepthook", lambda args: None)
    t = wf_main._start_decay_scheduler({}, str(tmp_path / "t.db"), channel)
    t.join(timeout=10)
    assert not t.is_alive()
    return calls


def test_the_runner_checks_the_backup_every_hour_and_runs_the_daily_cycle_once(monkeypatch, tmp_path):
    calls = _run_loop(monkeypatch, tmp_path, "telegram")
    assert calls["backup"] == 3 and calls["decay"] == 1
    assert calls["sleeps"] == [wf_main._BACKUP_CHECK_SECONDS] * 3 and wf_main._BACKUP_CHECK_SECONDS == 3600


def test_the_other_channel_process_never_uploads(monkeypatch, tmp_path):
    calls = _run_loop(monkeypatch, tmp_path, "matrix")
    assert calls["backup"] == 0 and calls["decay"] == 1
