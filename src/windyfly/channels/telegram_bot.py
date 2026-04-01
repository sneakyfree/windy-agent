"""Telegram adapter using python-telegram-bot.

Env vars: TELEGRAM_BOT_TOKEN
Get token from @BotFather on Telegram.
Install: pip install windyfly[telegram]
"""

from __future__ import annotations

import logging
import os

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class TelegramChannel(ChannelAdapter):
    """Windy Fly on Telegram."""

    name = "telegram"

    def __init__(self) -> None:
        self._app = None
        self._connected = False

    async def start(self) -> None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set — get one from @BotFather")

        from telegram.ext import ApplicationBuilder, MessageHandler, filters

        self._app = ApplicationBuilder().token(token).build()
        self._app.add_handler(
            MessageHandler(filters.TEXT, self._handle)
        )

        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        self._connected = True
        logger.info("Telegram bot started")

    async def _handle(self, update, context) -> None:
        if not update.message or not update.message.text:
            return

        text = update.message.text

        # Unified command detection
        from windyfly.channels.base import handle_incoming
        was_command, cmd_response = await handle_incoming(text, {"platform": "telegram"})
        if was_command:
            await update.message.reply_text(cmd_response)
            return

        msg = IncomingMessage(
            platform="telegram",
            channel_id=str(update.message.chat_id),
            sender_id=str(update.message.from_user.id),
            sender_name=update.message.from_user.first_name or "User",
            text=text,
        )
        response = await self.on_message(msg)
        await update.message.reply_text(response)

    async def send(self, message: OutgoingMessage) -> None:
        if self._app:
            await self._app.bot.send_message(
                chat_id=message.channel_id, text=message.text,
            )

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
