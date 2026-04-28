"""v7 Telegram CHANNEL-LAYER chaos — fault injection for the adapter
itself, complementing the agent-loop chaos in test_telegram_chaos.py.

Coverage gaps these tests close (each one a real crash mode in
production):

  - start() / stop() lifecycle re-entrancy
  - _safe_app_shutdown when individual steps raise
  - _on_polling_error with None / mangled context
  - _handle with malformed Telegram updates (no message, no
    from_user, empty text, missing first_name)
  - _heartbeat_loop survives a body exception (caught + logged,
    NOT propagated — would otherwise kill the heartbeat task and
    stop watchdog pings forever)
  - is_connected before start, after stop
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels.telegram_bot import TelegramChannel


def _channel(allowed: list[str] | None = None) -> TelegramChannel:
    return TelegramChannel(allowed_user_ids=allowed or ["1"])


# ── Lifecycle re-entrancy ──────────────────────────────────────────


class TestLifecycle:
    @pytest.mark.asyncio
    async def test_stop_before_start_no_raise(self):
        """stop() must be safe to call before start() ever fired —
        the channel manager calls it during cleanup of partially-
        initialized stacks (e.g. when token is unset)."""
        ch = _channel()
        await ch.stop()  # must not raise
        assert ch.is_connected() is False

    @pytest.mark.asyncio
    async def test_double_stop_idempotent(self):
        """Two stop() calls must not raise — signal-driven shutdown
        can race with manager-driven shutdown."""
        ch = _channel()
        ch._app = SimpleNamespace(
            updater=MagicMock(running=False, stop=AsyncMock()),
            stop=AsyncMock(),
            shutdown=AsyncMock(),
        )
        await ch.stop()
        await ch.stop()  # must not raise
        assert ch._app is None

    @pytest.mark.asyncio
    async def test_start_with_no_token_raises_runtime_error(self, monkeypatch):
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        ch = _channel()
        with pytest.raises(RuntimeError, match="TELEGRAM_BOT_TOKEN"):
            await ch.start()

    @pytest.mark.asyncio
    async def test_is_connected_false_before_any_start(self):
        ch = _channel()
        assert ch.is_connected() is False


# ── _safe_app_shutdown — must NEVER raise even with broken steps ──


class TestSafeAppShutdown:
    @pytest.mark.asyncio
    async def test_no_app_no_raise(self):
        ch = _channel()
        await ch._safe_app_shutdown()  # _app is None — must no-op
        assert ch._app is None

    @pytest.mark.asyncio
    async def test_continues_when_each_step_raises(self):
        """A partially-initialized PTB Application can have any
        combination of {updater.stop, stop, shutdown} blow up. We
        must run all three steps regardless and never raise."""
        ch = _channel()
        broken_updater = MagicMock(stop=MagicMock(side_effect=RuntimeError("boom1")))
        ch._app = SimpleNamespace(
            updater=broken_updater,
            stop=MagicMock(side_effect=RuntimeError("boom2")),
            shutdown=MagicMock(side_effect=RuntimeError("boom3")),
        )
        await ch._safe_app_shutdown()  # must not raise
        assert ch._app is None
        broken_updater.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_async_steps_correctly(self):
        ch = _channel()
        ch._app = SimpleNamespace(
            updater=MagicMock(stop=AsyncMock()),
            stop=AsyncMock(),
            shutdown=AsyncMock(),
        )
        await ch._safe_app_shutdown()
        assert ch._app is None


# ── _on_polling_error — robustness against PTB internals ──────────


class TestPollingErrorHandler:
    @pytest.mark.asyncio
    async def test_none_update_no_raise(self):
        ch = _channel()
        # PTB sometimes calls error handlers with update=None
        await ch._on_polling_error(None, SimpleNamespace(error=Exception("x")))

    @pytest.mark.asyncio
    async def test_no_error_attribute_no_raise(self):
        ch = _channel()
        # context object missing .error — defensive default to None
        await ch._on_polling_error(None, SimpleNamespace())


# ── _handle — malformed Telegram updates must not crash worker ────


class TestHandleMalformed:
    @pytest.mark.asyncio
    async def test_update_without_message_returns_silently(self):
        ch = _channel()
        update = SimpleNamespace(message=None)
        await ch._handle(update, None)  # no exception, no reply

    @pytest.mark.asyncio
    async def test_message_without_text_returns_silently(self):
        ch = _channel()
        update = SimpleNamespace(message=SimpleNamespace(text=None))
        await ch._handle(update, None)

    @pytest.mark.asyncio
    async def test_message_with_empty_text_returns_silently(self):
        ch = _channel()
        update = SimpleNamespace(message=SimpleNamespace(text=""))
        await ch._handle(update, None)

    @pytest.mark.asyncio
    async def test_unauthorized_sender_dropped(self, caplog):
        ch = _channel(allowed=["999"])
        update = SimpleNamespace(message=SimpleNamespace(
            text="hello",
            from_user=SimpleNamespace(id=42, first_name="Mallory"),
            chat_id=12345,
            reply_text=AsyncMock(),
        ))
        with caplog.at_level(logging.WARNING, logger="windyfly.channels.telegram_bot"):
            await ch._handle(update, None)
        assert any("unauthorized sender" in r.message for r in caplog.records)
        update.message.reply_text.assert_not_called()


# ── _heartbeat_loop must survive exceptions in body ──────────────


class TestHeartbeatRobustness:
    @pytest.mark.asyncio
    async def test_logging_exception_does_not_kill_loop(self):
        """If logger.info raises (e.g. handler is broken), the
        heartbeat task must continue ticking — otherwise watchdog
        pings stop and systemd would mistakenly restart us."""
        ch = _channel()
        ch._app = SimpleNamespace(updater=SimpleNamespace(running=True))

        ticks = {"n": 0}

        async def fake_sleep(_):
            ticks["n"] += 1
            if ticks["n"] >= 3:
                ch._shutting_down = True

        with patch(
            "windyfly.channels.telegram_bot.logger.info",
            side_effect=RuntimeError("logger died"),
        ), patch(
            "windyfly.channels.telegram_bot.asyncio.sleep", fake_sleep,
        ):
            await asyncio.wait_for(ch._heartbeat_loop(), timeout=2.0)

        # Loop ran multiple iterations despite logger.info raising every time.
        assert ticks["n"] >= 3

    @pytest.mark.asyncio
    async def test_polling_alive_exception_treated_as_dead(self):
        """If checking _polling_alive() throws, we must not crash —
        we must report DEAD (which triggers watchdog → restart)."""
        ch = _channel()

        class BoomApp:
            @property
            def updater(self):
                raise RuntimeError("ptb internals")

        ch._app = BoomApp()

        async def fake_sleep(_):
            ch._shutting_down = True

        with patch("windyfly.channels.telegram_bot.asyncio.sleep", fake_sleep):
            await asyncio.wait_for(ch._heartbeat_loop(), timeout=1.0)

        # _polling_alive() returned False, so we logged DEAD and cleared _connected.
        assert ch._connected is False


# ── Concurrent _handle calls — no _last_message_at race ──────────


class TestConcurrentHandle:
    @pytest.mark.asyncio
    async def test_many_handlers_at_once_no_crash(self):
        """Telegram delivers updates serially per chat but parallel
        across chats. Our handler must tolerate concurrent invocation
        without the race writing garbage to _last_message_at."""
        ch = _channel(allowed=["1", "2", "3"])

        async def fake_on_message(msg):
            await asyncio.sleep(0.001)
            return "ok"

        ch.on_message = fake_on_message  # type: ignore[assignment]

        def make_update(uid: int) -> SimpleNamespace:
            return SimpleNamespace(message=SimpleNamespace(
                text=f"hello from {uid}",
                from_user=SimpleNamespace(id=uid, first_name=f"u{uid}"),
                chat_id=uid * 100,
                reply_text=AsyncMock(),
            ))

        with patch(
            "windyfly.channels.base.handle_incoming",
            new=AsyncMock(return_value=(False, None)),
        ):
            await asyncio.gather(*[
                ch._handle(make_update(uid), None)
                for uid in (1, 2, 3, 1, 2, 3, 1, 2, 3, 1)
            ])

        # _last_message_at was set (some recent timestamp); no exceptions raised.
        assert ch._last_message_at > 0


# ── Backoff progression on repeated failures ──────────────────────


class TestBackoffProgression:
    def test_backoff_doubles_then_caps(self):
        """Exponential backoff must (a) double, (b) cap at MAX. Without
        the cap, prolonged outage would push backoff to absurd values
        (hours), so a transient is interpreted as permanent."""
        ch = _channel()
        from windyfly.channels.telegram_bot import (
            _INITIAL_BACKOFF_S, _MAX_BACKOFF_S,
        )
        assert ch._backoff == _INITIAL_BACKOFF_S
        # Simulate the in-loop progression
        observed = [ch._backoff]
        for _ in range(15):
            ch._backoff = min(ch._backoff * 2, _MAX_BACKOFF_S)
            observed.append(ch._backoff)
        assert observed[-1] == _MAX_BACKOFF_S
        assert observed[1] > observed[0]
        # And it never goes above the cap.
        assert max(observed) == _MAX_BACKOFF_S
