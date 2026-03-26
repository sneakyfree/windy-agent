"""Matrix/Synapse bot channel for Windy Fly.

Connects to the Synapse homeserver as @windyfly:chat.windypro.com,
handles DMs, auto-accepts invites, shows typing indicators and presence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any

import nio

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

logger = logging.getLogger(__name__)


class WindyFlyMatrixBot:
    """Windy Fly Matrix bot — lives inside Windy Chat as a contact."""

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        write_queue: WriteQueue,
    ) -> None:
        self.config = config
        self.db = db
        self.write_queue = write_queue

        matrix_config = config.get("matrix", {})
        homeserver = matrix_config.get("homeserver", "https://chat.windypro.com")
        bot_user = matrix_config.get("bot_user", "@windyfly:chat.windypro.com")

        self.bot_user_id = bot_user
        self.client = nio.AsyncClient(homeserver, bot_user)
        self.client.device_id = "WindyFlyAgent"

        # Map room_id → session_id for session continuity per room
        self._room_sessions: dict[str, str] = {}

        # Pending responses for offline queue
        self._pending_responses: list[tuple[str, str]] = []

        # Track boot time to ignore old messages
        self._boot_time = time.time()

    async def login(self) -> None:
        """Log into the Matrix homeserver.

        Uses MATRIX_BOT_TOKEN if available, otherwise MATRIX_BOT_PASSWORD.
        """
        token = os.environ.get("MATRIX_BOT_TOKEN")
        password = os.environ.get("MATRIX_BOT_PASSWORD")

        if token:
            self.client.access_token = token
            self.client.user_id = self.bot_user_id
            logger.info("Windy Fly logged in via token as %s", self.bot_user_id)
        elif password:
            response = await self.client.login(password)
            if isinstance(response, nio.LoginError):
                raise RuntimeError(f"Matrix login failed: {response.message}")
            logger.info("Windy Fly logged in via password as %s", self.bot_user_id)
        else:
            raise RuntimeError(
                "No Matrix credentials found. Set MATRIX_BOT_TOKEN or "
                "MATRIX_BOT_PASSWORD in your .env file."
            )

    async def _on_message(
        self,
        room: nio.MatrixRoom,
        event: nio.RoomMessageText,
    ) -> None:
        """Handle incoming text messages."""
        # Ignore our own messages
        if event.sender == self.bot_user_id:
            return

        # Ignore messages older than 30 seconds (prevent backlog processing)
        event_age = time.time() - (event.server_timestamp / 1000)
        if event_age > 30:
            return

        room_id = room.room_id
        body = event.body
        sender = event.sender
        display_name = room.user_name(sender) or sender

        # Extract Windy-specific metadata
        windy_lang = None
        if hasattr(event, "source") and event.source:
            windy_lang = event.source.get("content", {}).get("windy_lang")

        logger.info(
            "Message from %s in %s: %s",
            display_name, room_id, body[:100],
        )

        # Get or create session for this room
        if room_id not in self._room_sessions:
            self._room_sessions[room_id] = str(uuid.uuid4())
        session_id = self._room_sessions[room_id]

        # Show typing indicator
        try:
            await self.client.room_typing(room_id, True, timeout=15000)
        except Exception:
            logger.debug("Failed to set typing indicator")

        # Generate response
        try:
            response_text = agent_respond(
                self.config, self.db, self.write_queue,
                body, session_id,
            )
        except Exception as e:
            logger.error("Agent respond failed: %s", e)
            response_text = (
                "I hit a snag processing that. Let me try again in a moment. 🪰"
            )

        # Send response with Windy metadata
        try:
            content = {
                "msgtype": "m.text",
                "body": response_text,
                "windy_original": response_text,
                "windy_lang": "en",
            }
            await self.client.room_send(
                room_id,
                "m.room.message",
                content,
            )
        except Exception as e:
            logger.error("Failed to send response to %s: %s", room_id, e)
            self._pending_responses.append((room_id, response_text))

        # Turn off typing indicator
        try:
            await self.client.room_typing(room_id, False)
        except Exception:
            pass

    async def _on_invite(
        self,
        room: nio.MatrixRoom,
        event: nio.InviteMemberEvent,
    ) -> None:
        """Auto-accept room invites and send a welcome message."""
        if event.state_key != self.bot_user_id:
            return

        room_id = room.room_id
        logger.info("Received invite to room %s", room_id)

        try:
            await self.client.join(room_id)
            logger.info("Joined room %s", room_id)

            # Send welcome message
            await self.client.room_send(
                room_id,
                "m.room.message",
                {
                    "msgtype": "m.text",
                    "body": "Hey! I'm Windy Fly, your personal AI companion. 🪰",
                    "windy_original": "Hey! I'm Windy Fly, your personal AI companion. 🪰",
                    "windy_lang": "en",
                },
            )
        except Exception as e:
            logger.error("Failed to join room %s: %s", room_id, e)

    async def _flush_pending(self) -> None:
        """Flush any pending responses that failed to send."""
        if not self._pending_responses:
            return

        to_flush = self._pending_responses.copy()
        self._pending_responses.clear()

        for room_id, response_text in to_flush:
            try:
                await self.client.room_send(
                    room_id,
                    "m.room.message",
                    {
                        "msgtype": "m.text",
                        "body": response_text,
                        "windy_original": response_text,
                        "windy_lang": "en",
                    },
                )
                logger.info("Flushed pending response to %s", room_id)
            except Exception as e:
                logger.warning("Still can't send to %s: %s", room_id, e)
                self._pending_responses.append((room_id, response_text))

    async def start(self) -> None:
        """Start the bot: login, register callbacks, sync forever with reconnection."""
        await self.login()

        # Register event callbacks
        self.client.add_event_callback(self._on_message, nio.RoomMessageText)
        self.client.add_event_callback(self._on_invite, nio.InviteMemberEvent)

        # Set presence to online
        try:
            await self.client.set_presence("online")
        except Exception:
            logger.debug("Failed to set presence (server may not support it)")

        logger.info("Windy Fly is online and listening for messages")

        # Sync with reconnection retry loop
        max_backoff = 30
        backoff = 1

        while True:
            try:
                # Flush pending responses on reconnect
                await self._flush_pending()

                await self.client.sync_forever(
                    timeout=30000,
                    full_state=True,
                )
                # sync_forever only returns on error
                backoff = 1
            except (nio.exceptions.TransportError, Exception) as e:
                logger.warning(
                    "Connection lost: %s. Reconnecting in %ds...",
                    e, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        try:
            await self.client.set_presence("offline")
        except Exception:
            pass

        try:
            await self.client.close()
        except Exception:
            pass

        logger.info("Windy Fly is offline")
