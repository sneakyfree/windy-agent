"""Telegram adapter using python-telegram-bot.

Env vars: TELEGRAM_BOT_TOKEN
Get token from @BotFather on Telegram.
Install: pip install windyfly[telegram]

Resilience model (parity with matrix_bot.py):
  - Exponential backoff on initial-connect failures (1s → 60s max)
  - Error handler logs every polling error so one bad update can't
    masquerade as a system failure
  - 5-minute heartbeat: polling-loop liveness (truthful health) +
    last-message age (user activity, not health)
  - Reconnect events written to the observability ledger when a
    Database + WriteQueue are wired in

Signal-driven shutdown lives at the application layer (main.py). The
channel only exposes ``stop()``; the application orchestrates the
lifecycle so that ``write_queue.stop()`` and ``db.close()`` actually
get to run before the process exits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage
from windyfly.observability.sanitize import sanitize_outgoing
from windyfly.observability.sd_notify import notify_watchdog

logger = logging.getLogger(__name__)

_INITIAL_BACKOFF_S = 1
_MAX_BACKOFF_S = 60
_HEARTBEAT_INTERVAL_S = 300  # 5 minutes


class TelegramChannel(ChannelAdapter):
    """Windy Fly on Telegram."""

    name = "telegram"

    def __init__(
        self,
        allowed_user_ids: list[str] | None = None,
        dm_policy: str = "open",
        db: Any = None,
        write_queue: Any = None,
    ) -> None:
        # python-telegram-bot is an optional extra — mypy can't resolve
        # ApplicationBuilder on a baseline install, so type as Any.
        self._app: Any = None
        self._connected = False
        # When allowed_user_ids is non-empty, silently drop messages from any
        # other sender. Mirrors the fleet allowFrom convention (see
        # ACCESS_LOCKBOX §5 — Kit/Herm/Windy bots all gate by Grant's
        # Telegram ID to avoid the 2026-02-10 BlueBubbles incident).
        self._allowed_user_ids: set[str] = set(allowed_user_ids or [])
        self._dm_policy = dm_policy

        # Observability hooks — optional so callers without a DB still work.
        self._db = db
        self._write_queue = write_queue

        # Resilience state
        self._backoff = _INITIAL_BACKOFF_S
        self._shutting_down = False
        # Time of last successful user-message round-trip. Reflects USER
        # ACTIVITY, not bot health — a long age here just means no one
        # has texted, which is fine.
        self._last_message_at: float = 0.0
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set — get one from @BotFather")

        from telegram.ext import ApplicationBuilder, MessageHandler, filters

        # Outer connect loop — survives transient network failures during
        # initialize/start_polling. PTB's polling is itself resilient once
        # running, so this loop is mostly about getting off the ground when
        # the network is flaky at startup.
        last_error: Exception | None = None
        while not self._shutting_down:
            try:
                self._app = ApplicationBuilder().token(token).build()
                self._app.add_handler(MessageHandler(filters.TEXT, self._handle))
                self._app.add_error_handler(self._on_polling_error)

                await self._app.initialize()
                await self._app.start()
                await self._app.updater.start_polling()
                self._connected = True
                self._last_message_at = time.time()
                self._backoff = _INITIAL_BACKOFF_S
                logger.info("Telegram bot started")
                break
            except Exception as exc:
                last_error = exc
                self._connected = False
                logger.warning(
                    "Telegram start failed: %s. Reconnecting in %ds...",
                    exc, self._backoff,
                )
                self._log_reconnect_event(str(exc), self._backoff)
                await self._safe_app_shutdown()
                if self._shutting_down:
                    break
                await asyncio.sleep(self._backoff)
                self._backoff = min(self._backoff * 2, _MAX_BACKOFF_S)
        else:
            # Loop only exits via break or shutdown — if shutdown won, surface
            # the last error so the caller can act on it.
            if last_error is not None:
                raise last_error

        # Heartbeat task runs until stop() is called.
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def _on_polling_error(self, update: Any, context: Any) -> None:
        """Catch errors raised during update processing.

        Without a registered error handler, PTB writes a traceback to stderr
        and otherwise carries on; with one we can both silence the noise and
        log it through our observability path.
        """
        err = getattr(context, "error", None)
        logger.warning("Telegram polling error: %s", err)
        self._log_reconnect_event(str(err), 0)

    def _polling_alive(self) -> bool:
        """True iff PTB's polling loop is currently running.

        The pre-fix heartbeat conflated "connected at startup" with
        "still polling now," so a silently-dead polling loop looked
        healthy. This delegates to PTB's own state.
        """
        try:
            return bool(
                self._app
                and getattr(self._app, "updater", None)
                and self._app.updater.running
            )
        except Exception:
            return False

    async def _heartbeat_loop(self) -> None:
        """Log a heartbeat every 5 minutes with TRUE polling health."""
        while not self._shutting_down:
            try:
                alive = self._polling_alive()
                age = (
                    time.time() - self._last_message_at
                    if self._last_message_at
                    else -1.0
                )
                if alive:
                    logger.info(
                        "♥ Telegram heartbeat: polling=alive, last_message_age=%.0fs",
                        age,
                    )
                    # Pet the systemd watchdog. If polling is dead we
                    # deliberately DON'T pet it so systemd's
                    # WatchdogSec= timer fires and restarts us. No-op
                    # when NOTIFY_SOCKET is unset (dev / tests).
                    notify_watchdog()
                else:
                    logger.warning(
                        "✗ Telegram heartbeat: polling=DEAD, last_message_age=%.0fs"
                        " — PTB updater not running; restart needed",
                        age,
                    )
                    self._connected = False
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

    def _log_reconnect_event(self, error: str, backoff_seconds: int) -> None:
        """Best-effort write to the observability ledger."""
        if self._db is None or self._write_queue is None:
            return
        try:
            from windyfly.observability.events import log_event
            log_event(self._db, self._write_queue, "telegram.reconnect", {
                "error": error[:200],
                "backoff_seconds": backoff_seconds,
            })
        except Exception as e:
            logger.debug("Reconnect event logging failed: %s", e)

    async def _safe_app_shutdown(self) -> None:
        """Tear down a partially-built app without raising."""
        if self._app is None:
            return
        for step in (
            lambda: self._app.updater.stop() if self._app.updater else None,
            lambda: self._app.stop(),
            lambda: self._app.shutdown(),
        ):
            try:
                result = step()
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.debug("App shutdown step failed: %s", e)
        self._app = None

    async def _handle(self, update, context) -> None:
        if not update.message or not update.message.text:
            return

        sender_id = str(update.message.from_user.id)
        if self._allowed_user_ids and sender_id not in self._allowed_user_ids:
            logger.warning("Dropping Telegram message from unauthorized sender %s", sender_id)
            return

        text = update.message.text

        # Unified command detection
        from windyfly.channels.base import handle_incoming
        was_command, cmd_response = await handle_incoming(text, {"platform": "telegram"})
        if was_command:
            await update.message.reply_text(sanitize_outgoing(cmd_response))
            self._last_message_at = time.time()
            return

        msg = IncomingMessage(
            platform="telegram",
            channel_id=str(update.message.chat_id),
            sender_id=str(update.message.from_user.id),
            sender_name=update.message.from_user.first_name or "User",
            text=text,
        )
        # on_message wired by the channel manager before start().
        assert self.on_message is not None
        response = await self.on_message(msg)
        await update.message.reply_text(sanitize_outgoing(response))
        self._last_message_at = time.time()

    async def send(self, message: OutgoingMessage) -> None:
        if self._app:
            await self._app.bot.send_message(
                chat_id=message.channel_id,
                text=sanitize_outgoing(message.text),
            )

    async def stop(self) -> None:
        self._shutting_down = True
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except (asyncio.CancelledError, Exception):
                pass
            self._heartbeat_task = None
        await self._safe_app_shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._polling_alive()
