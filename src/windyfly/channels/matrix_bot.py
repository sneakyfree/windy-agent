"""Matrix/Synapse bot channel for Windy Fly.

Connects to the Synapse homeserver as @windyfly:chat.windypro.com,
handles DMs (including E2E encrypted rooms), auto-accepts invites,
shows typing indicators and presence.

Production hardening:
  - Exponential backoff on sync failures (1s → 60s max)
  - 5-minute heartbeat logging (connected, rooms, pending queue)
  - Graceful shutdown on SIGTERM/SIGINT (offline presence, flush, close)
  - Automatic re-login on expired access token
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import signal
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

# Backoff constants
_INITIAL_BACKOFF_S = 1
_MAX_BACKOFF_S = 60
_HEARTBEAT_INTERVAL_S = 300  # 5 minutes


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

        # Shutdown flag for graceful termination
        self._shutting_down = False

        # Connection state tracking
        self._connected = False
        self._last_sync_success: float = 0.0

        # Backoff state
        self._backoff = _INITIAL_BACKOFF_S

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

    async def _relogin_with_password(self) -> bool:
        """Attempt to re-login with password when the access token expires.

        Returns True if re-login succeeded, False otherwise.
        """
        password = os.environ.get("MATRIX_BOT_PASSWORD")
        if not password:
            logger.error(
                "Access token expired and no MATRIX_BOT_PASSWORD set — "
                "cannot re-authenticate. Set MATRIX_BOT_PASSWORD in .env."
            )
            return False

        try:
            logger.info("Access token expired — attempting re-login with password...")
            response = await self.client.login(password)
            if isinstance(response, nio.LoginError):
                logger.error("Re-login failed: %s", response.message)
                return False

            logger.info("Re-login successful. New token acquired.")
            await self._setup_encryption()
            return True
        except Exception as exc:
            logger.error("Re-login attempt failed: %s", exc)
            return False

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
                "windy_original": True,
                "windy_lang": self._detect_lang(response_text),
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
                    "windy_original": True,
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
                        "windy_original": True,
                        "windy_lang": "en",
                    },
                )
                logger.info("Flushed pending response to %s", room_id)
            except Exception as e:
                logger.warning("Still can't send to %s: %s", room_id, e)
                self._pending_responses.append((room_id, response_text))

    def _is_token_expired_error(self, error: Exception) -> bool:
        """Check if an error indicates an expired/invalid access token."""
        err_str = str(error).lower()
        return any(
            phrase in err_str
            for phrase in [
                "m_unknown_token",
                "access token",
                "token expired",
                "invalid token",
                "401",
                "unauthorized",
            ]
        )

    async def _heartbeat_loop(self) -> None:
        """Log a heartbeat every 5 minutes showing connection status."""
        while not self._shutting_down:
            try:
                rooms_joined = len(self.client.rooms) if hasattr(self.client, "rooms") else 0
                pending_count = len(self._pending_responses)
                logger.info(
                    "♥ Matrix heartbeat: connected=%s, rooms_joined=%d, pending_queue=%d",
                    self._connected,
                    rooms_joined,
                    pending_count,
                )
            except Exception as e:
                logger.debug("Heartbeat error: %s", e)

            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)

    def _setup_signal_handlers(self, loop: asyncio.AbstractEventLoop) -> None:
        """Register SIGTERM and SIGINT handlers for graceful shutdown."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(
                    sig,
                    functools.partial(self._handle_shutdown_signal, sig, loop),
                )
            except (NotImplementedError, RuntimeError):
                # Windows or non-main-thread — use signal.signal fallback
                try:
                    signal.signal(sig, lambda s, f: self._handle_shutdown_signal(s, loop))
                except (OSError, ValueError):
                    pass

    def _handle_shutdown_signal(
        self, sig: int, loop: asyncio.AbstractEventLoop
    ) -> None:
        """Handle shutdown signal by scheduling graceful stop."""
        sig_name = signal.Signals(sig).name if hasattr(signal, "Signals") else str(sig)
        logger.info("Received %s — initiating graceful shutdown...", sig_name)
        self._shutting_down = True
        loop.create_task(self._graceful_shutdown())

    async def _graceful_shutdown(self) -> None:
        """Graceful shutdown: set offline, flush messages, close connection."""
        logger.info("Graceful shutdown: setting presence to offline...")
        try:
            await self.client.set_presence("offline", status_msg="Windy Fly shutting down")
        except Exception as e:
            logger.debug("Could not set offline presence: %s", e)

        logger.info("Graceful shutdown: flushing %d pending messages...", len(self._pending_responses))
        await self._flush_pending()

        logger.info("Graceful shutdown: closing connection...")
        try:
            await self.client.close()
        except Exception as e:
            logger.debug("Error closing client: %s", e)

        self._connected = False
        logger.info("Graceful shutdown complete. Windy Fly is offline. 🪰")

    async def start(self) -> None:
        """Start the bot: login, register callbacks, sync forever with reconnection."""
        await self.login()

        # Register event callbacks (including E2E encrypted events)
        self.client.add_event_callback(self._on_message, nio.RoomMessageText)
        self.client.add_event_callback(self._on_invite, nio.InviteMemberEvent)
        self.client.add_event_callback(self._on_encrypted_event, nio.MegolmEvent)

        # Set presence to online (with retry — R1.11)
        for attempt in range(3):
            try:
                await self.client.set_presence(
                    "online", status_msg="Windy Fly is ready 🪰"
                )
                logger.info("Presence set to online")
                break
            except Exception as e:
                if attempt == 2:
                    logger.warning(
                        "Failed to set presence after 3 attempts: %s", e
                    )
                await asyncio.sleep(1)

        logger.info("Windy Fly is online and listening for messages (E2E enabled)")

        # Set up graceful shutdown signal handlers
        try:
            loop = asyncio.get_running_loop()
            self._setup_signal_handlers(loop)
        except RuntimeError:
            logger.debug("Could not register signal handlers (no running loop)")

        # Start heartbeat in background
        heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        # Sync with reconnection retry loop (exponential backoff)
        self._backoff = _INITIAL_BACKOFF_S
        self._connected = True

        try:
            while not self._shutting_down:
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
                    self._backoff = _INITIAL_BACKOFF_S
                    self._connected = True
                    self._last_sync_success = time.time()
                except Exception as e:
                    self._connected = False

                    # Check if this is a token expiry error
                    if self._is_token_expired_error(e):
                        logger.warning("Access token expired. Attempting re-login...")
                        if await self._relogin_with_password():
                            self._backoff = _INITIAL_BACKOFF_S
                            self._connected = True
                            continue
                        else:
                            logger.error("Re-login failed. Will retry in %ds", self._backoff)

                    logger.warning(
                        "Connection lost: %s. Reconnecting in %ds...",
                        e, self._backoff,
                    )

                    # Log reconnect event for observability (R1.10)
                    try:
                        from windyfly.observability.events import log_event
                        log_event(self.db, self.write_queue, "matrix.reconnect", {
                            "error": str(e)[:200],
                            "backoff_seconds": self._backoff,
                            "token_expired": self._is_token_expired_error(e),
                        })
                    except Exception:
                        pass  # Don't let event logging break reconnection

                    if self._shutting_down:
                        break

                    await asyncio.sleep(self._backoff)
                    self._backoff = min(self._backoff * 2, _MAX_BACKOFF_S)
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        self._shutting_down = True
        await self._graceful_shutdown()

    @staticmethod
    def _detect_lang(text: str) -> str:
        """Quick language detection based on Unicode character ranges.

        R1.12: Detects the dominant script in the response text and maps
        it to a language code. Falls back to 'en' for Latin scripts.

        Args:
            text: The response text to analyze.

        Returns:
            ISO 639-1 language code (best guess).
        """
        import unicodedata

        sample = text[:200]
        scripts: dict[str, int] = {}
        for ch in sample:
            if ch.isalpha():
                try:
                    name = unicodedata.name(ch, "UNKNOWN").split()[0]
                    scripts[name] = scripts.get(name, 0) + 1
                except ValueError:
                    pass
        if not scripts:
            return "en"
        dominant = max(scripts, key=scripts.get)  # type: ignore[arg-type]
        # Map common Unicode script name prefixes to ISO 639-1 codes
        script_map = {
            "CJK": "zh",
            "HANGUL": "ko",
            "HIRAGANA": "ja",
            "KATAKANA": "ja",
            "ARABIC": "ar",
            "DEVANAGARI": "hi",
            "CYRILLIC": "ru",
            "THAI": "th",
            "HEBREW": "he",
            "BENGALI": "bn",
            "TAMIL": "ta",
            "TELUGU": "te",
        }
        return script_map.get(dominant, "en")
