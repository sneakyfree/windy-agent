"""Base channel interface — every messaging platform implements this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable


@dataclass
class IncomingMessage:
    """Normalized message from any platform."""

    platform: str
    channel_id: str
    sender_id: str
    sender_name: str
    text: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutgoingMessage:
    """Response to send back to the platform."""

    text: str
    channel_id: str
    reply_to: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class ChannelAdapter(ABC):
    """Base class for all messaging platform adapters.

    Subclasses implement platform-specific connection logic.
    The channel manager sets ``on_message`` before calling ``start()``.
    """

    name: str = "unknown"

    @abstractmethod
    async def start(self) -> None:
        """Connect to the platform and start listening for messages."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect from the platform."""
        ...

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> None:
        """Send a message to the platform."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if currently connected and listening."""
        ...

    # Set by the channel manager before start()
    on_message: Callable[[IncomingMessage], Awaitable[str]] | None = None


async def handle_incoming(text: str, context: dict | None = None) -> tuple[bool, str]:
    """Check if text is a command and execute it. Returns (was_command, response)."""
    from windyfly.commands.registry import registry, is_command, parse_command

    if is_command(text):
        response = await registry.execute(parse_command(text), context)
        return True, response
    return False, ""
