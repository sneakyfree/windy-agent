"""The Matrix channel must back off when /sync fails, and say so.

Measured on Windy 0, 2026-09-03 → 09-05: with chat.windychat.ai returning
502, nio's sync_forever retried every ~135 ms (811,105 failed syncs in one
day) while the heartbeat reported polling=true and the guardian said OK.
These tests pin the fix: exponential sleep inside the SyncError callback,
escape to the reconnect loop after a run of failures, an honest sync_ok bit
in the heartbeat file, and joining the hatch DM room on login.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels.matrix_bot import (
    _SYNC_FAILS_BEFORE_RECONNECT,
    MatrixSyncDead,
    WindyFlyMatrixBot,
)
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.supervisor.guardian import GuardianConfig, check_health
from windyfly.supervisor.heartbeat import read_heartbeat, write_heartbeat


def _config() -> dict:
    return {
        "agent": {"default_model": "x", "max_context_tokens": 8000,
                  "max_response_tokens": 2000, "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20,
                   "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md"},
        "matrix": {"homeserver": "https://chat.windychat.ai",
                   "bot_user": "@windyfly:chat.windychat.ai",
                   "store_path": "data/matrix_store_test"},
    }


def _bot() -> WindyFlyMatrixBot:
    return WindyFlyMatrixBot(_config(), Database(":memory:"), WriteQueue())


def _sync_error(status: int | None = 502, message: str = "Bad Gateway"):
    err = MagicMock()
    err.status_code = status
    err.message = message
    return err


class TestSyncErrorBackoff:
    @pytest.mark.asyncio
    async def test_sleeps_exponentially_then_escapes(self):
        bot = _bot()
        sleeps: list[float] = []

        async def fake_sleep(s):
            sleeps.append(s)

        with patch("windyfly.channels.matrix_bot.asyncio.sleep", fake_sleep):
            for _ in range(_SYNC_FAILS_BEFORE_RECONNECT - 1):
                await bot._on_sync_error(_sync_error())
            with pytest.raises(MatrixSyncDead) as exc:
                await bot._on_sync_error(_sync_error())

        assert sleeps == [1, 2, 4, 8, 16, 32, 60]
        assert "502" in str(exc.value)
        assert bot._sync_failures == _SYNC_FAILS_BEFORE_RECONNECT
        assert bot._sync_healthy() is False

    @pytest.mark.asyncio
    async def test_401_escape_routes_to_relogin(self):
        """The escape exception must carry the status so the reconnect
        loop's token-expiry check can trigger a password re-login."""
        bot = _bot()
        with patch("windyfly.channels.matrix_bot.asyncio.sleep", AsyncMock()):
            for _ in range(_SYNC_FAILS_BEFORE_RECONNECT - 1):
                await bot._on_sync_error(_sync_error(401, "Invalid access token"))
            with pytest.raises(MatrixSyncDead) as exc:
                await bot._on_sync_error(_sync_error(401, "Invalid access token"))
        assert bot._is_token_expired_error(exc.value)

    @pytest.mark.asyncio
    async def test_success_resets_failure_run_and_backoff(self):
        bot = _bot()
        bot._backoff = 32
        with patch("windyfly.channels.matrix_bot.asyncio.sleep", AsyncMock()):
            await bot._on_sync_error(_sync_error())
            await bot._on_sync_error(_sync_error())
        assert bot._sync_failures == 2
        await bot._on_sync_response(MagicMock())
        assert bot._sync_failures == 0
        assert bot._backoff == 1
        assert bot._last_sync_success > 0
        assert bot._sync_healthy() is True

    def test_stale_sync_is_unhealthy(self):
        bot = _bot()
        bot._last_sync_success = time.time() - 700
        assert bot._sync_healthy() is False
        bot._last_sync_success = time.time() - 60
        assert bot._sync_healthy() is True

    def test_never_synced_tolerates_short_run(self):
        bot = _bot()
        assert bot._sync_healthy() is True
        bot._sync_failures = 3
        assert bot._sync_healthy() is False

    @pytest.mark.asyncio
    async def test_outage_stays_unhealthy_across_reconnect_cycles(self):
        """Measured on Windy 0 2026-09-05 16:25Z after the first deploy: the
        escape resets the per-run counter, so a heartbeat written right
        after a reconnect said sync_ok=true in the middle of a 2-day
        outage. The outage start must survive the reset."""
        bot = _bot()
        with patch("windyfly.channels.matrix_bot.asyncio.sleep", AsyncMock()):
            await bot._on_sync_error(_sync_error())
        assert bot._sync_failed_since is not None
        bot._sync_failures = 0                       # what the reconnect loop does
        bot._sync_failed_since -= 300                # outage began 5 min ago
        assert bot._sync_healthy() is False
        assert bot._sync_fail_age_s() >= 300
        await bot._on_sync_response(MagicMock())     # a real success clears it
        assert bot._sync_failed_since is None
        assert bot._sync_healthy() is True


