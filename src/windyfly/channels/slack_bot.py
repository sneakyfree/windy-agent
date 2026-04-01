"""Slack adapter using slack-bolt (Socket Mode).

Env vars: SLACK_BOT_TOKEN, SLACK_APP_TOKEN
Create app at api.slack.com/apps -> Socket Mode.
Install: pip install windyfly[slack]
"""

from __future__ import annotations

import logging
import os

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class SlackChannel(ChannelAdapter):
    """Windy Fly on Slack via Socket Mode (no public URL needed)."""

    name = "slack"

    def __init__(self) -> None:
        self._app = None
        self._handler = None
        self._connected = False

    async def start(self) -> None:
        bot_token = os.environ.get("SLACK_BOT_TOKEN")
        app_token = os.environ.get("SLACK_APP_TOKEN")
        if not bot_token or not app_token:
            raise RuntimeError(
                "SLACK_BOT_TOKEN and SLACK_APP_TOKEN required — "
                "create at api.slack.com/apps"
            )

        from slack_bolt.async_app import AsyncApp
        from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

        self._app = AsyncApp(token=bot_token)
        adapter = self

        @self._app.message("")
        async def handle_message(message, say):
            text = message.get("text", "")

            # Unified command detection
            from windyfly.channels.base import handle_incoming
            was_command, cmd_response = await handle_incoming(text, {"platform": "slack"})
            if was_command:
                await say(cmd_response)
                return

            msg = IncomingMessage(
                platform="slack",
                channel_id=message.get("channel", ""),
                sender_id=message.get("user", ""),
                sender_name=message.get("user", "User"),
                text=text,
            )
            response = await adapter.on_message(msg)
            await say(response)

        self._handler = AsyncSocketModeHandler(self._app, app_token)
        await self._handler.connect_async()
        self._connected = True
        logger.info("Slack bot connected via Socket Mode")

    async def send(self, message: OutgoingMessage) -> None:
        if self._app:
            await self._app.client.chat_postMessage(
                channel=message.channel_id, text=message.text,
            )

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
