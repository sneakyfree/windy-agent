"""Tests for the Matrix bot channel.

Tests message handling, invite acceptance, pending queue,
and bot initialization with mocked nio client.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


def _make_config() -> dict:
    return {
        "agent": {
            "default_model": "gpt-4o-mini",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {
            "soul_path": "SOUL.md",
            "humor_level": 7,
            "formality": 4,
            "proactivity": 5,
            "verbosity": 5,
            "reasoning_depth": 6,
            "autonomy": 3,
            "epistemic_strictness": 5,
        },
        "matrix": {
            "homeserver": "https://chat.windychat.ai",
            "bot_user": "@windyfly:chat.windychat.ai",
        },
    }


class TestMatrixBotInit:
    def test_creates_client(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        assert bot.bot_user_id == "@windyfly:chat.windychat.ai"
        assert bot.client is not None
        db.close()

    def test_room_sessions_empty_initially(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        assert len(bot._room_sessions) == 0
        db.close()

    def test_pending_responses_empty_initially(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        assert len(bot._pending_responses) == 0
        db.close()


class TestMatrixBotLogin:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {"MATRIX_BOT_TOKEN": "test-token-123"})
    async def test_token_login(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot._resolve_identity_from_token = AsyncMock()  # keep hermetic
        await bot.login()
        assert bot.client.access_token == "test-token-123"
        db.close()

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"MATRIX_BOT_TOKEN": "tok"})
    async def test_identity_realigned_to_token_owner(self):
        """The token is the source of truth for identity. If it belongs to a
        different user than config's matrix.bot_user, the bot must adopt the
        token's identity — otherwise _on_invite (state_key == bot_user_id)
        ignores every invite to the real user and never joins (Windy 0 was
        dark on Windy Chat for exactly this reason, 2026-07-06)."""
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()  # bot_user = @windyfly:chat.windychat.ai
        bot = WindyFlyMatrixBot(config, db, wq)
        assert bot.bot_user_id == "@windyfly:chat.windychat.ai"

        whoami = MagicMock()
        whoami.user_id = "@agent_et26-t11v-npd1:chat.windychat.ai"
        bot.client.whoami = AsyncMock(return_value=whoami)
        bot._setup_encryption = AsyncMock()

        await bot.login()

        assert bot.bot_user_id == "@agent_et26-t11v-npd1:chat.windychat.ai"
        assert bot.client.user_id == "@agent_et26-t11v-npd1:chat.windychat.ai"
        db.close()

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"MATRIX_BOT_TOKEN": "tok"})
    async def test_identity_resolution_never_crashes_channel(self):
        """whoami failing must not break login — an unverified identity is
        better than a dead channel."""
        db = Database(":memory:")
        wq = WriteQueue()
        bot = WindyFlyMatrixBot(_make_config(), db, wq)
        bot.client.whoami = AsyncMock(side_effect=RuntimeError("network down"))
        bot._setup_encryption = AsyncMock()
        await bot.login()  # must not raise
        assert bot.bot_user_id == "@windyfly:chat.windychat.ai"  # unchanged
        db.close()

    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_no_credentials_raises(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        # Remove any existing env vars
        import os
        os.environ.pop("MATRIX_BOT_TOKEN", None)
        os.environ.pop("MATRIX_BOT_PASSWORD", None)
        with pytest.raises(RuntimeError, match="No Matrix credentials"):
            await bot.login()
        db.close()


class TestMatrixBotMessage:
    @pytest.mark.asyncio
    @patch("windyfly.channels.matrix_bot.agent_respond")
    async def test_ignores_own_messages(self, mock_respond):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)

        room = MagicMock()
        room.room_id = "!test:chat.windychat.ai"
        room.user_name.return_value = "Windy Fly"

        event = MagicMock()
        event.sender = "@windyfly:chat.windychat.ai"  # Self
        event.body = "Hello"
        event.server_timestamp = time.time() * 1000

        await bot._on_message(room, event)
        mock_respond.assert_not_called()
        db.close()

    @pytest.mark.asyncio
    @patch("windyfly.channels.matrix_bot.agent_respond")
    async def test_ignores_old_messages(self, mock_respond):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)

        room = MagicMock()
        room.room_id = "!test:chat.windychat.ai"

        event = MagicMock()
        event.sender = "@user:chat.windychat.ai"
        event.body = "Old message"
        event.server_timestamp = (time.time() - 60) * 1000  # 60 seconds old

        await bot._on_message(room, event)
        mock_respond.assert_not_called()
        db.close()

    @pytest.mark.asyncio
    @patch("windyfly.channels.matrix_bot.agent_respond")
    async def test_responds_to_fresh_message(self, mock_respond):
        mock_respond.return_value = "Hello! I'm Windy Fly."

        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_typing = AsyncMock()
        bot.client.room_send = AsyncMock()

        room = MagicMock()
        room.room_id = "!test:chat.windychat.ai"
        room.user_name.return_value = "TestUser"

        event = MagicMock()
        event.sender = "@user:chat.windychat.ai"
        event.body = "Hey there!"
        event.server_timestamp = time.time() * 1000
        event.source = {"content": {"windy_lang": "en"}}

        await bot._on_message(room, event)

        mock_respond.assert_called_once()
        bot.client.room_send.assert_called_once()

        # Verify Windy metadata in response
        call_args = bot.client.room_send.call_args
        content = call_args[0][2]
        assert content["windy_lang"] == "en"
        assert content["windy_original"] is True
        db.close()

    @pytest.mark.asyncio
    @patch("windyfly.channels.matrix_bot.agent_respond")
    async def test_creates_session_per_room(self, mock_respond):
        mock_respond.return_value = "Response"

        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_typing = AsyncMock()
        bot.client.room_send = AsyncMock()

        for room_id in ["!room1:test", "!room2:test"]:
            room = MagicMock()
            room.room_id = room_id
            room.user_name.return_value = "User"

            event = MagicMock()
            event.sender = "@user:test"
            event.body = "Hi"
            event.server_timestamp = time.time() * 1000
            event.source = {}

            await bot._on_message(room, event)

        # Each room should get its own session
        assert len(bot._room_sessions) == 2
        assert bot._room_sessions["!room1:test"] != bot._room_sessions["!room2:test"]
        db.close()


class TestMatrixBotInvite:
    @pytest.mark.asyncio
    async def test_accepts_invite(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.join = AsyncMock()
        bot.client.room_send = AsyncMock()

        room = MagicMock()
        room.room_id = "!newroom:chat.windychat.ai"

        event = MagicMock()
        event.state_key = "@windyfly:chat.windychat.ai"

        await bot._on_invite(room, event)

        bot.client.join.assert_called_once_with("!newroom:chat.windychat.ai")
        # Welcome message sent
        bot.client.room_send.assert_called_once()
        welcome_content = bot.client.room_send.call_args[0][2]
        assert "Windy Fly" in welcome_content["body"]
        db.close()

    @pytest.mark.asyncio
    async def test_ignores_invite_for_others(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.join = AsyncMock()

        room = MagicMock()
        room.room_id = "!room:test"
        event = MagicMock()
        event.state_key = "@otheruser:test"  # Not us

        await bot._on_invite(room, event)
        bot.client.join.assert_not_called()
        db.close()


class TestPendingResponseQueue:
    @pytest.mark.asyncio
    async def test_flush_pending(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_send = AsyncMock()

        bot._pending_responses.append(("!room:test", "Delayed response"))
        await bot._flush_pending()

        bot.client.room_send.assert_called_once()
        assert len(bot._pending_responses) == 0
        db.close()

    @pytest.mark.asyncio
    async def test_no_flush_when_empty(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_send = AsyncMock()

        await bot._flush_pending()
        bot.client.room_send.assert_not_called()
        db.close()


class TestMatrixBotGracefulShutdown:
    """Regression for the 2026-07-04 prod SIGKILL: nio's sync_forever has its
    own retry loop, so closing the client from a side task never unblocks it —
    the process hung and systemd killed it (windy-0@matrix failed). A shutdown
    signal must CANCEL the sync task so start() returns and graceful runs."""

    @pytest.mark.asyncio
    async def test_sigterm_cancels_sync_and_runs_graceful(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bot = WindyFlyMatrixBot(_make_config(), db, wq)

        # Stub login + the reconnect-loop preamble so start() reaches sync.
        bot.login = AsyncMock()
        bot._flush_pending = AsyncMock()
        bot._auto_trust_devices = AsyncMock()
        bot._replay_offline_queue = AsyncMock()

        # sync_forever blocks forever until cancelled — the real behavior.
        async def _sync_forever(*a, **k):
            await asyncio.Event().wait()

        bot.client.sync_forever = _sync_forever
        bot.client.add_event_callback = MagicMock()
        bot.client.set_presence = AsyncMock()
        bot.client.close = AsyncMock()

        start_task = asyncio.create_task(bot.start())

        # Wait until the sync task is actually in flight.
        for _ in range(200):
            if bot._sync_task is not None and not bot._sync_task.done():
                break
            await asyncio.sleep(0.01)
        assert bot._sync_task is not None

        # Fire the shutdown handler (what SIGTERM triggers).
        bot._handle_shutdown_signal(15, asyncio.get_running_loop())

        # start() must return promptly — no hang.
        await asyncio.wait_for(start_task, timeout=5)

        assert bot._graceful_done is True
        bot.client.set_presence.assert_awaited()  # went offline
        bot.client.close.assert_awaited()          # connection closed
        db.close()

    @pytest.mark.asyncio
    async def test_graceful_shutdown_is_idempotent(self):
        db = Database(":memory:")
        wq = WriteQueue()
        bot = WindyFlyMatrixBot(_make_config(), db, wq)
        bot.client.set_presence = AsyncMock()
        bot.client.close = AsyncMock()

        await bot._graceful_shutdown()
        await bot._graceful_shutdown()  # second call is a no-op

        assert bot.client.close.await_count == 1
        db.close()


class TestMatrixCredentialsError:
    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_no_creds_raises_typed_error(self):
        """The launcher catches MatrixCredentialsError for a friendly message
        instead of a raw traceback — so it must be a distinct type."""
        from windyfly.channels.matrix_bot import MatrixCredentialsError

        db = Database(":memory:")
        wq = WriteQueue()
        bot = WindyFlyMatrixBot(_make_config(), db, wq)
        import os
        os.environ.pop("MATRIX_BOT_TOKEN", None)
        os.environ.pop("MATRIX_BOT_PASSWORD", None)
        with pytest.raises(MatrixCredentialsError):
            await bot.login()
        db.close()
