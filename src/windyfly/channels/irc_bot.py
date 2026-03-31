"""IRC adapter — simple, works everywhere.

Env vars: IRC_SERVER, IRC_PORT, IRC_CHANNEL, IRC_NICKNAME
No API key needed — IRC is open.
Install: pip install windyfly[irc]
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)


class IRCChannel(ChannelAdapter):
    """Windy Fly on IRC — responds when mentioned by nickname."""

    name = "irc"

    def __init__(self) -> None:
        self._connected = False
        self._connection = None
        self._thread: threading.Thread | None = None

    async def start(self) -> None:
        server = os.environ.get("IRC_SERVER", "irc.libera.chat")
        port = int(os.environ.get("IRC_PORT", "6667"))
        channel = os.environ.get("IRC_CHANNEL", "#windyfly")
        nickname = os.environ.get("IRC_NICKNAME", "windyfly")

        import irc.client

        reactor = irc.client.Reactor()
        self._connection = reactor.server().connect(server, port, nickname)
        adapter = self
        loop = asyncio.get_event_loop()

        def on_connect(connection, event):
            connection.join(channel)
            adapter._connected = True
            logger.info("IRC connected to %s on %s", channel, server)

        def on_pubmsg(connection, event):
            text = event.arguments[0]
            if nickname.lower() not in text.lower():
                return
            msg = IncomingMessage(
                platform="irc",
                channel_id=event.target,
                sender_id=event.source,
                sender_name=event.source.split("!")[0],
                text=text,
            )
            future = asyncio.run_coroutine_threadsafe(adapter.on_message(msg), loop)
            try:
                response = future.result(timeout=30)
                connection.privmsg(event.target, response)
            except Exception as exc:
                logger.error("IRC response error: %s", exc)

        self._connection.add_global_handler("welcome", on_connect)
        self._connection.add_global_handler("pubmsg", on_pubmsg)

        self._thread = threading.Thread(
            target=reactor.process_forever, daemon=True,
        )
        self._thread.start()

    async def send(self, message: OutgoingMessage) -> None:
        if self._connection:
            self._connection.privmsg(message.channel_id, message.text)

    async def stop(self) -> None:
        if self._connection:
            self._connection.disconnect("Goodbye!")
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
