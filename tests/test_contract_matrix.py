"""Contract tests for Matrix bot message format.

Verifies that when the Matrix bot sends a message, it includes the
correct Windy metadata: {msgtype, body, windy_original: true, windy_lang}.
"""

from __future__ import annotations

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
            "homeserver": "https://chat.windyword.ai",
            "bot_user": "@windyfly:chat.windyword.ai",
        },
    }


class TestMessageFormatContract:
    """Verify the exact message content shape sent to Matrix rooms."""

    @pytest.mark.asyncio
    @patch("windyfly.channels.matrix_bot.agent_respond")
    async def test_response_message_has_correct_schema(self, mock_respond):
        """Agent response message must include msgtype, body, windy_original=True, windy_lang."""
        mock_respond.return_value = "Here's your answer!"

        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_typing = AsyncMock()
        bot.client.room_send = AsyncMock()

        room = MagicMock()
        room.room_id = "!contract:chat.windyword.ai"
        room.user_name.return_value = "ContractUser"

        event = MagicMock()
        event.sender = "@user:chat.windyword.ai"
        event.body = "What's the weather?"
        event.server_timestamp = time.time() * 1000
        event.source = {"content": {}}

        await bot._on_message(room, event)

        bot.client.room_send.assert_called_once()
        call_args = bot.client.room_send.call_args[0]

        # First arg: room_id
        assert call_args[0] == "!contract:chat.windyword.ai"
        # Second arg: event type
        assert call_args[1] == "m.room.message"

        # Third arg: content dict — the contract
        content = call_args[2]
        assert content["msgtype"] == "m.text"
        assert content["body"] == "Here's your answer!"
        assert content["windy_original"] is True  # boolean, NOT the text
        assert isinstance(content["windy_lang"], str)
        assert len(content["windy_lang"]) >= 2  # ISO 639-1 code

        db.close()

    @pytest.mark.asyncio
    async def test_welcome_message_has_correct_schema(self):
        """Welcome message on invite must also include windy_original=True."""
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.join = AsyncMock()
        bot.client.room_send = AsyncMock()

        room = MagicMock()
        room.room_id = "!welcome:chat.windyword.ai"

        event = MagicMock()
        event.state_key = "@windyfly:chat.windyword.ai"

        await bot._on_invite(room, event)

        content = bot.client.room_send.call_args[0][2]
        assert content["msgtype"] == "m.text"
        assert content["windy_original"] is True
        assert content["windy_lang"] == "en"
        assert "Windy Fly" in content["body"]

        db.close()

    @pytest.mark.asyncio
    async def test_flushed_message_has_correct_schema(self):
        """Pending messages flushed on reconnect must also match the contract."""
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_send = AsyncMock()

        bot._pending_responses.append(("!flush:test", "Delayed reply"))
        await bot._flush_pending()

        content = bot.client.room_send.call_args[0][2]
        assert content["msgtype"] == "m.text"
        assert content["body"] == "Delayed reply"
        assert content["windy_original"] is True
        assert content["windy_lang"] == "en"

        db.close()

    @pytest.mark.asyncio
    @patch("windyfly.channels.matrix_bot.agent_respond")
    async def test_windy_lang_detects_non_latin(self, mock_respond):
        """windy_lang should detect non-Latin scripts."""
        mock_respond.return_value = "こんにちは世界"

        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)
        bot.client.room_typing = AsyncMock()
        bot.client.room_send = AsyncMock()

        room = MagicMock()
        room.room_id = "!lang:test"
        room.user_name.return_value = "User"

        event = MagicMock()
        event.sender = "@user:test"
        event.body = "Hi"
        event.server_timestamp = time.time() * 1000
        event.source = {}

        await bot._on_message(room, event)

        content = bot.client.room_send.call_args[0][2]
        assert content["windy_lang"] == "ja"

        db.close()


class TestBotIdentityContract:
    """Verify bot identity defaults."""

    def test_default_homeserver(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        del config["matrix"]["homeserver"]
        del config["matrix"]["bot_user"]

        bot = WindyFlyMatrixBot(config, db, wq)
        assert bot.bot_user_id == "@windyfly:chat.windyword.ai"
        db.close()

    def test_custom_homeserver(self):
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        config["matrix"]["homeserver"] = "https://custom.server.com"
        config["matrix"]["bot_user"] = "@bot:custom.server.com"

        bot = WindyFlyMatrixBot(config, db, wq)
        assert bot.bot_user_id == "@bot:custom.server.com"
        db.close()

    @pytest.mark.asyncio
    @patch.dict("os.environ", {"MATRIX_BOT_TOKEN": "tok123"})
    async def test_token_login_sets_credentials(self):
        """Token login should set access_token and user_id on the client."""
        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)

        await bot.login()

        assert bot.client.access_token == "tok123"
        assert bot.client.user_id == "@windyfly:chat.windyword.ai"
        db.close()

    @pytest.mark.asyncio
    @patch.dict("os.environ", {}, clear=True)
    async def test_no_credentials_raises(self):
        """Missing both token and password raises RuntimeError."""
        import os
        os.environ.pop("MATRIX_BOT_TOKEN", None)
        os.environ.pop("MATRIX_BOT_PASSWORD", None)

        db = Database(":memory:")
        wq = WriteQueue()
        config = _make_config()
        bot = WindyFlyMatrixBot(config, db, wq)

        with pytest.raises(RuntimeError, match="No Matrix credentials"):
            await bot.login()
        db.close()
