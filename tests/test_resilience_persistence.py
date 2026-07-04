"""Circuit-breaker persistence + write-failure telemetry (2026-07-04 audit).

Two invisibility bugs:
1. `_provider_cooldowns` was a module dict — every restart (including the
   crash-restart loops the cooldowns exist to protect against) zeroed the
   breaker, so a dead provider got hammered afresh each cycle.
2. WriteQueue failures were logged and dropped — a full disk meant total
   silent memory loss while chat kept working.
"""

from __future__ import annotations

import json
import time

from windyfly.agent import models
from windyfly.memory.write_queue import (
    WriteQueue,
    get_write_stats,
    reset_write_stats,
)


class TestCooldownPersistence:
    def test_failure_persists_to_state_file(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        models._record_provider_failure("testprov", "503 unavailable")
        saved = json.loads((tmp_path / "provider-cooldowns.json").read_text())
        assert "testprov" in saved
        until, count = saved["testprov"]
        assert until > time.time()
        assert count == 1

    def test_load_restores_unexpired_and_drops_stale(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
        state = {
            "alive": [time.time() + 600, 3],
            "expired": [time.time() - 600, 5],
            "garbage": "not-a-tuple",
        }
        (tmp_path / "provider-cooldowns.json").write_text(json.dumps(state))
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        models._load_cooldowns()
        assert "alive" in models._provider_cooldowns
        assert models._provider_cooldowns["alive"][1] == 3
        assert "expired" not in models._provider_cooldowns
        assert "garbage" not in models._provider_cooldowns

    def test_success_clears_persisted_entry(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        models._record_provider_failure("testprov", "503")
        models._record_provider_success("testprov")
        saved = json.loads((tmp_path / "provider-cooldowns.json").read_text())
        assert saved == {}

    def test_missing_state_file_is_harmless(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "nope"))
        monkeypatch.setattr(models, "_provider_cooldowns", {})
        models._load_cooldowns()  # must not raise
        assert models._provider_cooldowns == {}


class TestWriteFailureTelemetry:
    def test_failed_write_increments_stats(self):
        reset_write_stats()

        def _boom():
            raise OSError("disk full")

        wq = WriteQueue()
        wq.start()
        wq.enqueue(0, _boom)
        wq.stop()

        stats = get_write_stats()
        assert stats["failures"] == 1
        assert "disk full" in stats["last_error"]
        assert stats["last_failure_ts"] > 0
        reset_write_stats()

    def test_successful_write_leaves_stats_clean(self):
        reset_write_stats()
        wq = WriteQueue()
        wq.start()
        wq.enqueue(0, lambda: None)
        wq.stop()
        assert get_write_stats()["failures"] == 0
