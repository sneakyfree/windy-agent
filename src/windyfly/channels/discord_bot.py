"""Discord adapter using discord.py.

Env vars: DISCORD_BOT_TOKEN
Create bot at discord.com/developers -> Bot -> Token.
Install: pip install windyfly[discord]
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class DiscordChannel(ChannelAdapter):
    """Windy Fly on Discord — responds to DMs and @mentions."""

    name = "discord"

    def __init__(self) -> None:
        # SDK-typed as Any — discord.py is an optional extra; mypy can't
        # resolve discord.Client on a baseline install.
        self._client: Any = None
        self._connected = False
        self._loop_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        token = os.environ.get("DISCORD_BOT_TOKEN")
        if not token:
            raise RuntimeError(
                "DISCORD_BOT_TOKEN not set — create bot at discord.com/developers"
            )

        import discord

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)
        adapter = self

        @self._client.event
        async def on_ready():
            adapter._connected = True
            logger.info("Discord bot ready as %s", adapter._client.user)

        @self._client.event
        async def on_message(message):
            if message.author == adapter._client.user:
                return
            # Respond to DMs or when @mentioned
            if message.guild and not adapter._client.user.mentioned_in(message):
                return

            text = message.content

            # Unified command detection
            from windyfly.channels.base import handle_incoming
            was_command, cmd_response = await handle_incoming(text, {"platform": "discord"})
            if was_command:
                await message.reply(cmd_response)
                return

            msg = IncomingMessage(
                platform="discord",
                channel_id=str(message.channel.id),
                sender_id=str(message.author.id),
                sender_name=message.author.display_name,
                text=text,
            )
            # on_message wired by the channel manager before start().
            assert adapter.on_message is not None
            response = await adapter.on_message(msg)
            await message.reply(response)

        self._loop_task = asyncio.create_task(self._client.start(token))

    async def send(self, message: OutgoingMessage) -> None:
        if self._client:
            channel = self._client.get_channel(int(message.channel_id))
            if channel:
                await channel.send(message.text)

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
