"""CLI channel adapter — wraps the terminal as a ChannelAdapter.

Always available. Used as the default when no other channels are configured.
"""

from __future__ import annotations

import asyncio
import logging

from rich.console import Console

from windyfly.channels.base import ChannelAdapter, IncomingMessage, OutgoingMessage

logger = logging.getLogger(__name__)
_console = Console()


class CLIChannel(ChannelAdapter):
    """Interactive terminal chat as a ChannelAdapter."""

    name = "cli"

    def __init__(self) -> None:
        self._connected = False

    async def start(self) -> None:
        self._connected = True
        logger.info("CLI channel active")

        while self._connected:
            try:
                user_input = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input("You: "),
                )
            except (EOFError, KeyboardInterrupt):
                break

            if not user_input.strip():
                continue

            if user_input.strip().lower() in ("exit", "quit", "bye"):
                _console.print("Goodbye!")
                break

            msg = IncomingMessage(
                platform="cli",
                channel_id="terminal",
                sender_id="user",
                sender_name="User",
                text=user_input,
            )

            if self.on_message:
                response = await self.on_message(msg)
                _console.print(f"Fly: {response}")

        self._connected = False

    async def send(self, message: OutgoingMessage) -> None:
        _console.print(f"Fly: {message.text}")

    async def stop(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected
