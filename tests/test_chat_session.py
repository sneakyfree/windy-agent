"""One-soul chat identity — chat_session.py + matrix_bot EPT login.

The real Fly mints its @agent_<passport> Matrix session from its EPT
(windy-chat #111) instead of the dead registration-secret path. These
tests pin: the EPT bearer is sent, 404/never-provisioned and no-EPT
degrade to None (legacy env fallback), and matrix_bot.login() adopts
the minted identity + device (no hardcoded WindyFlyAgent desync).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly import chat_session


def _run(coro):
    return asyncio.run(coro)


def _mock_client(resp):
    client = AsyncMock()
    client.post = AsyncMock(return_value=resp)
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


class TestFetchAgentChatSession:
    def test_no_ept_returns_none(self, monkeypatch):
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
        assert _run(chat_session.fetch_agent_chat_session()) is None

    def test_sends_ept_bearer_and_returns_session(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-live")
        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "matrix_user_id": "@agent_et26-x:chat.windychat.ai",
            "access_token": "syt_abc",
            "device_id": "FLYDEV9",
            "dm_room_id": "!dm:chat.windychat.ai",
        }
        resp.raise_for_status = MagicMock()
        ctx, client = _mock_client(resp)
        with patch("httpx.AsyncClient", return_value=ctx):
            out = _run(chat_session.fetch_agent_chat_session())
        assert out["access_token"] == "syt_abc"
        url = client.post.call_args.args[0]
        assert url.endswith("/api/v1/onboarding/agent/session")
        headers = client.post.call_args.kwargs["headers"]
        assert headers["Authorization"] == "Bearer ept-live"

    def test_unprovisioned_404_returns_none(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-live")
        resp = MagicMock(status_code=404)
        ctx, _ = _mock_client(resp)
        with patch("httpx.AsyncClient", return_value=ctx):
            assert _run(chat_session.fetch_agent_chat_session()) is None

    def test_connect_error_returns_none(self, monkeypatch):
        import httpx

        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-live")
        client = AsyncMock()
        client.post = AsyncMock(side_effect=httpx.ConnectError("down"))
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=ctx):
            assert _run(chat_session.fetch_agent_chat_session()) is None


class TestMatrixBotOneSoulLogin:
    def _bot(self):
        from windyfly.channels.matrix_bot import WindyFlyMatrixBot

        bot = WindyFlyMatrixBot.__new__(WindyFlyMatrixBot)
        bot.config = {}
        bot.bot_user_id = "@windyfly:chat.windychat.ai"
        bot.client = MagicMock()
        bot._hatch_dm_room_id = None
        bot._setup_encryption = AsyncMock()
        return bot

    def test_login_adopts_minted_identity_and_device(self):
        bot = self._bot()
        session = {
            "matrix_user_id": "@agent_et26-x:chat.windychat.ai",
            "access_token": "syt_minted",
            "device_id": "FLYDEV9",
            "dm_room_id": "!dm:chat.windychat.ai",
        }
        with patch(
            "windyfly.chat_session.fetch_agent_chat_session",
            AsyncMock(return_value=session),
        ):
            _run(bot.login())
        assert bot.bot_user_id == "@agent_et26-x:chat.windychat.ai"
        assert bot.client.access_token == "syt_minted"
        assert bot.client.device_id == "FLYDEV9"  # not "WindyFlyAgent"
        assert bot._hatch_dm_room_id == "!dm:chat.windychat.ai"
        bot._setup_encryption.assert_awaited()

    def test_login_falls_back_to_legacy_token(self, monkeypatch):
        bot = self._bot()
        monkeypatch.setenv("MATRIX_BOT_TOKEN", "legacy-token")
        with patch(
            "windyfly.chat_session.fetch_agent_chat_session",
            AsyncMock(return_value=None),
        ):
            _run(bot.login())
        assert bot.client.access_token == "legacy-token"
        assert bot.bot_user_id == "@windyfly:chat.windychat.ai"

    def test_login_raises_when_nothing_available(self, monkeypatch):
        bot = self._bot()
        monkeypatch.delenv("MATRIX_BOT_TOKEN", raising=False)
        monkeypatch.delenv("MATRIX_BOT_PASSWORD", raising=False)
        with patch(
            "windyfly.chat_session.fetch_agent_chat_session",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(RuntimeError):
                _run(bot.login())
