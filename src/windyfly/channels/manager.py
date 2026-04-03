"""Channel manager — starts/stops all configured channels, routes messages to the agent loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from windyfly.channels.base import ChannelAdapter, IncomingMessage

logger = logging.getLogger(__name__)


class ChannelManager:
    """Manages all active messaging channels.

    Routes incoming messages from any platform to the agent loop
    and returns responses. The brain doesn't care where the message
    came from.
    """

    def __init__(
        self,
        agent_respond: Callable[[str, str], Awaitable[str] | str],
    ) -> None:
        """
        Args:
            agent_respond: callable(user_message, session_id) -> str
        """
        self.agent_respond = agent_respond
        self.channels: dict[str, ChannelAdapter] = {}

    def register(self, adapter: ChannelAdapter) -> None:
        """Register a channel adapter."""
        adapter.on_message = self._handle_message
        self.channels[adapter.name] = adapter
        logger.info("Channel registered: %s", adapter.name)

    async def _handle_message(self, msg: IncomingMessage) -> str:
        """Route incoming message to agent loop, return response."""
        session_id = f"{msg.platform}:{msg.channel_id}"
        try:
            result = self.agent_respond(msg.text, session_id)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except Exception as exc:
            logger.error("Agent error on %s: %s", msg.platform, exc)
            return "Sorry, I hit an error processing that. Try again?"

    async def start_all(self) -> None:
        """Start all registered channels."""
        for name, ch in self.channels.items():
            try:
                await ch.start()
                logger.info("Channel started: %s", name)
            except Exception as exc:
                logger.error("Channel %s failed to start: %s", name, exc)

    async def stop_all(self) -> None:
        """Stop all registered channels."""
        for name, ch in self.channels.items():
            try:
                await ch.stop()
                logger.info("Channel stopped: %s", name)
            except Exception as e:
                logger.debug("Channel %s stop failed: %s", name, e)

    def status(self) -> dict[str, bool]:
        """Return connection status of all channels."""
        return {name: ch.is_connected() for name, ch in self.channels.items()}
