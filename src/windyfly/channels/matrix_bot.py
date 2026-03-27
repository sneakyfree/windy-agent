"""Matrix/Synapse bot channel for Windy Fly.

Connects to the Synapse homeserver as @windyfly:chat.windypro.com,
handles DMs (including E2E encrypted rooms), auto-accepts invites,
shows typing indicators and presence.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import nio

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class WindyFlyMatrixBot:
    """Windy Fly Matrix bot — lives inside Windy Chat as a contact."""

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        write_queue: WriteQueue,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.write_queue = write_queue
        self.tool_registry = tool_registry

        matrix_config = config.get("matrix", {})
        homeserver = matrix_config.get("homeserver", "https://chat.windypro.com")
        bot_user = matrix_config.get("bot_user", "@windyfly:chat.windypro.com")

        self.bot_user_id = bot_user

        # E2E encryption key store (G14)
        store_path = matrix_config.get("store_path", "data/matrix_store")
        Path(store_path).mkdir(parents=True, exist_ok=True)

        self.client = nio.AsyncClient(
            homeserver,
            bot_user,
            store_path=store_path,
        )
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
        After login, uploads E2E encryption keys and trusts known devices.
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

        # Upload E2E encryption keys (G14)
        await self._setup_encryption()

    async def _setup_encryption(self) -> None:
        """Initialize E2E encryption: upload keys and auto-trust devices."""
        try:
            # Upload device keys to the homeserver
            if self.client.should_upload_keys:
                response = await self.client.keys_upload()
                if isinstance(response, nio.KeysUploadError):
                    logger.warning("Failed to upload E2E keys: %s", response.message)
                else:
                    logger.info("E2E encryption keys uploaded successfully")

            # Auto-trust all devices from users we share rooms with
            # This is appropriate for a bot — it trusts all senders
            await self._auto_trust_devices()

        except Exception as e:
            logger.warning("E2E setup failed (non-fatal): %s", e)

    async def _auto_trust_devices(self) -> None:
        """Auto-trust all known devices (bot policy: trust everyone).

        For a bot, this is the right policy — we want to read messages
        from all devices without manual verification prompts.
        """
        try:
            for user_id in self.client.device_store.users:
                for device_id, olm_device in self.client.device_store[user_id].items():
                    if not self.client.is_device_verified(olm_device):
                        self.client.verify_device(olm_device)
                        logger.debug("Auto-trusted device %s/%s", user_id, device_id)
        except Exception as e:
            logger.debug("Auto-trust scan: %s", e)

    async def _on_message(
        self,
        room: nio.MatrixRoom,
        event: nio.RoomMessageText,
    ) -> None:
        """Handle incoming text messages (encrypted and unencrypted)."""
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
                body, session_id, self.tool_registry,
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

    async def _on_encrypted_event(
        self,
        room: nio.MatrixRoom,
        event: nio.MegolmEvent,
    ) -> None:
        """Handle encrypted messages we couldn't decrypt.

        This fires when we receive a MegolmEvent that nio couldn't
        auto-decrypt. We attempt to request the missing keys.
        """
        logger.warning(
            "Couldn't decrypt message from %s in %s (session: %s). "
            "Requesting keys...",
            event.sender, room.room_id, event.session_id,
        )

        try:
            # Request missing encryption keys from the sender's device
            await self.client.request_room_key(event)
        except Exception as e:
            logger.error("Key request failed: %s", e)

    async def _on_key_verification(
        self,
        event: nio.KeyVerificationStart,
    ) -> None:
        """Auto-accept key verification requests (bot policy)."""
        logger.info("Key verification request from %s", event.sender)
        try:
            await self.client.accept_key_verification(event.transaction_id)
            await self.client.confirm_short_auth_string(event.transaction_id)
        except Exception as e:
            logger.debug("Key verification handling: %s", e)

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

            # Trust all devices in the new room
            await self._auto_trust_devices()

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

        # Register event callbacks (including E2E encrypted events)
        self.client.add_event_callback(self._on_message, nio.RoomMessageText)
        self.client.add_event_callback(self._on_invite, nio.InviteMemberEvent)
        self.client.add_event_callback(self._on_encrypted_event, nio.MegolmEvent)

        # Set presence to online
        try:
            await self.client.set_presence("online")
        except Exception:
            logger.debug("Failed to set presence (server may not support it)")

        logger.info("Windy Fly is online and listening for messages (E2E enabled)")

        # Sync with reconnection retry loop
        max_backoff = 30
        backoff = 1

        while True:
            try:
                # Flush pending responses on reconnect
                await self._flush_pending()

                # Re-trust devices on reconnect
                await self._auto_trust_devices()

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