class TestHonestHeartbeat:
    def test_heartbeat_carries_sync_fields(self, tmp_path):
        bot = _bot()
        bot._connected = True
        bot._last_sync_success = time.time() - 900
        bot._sync_failures = 5
        bot._last_sync_error = "status=502 Bad Gateway"
        captured = {}

        def fake_write(channel, *, polling=True, extra=None, **kw):
            captured.update({"channel": channel, "polling": polling, **(extra or {})})

        with patch("windyfly.supervisor.heartbeat.write_heartbeat", fake_write):
            bot._write_heartbeat_once()

        assert captured["channel"] == "matrix"
        assert captured["polling"] is True          # the loop is alive
        assert captured["sync_ok"] is False         # but the homeserver is not
        assert captured["sync_failures"] == 5
        assert captured["sync_fail_age_s"] >= 900
        assert "502" in captured["last_error"]

    def test_write_heartbeat_extra_never_overrides_core_keys(self, tmp_path):
        write_heartbeat("matrix", polling=True, state_dir=tmp_path,
                        extra={"sync_ok": False, "polling": False, "channel": "x"})
        hb = read_heartbeat("matrix", tmp_path)
        assert hb["polling"] is True
        assert hb["channel"] == "matrix"
        assert hb["sync_ok"] is False

    def test_guardian_reports_sync_failing_without_restarting(self, tmp_path):
        write_heartbeat("matrix", polling=True, state_dir=tmp_path,
                        extra={"sync_ok": False, "sync_fail_age_s": 4200.0})
        cfg = GuardianConfig(channels=["matrix"], state_dir_override=str(tmp_path))
        res = check_health(cfg)
        assert res.healthy is True
        assert "matrix:sync_failing(4200.0s)" in res.detail

    def test_guardian_still_fails_on_polling_dead(self, tmp_path):
        write_heartbeat("matrix", polling=False, state_dir=tmp_path,
                        extra={"sync_ok": False})
        cfg = GuardianConfig(channels=["matrix"], state_dir_override=str(tmp_path))
        res = check_health(cfg)
        assert res.healthy is False
        assert "matrix:polling_dead" in res.detail


class TestHatchDmRoomJoin:
    @pytest.mark.asyncio
    async def test_joins_when_not_a_member(self):
        bot = _bot()
        bot._hatch_dm_room_id = "!dm:chat.windychat.ai"
        bot.client.rooms = {}
        bot.client.join = AsyncMock()
        await bot._join_hatch_dm_room()
        bot.client.join.assert_awaited_once_with("!dm:chat.windychat.ai")

    @pytest.mark.asyncio
    async def test_skips_when_already_joined_or_unknown(self):
        bot = _bot()
        bot.client.join = AsyncMock()
        await bot._join_hatch_dm_room()                       # no room id
        bot._hatch_dm_room_id = "!dm:chat.windychat.ai"
        bot.client.rooms = {"!dm:chat.windychat.ai": object()}
        await bot._join_hatch_dm_room()                       # already in
        bot.client.join.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_join_failure_never_raises(self):
        bot = _bot()
        bot._hatch_dm_room_id = "!dm:chat.windychat.ai"
        bot.client.rooms = {}
        bot.client.join = AsyncMock(side_effect=RuntimeError("502"))
        await bot._join_hatch_dm_room()  # must not raise
