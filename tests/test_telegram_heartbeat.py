"""Telegram heartbeat health-signal regression.

Pre-fix bug: ~/.windy/windy-0.log showed
``♥ Telegram heartbeat: connected=True, last_success_age=1800s``
climbing unbounded while polling was perfectly healthy. The field
``_last_success_at`` was only set on user-message receipt, so a long
quiet period looked like a system failure.

This test locks the new contract:
  - ``_polling_alive()`` reflects PTB updater state, not stale flags.
  - ``is_connected()`` delegates to ``_polling_alive()``.
  - Heartbeat log uses ``polling=alive|DEAD`` + ``last_message_age``.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from windyfly.channels.telegram_bot import TelegramChannel


def _make_channel() -> TelegramChannel:
    return TelegramChannel(allowed_user_ids=["1"])


class TestPollingAlive:
    def test_no_app_means_dead(self):
        ch = _make_channel()
        assert ch._polling_alive() is False

    def test_no_updater_means_dead(self):
        ch = _make_channel()
        ch._app = SimpleNamespace(updater=None)
        assert ch._polling_alive() is False

    def test_updater_not_running_means_dead(self):
        ch = _make_channel()
        ch._app = SimpleNamespace(updater=SimpleNamespace(running=False))
        assert ch._polling_alive() is False

    def test_updater_running_means_alive(self):
        ch = _make_channel()
        ch._app = SimpleNamespace(updater=SimpleNamespace(running=True))
        assert ch._polling_alive() is True

    def test_updater_running_raises_treated_as_dead(self):
        """If accessing .running blows up (PTB internal weirdness),
        we report DEAD rather than masking the failure."""
        ch = _make_channel()

        class Boom:
            @property
            def running(self):
                raise RuntimeError("ptb internals exploded")

        ch._app = SimpleNamespace(updater=Boom())
        assert ch._polling_alive() is False


class TestIsConnected:
    def test_delegates_to_polling_alive(self):
        ch = _make_channel()
        ch._app = SimpleNamespace(updater=SimpleNamespace(running=True))
        assert ch.is_connected() is True
        ch._app.updater.running = False
        assert ch.is_connected() is False


class TestHeartbeatLog:
    @pytest.mark.asyncio
    async def test_alive_logs_polling_alive_with_message_age(self, caplog):
        ch = _make_channel()
        ch._app = SimpleNamespace(updater=SimpleNamespace(running=True))
        ch._last_message_at = 0.0  # never received a message

        async def fake_sleep(_):
            ch._shutting_down = True

        with caplog.at_level(logging.INFO, logger="windyfly.channels.telegram_bot"):
            with patch("windyfly.channels.telegram_bot.asyncio.sleep", fake_sleep):
                await asyncio.wait_for(ch._heartbeat_loop(), timeout=1.0)

        joined = "\n".join(r.message for r in caplog.records)
        assert "polling=alive" in joined
        assert "last_message_age" in joined
        # The pre-fix format must be gone — anyone scraping it should
        # get a loud failure so they update.
        assert "connected=" not in joined
        assert "last_success_age" not in joined

    @pytest.mark.asyncio
    async def test_dead_logs_warning_and_clears_connected(self, caplog):
        ch = _make_channel()
        ch._connected = True  # simulate stale "we connected once"
        ch._app = SimpleNamespace(updater=SimpleNamespace(running=False))

        async def fake_sleep(_):
            ch._shutting_down = True

        with caplog.at_level(logging.WARNING, logger="windyfly.channels.telegram_bot"):
            with patch("windyfly.channels.telegram_bot.asyncio.sleep", fake_sleep):
                await asyncio.wait_for(ch._heartbeat_loop(), timeout=1.0)

        joined = "\n".join(r.message for r in caplog.records)
        assert "polling=DEAD" in joined
        assert ch._connected is False
