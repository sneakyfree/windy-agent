"""Channel manager — starts/stops all configured channels, routes messages to the agent loop."""

from __future__ import annotations

import asyncio
import logging
from typing import Callable, Awaitable

from windyfly.channels.base import ChannelAdapter, IncomingMessage

logger = logging.getLogger(__name__)


class ChannelStartupError(RuntimeError):
    """Raised when one or more registered channels fail to start.

    A registered channel is one the operator explicitly opted into;
    failing to start it means the service cannot perform its job.
    Callers should treat this as fatal — log it, do NOT call
    sd_notify(READY=1), and exit non-zero so the process supervisor
    (systemd Restart=always) retries from a clean slate rather than
    leaving an `active (running)` service with no live channel.

    Surfaced 2026-05-17 outage: a missing optional dep
    (``python-telegram-bot``) made the Telegram channel ImportError
    at ``start()``. The previous implementation swallowed the
    exception with ``logger.error`` and returned normally, so
    ``windyfly.main`` then called ``notify_ready()`` and systemd's
    watchdog killed the zombie service every 10 min for 3 days.
    """

    def __init__(self, message: str, failures: list[tuple[str, BaseException]]) -> None:
        super().__init__(message)
        self.failures = failures


class ChannelManager:
    """Manages all active messaging channels.

    Routes incoming messages from any platform to the agent loop
    and returns responses. The brain doesn't care where the message
    came from.
    """

    def __init__(
        self,
        agent_respond: Callable[..., Awaitable[str] | str],
    ) -> None:
        """
        Args:
            agent_respond: callable(user_message, session_id) -> str;
                may optionally accept a ``band=`` keyword (detected via
                inspect at call time).
        """
        self.agent_respond = agent_respond
        self.channels: dict[str, ChannelAdapter] = {}
        self._accepts_band: bool | None = None

    def register(self, adapter: ChannelAdapter) -> None:
        """Register a channel adapter."""
        adapter.on_message = self._handle_message
        self.channels[adapter.name] = adapter
        logger.info("Channel registered: %s", adapter.name)

    async def send(self, channel_name: str, message) -> dict:
        """Dispatch an outgoing message, gated by the trust check.

        This is the call path for `post_chat_message`. Adapters still
        expose `.send()` for legacy plumbing, but new code should go
        through here so the gate and audit path are centralized.
        """
        from windyfly.trust.gate import TrustDenied, require_trust

        try:
            await require_trust("post_chat_message")
        except TrustDenied as denied:
            logger.warning("Chat message blocked by trust gate: %s", denied)
            return {"status": "denied", "error": str(denied)}

        adapter = self.channels.get(channel_name)
        if adapter is None:
            return {"status": "failed", "error": f"unknown channel: {channel_name}"}
        await adapter.send(message)
        return {"status": "sent"}

    async def _handle_message(self, msg: IncomingMessage) -> str:
        """Route incoming message to agent loop, return response."""
        # Rolling session_id — bumped on /new so prior turns drop out
        # of get_recent_episodes() and the per-session token tracker
        # restarts at zero. See windyfly.agent.session_reset for why.
        from windyfly.agent.session_reset import next_session_id
        session_id = next_session_id(msg.platform, msg.channel_id)
        # Sender → band (Sprint 4): identity finally survives past the
        # adapter boundary. Callbacks that accept ``band=`` get the
        # resolved trust level; older two-arg callbacks (tests, custom
        # embeddings) keep working unchanged.
        from windyfly.channels.identity import resolve_band
        band = resolve_band(msg.platform, msg.sender_id)
        if self._accepts_band is None:
            import inspect
            try:
                params = inspect.signature(self.agent_respond).parameters
                self._accepts_band = "band" in params or any(
                    prm.kind is inspect.Parameter.VAR_KEYWORD
                    for prm in params.values()
                )
            except (TypeError, ValueError):
                self._accepts_band = False
        try:
            if self._accepts_band:
                result = self.agent_respond(msg.text, session_id, band=band)
            else:
                result = self.agent_respond(msg.text, session_id)
            if asyncio.iscoroutine(result):
                result = await result
            # agent_respond is typed Callable[..., Awaitable[str] | str];
            # after the coroutine-unwrap above `result` is always a str at
            # runtime — narrow explicitly for mypy.
            assert isinstance(result, str)
            return result
        except Exception as exc:
            from windyfly.channels.errors import classify
            classified = classify(exc)
            logger.error("Agent error on %s: %s", msg.platform, classified.log_message)
            return classified.user_message

    async def start_all(self) -> None:
        """Start all registered channels.

        Raises:
            ChannelStartupError: if any registered channel's ``start()``
                raises. Channels that succeeded earlier in the loop
                are left running so the caller can stop them as part
                of clean shutdown — failures don't roll back the rest.

        Channels we registered are channels we want running; a failure
        to start one means the service is not operable. The caller
        (``windyfly.main._run``) catches this and exits non-zero
        BEFORE calling ``notify_ready()``, which prevents the
        "active (running) but functionally dead" state that fooled
        systemd into watchdog-killing a useless process every 10 min
        during the 2026-05-17 outage.
        """
        failures: list[tuple[str, BaseException]] = []
        for name, ch in self.channels.items():
            try:
                await ch.start()
                logger.info("Channel started: %s", name)
            except Exception as exc:
                logger.error("Channel %s failed to start: %s", name, exc)
                failures.append((name, exc))
        if failures:
            names = ", ".join(n for n, _ in failures)
            raise ChannelStartupError(
                f"channel(s) failed to start: {names}",
                failures=failures,
            )

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
