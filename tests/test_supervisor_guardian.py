"""The Guardian — cross-platform wedge-catcher (Tier 3, 2026-07-18)."""
from __future__ import annotations

import time

from windyfly.supervisor import heartbeat as hb
from windyfly.supervisor.guardian import (
    GuardianConfig, GuardianState, check_health, tick,
)


def _write_hb(tmp_path, channel, *, age_s=0.0, pid=None, polling=True):
    import json, os
    p = tmp_path / f"heartbeat-{channel}.json"
    p.write_text(json.dumps({
        "ts": time.time() - age_s,
        "pid": pid if pid is not None else os.getpid(),
        "channel": channel, "polling": polling,
    }))


class TestHeartbeatFile:
    def test_roundtrip_and_age(self, tmp_path):
        hb.write_heartbeat("telegram", pid=1234, polling=True, state_dir=tmp_path)
        got = hb.read_heartbeat("telegram", state_dir=tmp_path)
        assert got["pid"] == 1234 and got["polling"] is True
        age = hb.heartbeat_age("telegram", state_dir=tmp_path)
        assert age is not None and age < 5

    def test_absent_is_none(self, tmp_path):
        assert hb.read_heartbeat("nope", state_dir=tmp_path) is None
        assert hb.heartbeat_age("nope", state_dir=tmp_path) is None


class TestHealth:
    def _cfg(self, tmp_path, **kw):
        return GuardianConfig(
            channels=["telegram"], state_dir_override=str(tmp_path), **kw,
        )

    def test_fresh_heartbeat_healthy(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=10)  # own live pid
        assert check_health(self._cfg(tmp_path)).healthy is True

    def test_stale_heartbeat_unhealthy(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=99999)
        r = check_health(self._cfg(tmp_path))
        assert r.healthy is False and "stale" in r.detail

    def test_dead_pid_unhealthy(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=5, pid=2)  # pid 2 not ours; likely dead
        # Use a pid that is definitely dead: a huge unlikely pid
        _write_hb(tmp_path, "telegram", age_s=5, pid=999999)
        r = check_health(self._cfg(tmp_path))
        assert r.healthy is False and "dead" in r.detail

    def test_polling_dead_flag_unhealthy(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=5, polling=False)
        r = check_health(self._cfg(tmp_path))
        assert r.healthy is False and "polling_dead" in r.detail

    def test_no_signal_conservative_pass(self, tmp_path):
        # No heartbeat, no external probe → don't kill what we can't see
        assert check_health(self._cfg(tmp_path)).healthy is True

    def test_external_probe_failure(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=5)
        cfg = self._cfg(tmp_path, external_probe=lambda: (False, "getMe_401"))
        r = check_health(cfg)
        assert r.healthy is False and "getMe_401" in r.detail

    def test_external_probe_error_does_not_wedge(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=5)
        def boom():
            raise RuntimeError("network")
        cfg = self._cfg(tmp_path, external_probe=boom)
        r = check_health(cfg)
        assert r.healthy is False and "probe_error" in r.detail


class TestTickRestartDiscipline:
    def _cfg(self, tmp_path):
        return GuardianConfig(channels=["telegram"], fail_limit=3,
                              state_dir_override=str(tmp_path))

    def test_healthy_keeps_counter_zero(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=5)
        st = tick(self._cfg(tmp_path), GuardianState(), lambda: True)
        assert st.consecutive_fails == 0 and st.last_result.startswith("OK")

    def test_restart_fires_at_limit(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=99999)
        cfg = self._cfg(tmp_path)
        calls = []
        rf = lambda: (calls.append(1), True)[1]
        st = GuardianState()
        for _ in range(3):
            st = tick(cfg, st, rf)
        assert len(calls) == 1
        assert "RESTART_TRIGGERED" in st.last_result

    def test_failed_restart_keeps_retrying(self, tmp_path):
        _write_hb(tmp_path, "telegram", age_s=99999)
        cfg = self._cfg(tmp_path)
        calls = []
        rf = lambda: (calls.append(1), False)[1]  # restart never takes
        st = GuardianState()
        for _ in range(6):
            st = tick(cfg, st, rf)
        # parked one below limit → re-fires every tick after the first
        assert len(calls) >= 3
        assert "RESTART_FAILED" in st.last_result

    def test_recovery_resets_counter(self, tmp_path):
        cfg = self._cfg(tmp_path)
        _write_hb(tmp_path, "telegram", age_s=99999)
        st = GuardianState()
        for _ in range(2):
            st = tick(cfg, st, lambda: True)
        assert st.consecutive_fails == 2
        _write_hb(tmp_path, "telegram", age_s=5)  # agent healthy again
        st = tick(cfg, st, lambda: True)
        assert st.consecutive_fails == 0


class TestHeartbeatReadRetry:
    """Windows-campaign finding 2026-07-18: a torn read (writer mid
    os.replace) returned None for one tick. Retry-once seals it; a
    truly-absent file still short-circuits to None without retrying."""

    def test_absent_file_returns_none_fast(self, tmp_path, monkeypatch):
        slept = []
        monkeypatch.setattr("time.sleep", lambda s: slept.append(s))
        assert hb.read_heartbeat("ghost", state_dir=tmp_path) is None
        assert slept == []  # no retry for a missing file

    def test_transient_read_error_retries_then_succeeds(self, tmp_path, monkeypatch):
        hb.write_heartbeat("telegram", pid=7, state_dir=tmp_path)
        import pathlib
        calls = {"n": 0}
        real_read = pathlib.Path.read_text

        def flaky(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise PermissionError("sharing violation")  # torn read
            return real_read(self, *a, **k)

        monkeypatch.setattr(pathlib.Path, "read_text", flaky)
        monkeypatch.setattr("time.sleep", lambda s: None)
        got = hb.read_heartbeat("telegram", state_dir=tmp_path)
        assert got is not None and got["pid"] == 7
        assert calls["n"] == 2  # failed once, retried, succeeded
