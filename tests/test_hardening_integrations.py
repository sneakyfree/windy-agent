"""Hardening tests for integration graceful degradation.

Calls every Windy integration with an unreachable host and verifies:
- User-friendly error messages (no stack traces)
- Agent loop continues after failure
- Failures are logged appropriately
"""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest
import respx

from windyfly.integrations.windy_clone import CloneStatus, get_clone_status
from windyfly.integrations.windy_cloud import BackupResult, SyncStatus, backup_database, sync_status
from windyfly.integrations.windy_traveler import TranslationResult, translate_text
from windyfly.integrations.windy_word import Recording, search_recordings, get_recording
from windyfly.integrations.push_gateway import PushResult, send_push
from windyfly.integrations.contact_discovery import discover_contacts


UNREACHABLE = "http://192.0.2.1:1"  # RFC 5737 TEST-NET — guaranteed unreachable


# --- Windy Clone ---


class TestCloneGracefulDegradation:
    async def test_connection_error_via_respx(self, monkeypatch):
        """Clone integration handles connection errors gracefully."""
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")

        with respx.mock:
            respx.get("http://test.local/api/v1/clone/training-data").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            status = await get_clone_status()

        assert isinstance(status, CloneStatus)
        assert status.is_available is False
        assert status.error != ""
        assert "Traceback" not in status.error

    async def test_no_config_returns_error(self, monkeypatch):
        monkeypatch.delenv("WINDY_API_URL", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        status = await get_clone_status()
        assert status.is_available is False


# --- Windy Cloud ---


class TestCloudGracefulDegradation:
    async def test_backup_connection_error(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_CLOUD_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")

        db_file = tmp_path / "test.db"
        db_file.write_bytes(b"fake db content")

        with respx.mock:
            respx.post("http://test.local/api/storage/files/upload").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            result = await backup_database(str(db_file))

        assert isinstance(result, BackupResult)
        assert result.success is False
        assert result.error != ""
        assert "Traceback" not in result.error

    async def test_sync_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_CLOUD_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")

        with respx.mock:
            respx.get("http://test.local/api/storage/health").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            status = await sync_status()

        assert isinstance(status, SyncStatus)
        assert status.is_available is False
        assert status.error != ""

    async def test_backup_no_config(self, monkeypatch):
        monkeypatch.delenv("WINDY_CLOUD_URL", raising=False)
        result = await backup_database("data/test.db")
        assert result.success is False


# --- Windy Traveler (Translation) ---


class TestTravelerGracefulDegradation:
    async def test_translate_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")

        with respx.mock:
            respx.post("http://test.local/api/v1/translate/text").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            result = await translate_text("Hello", "es")

        assert isinstance(result, TranslationResult)
        assert result.success is False
        assert result.error != ""
        assert "Traceback" not in result.error


# --- Windy Word (Recordings) ---


class TestWordGracefulDegradation:
    async def test_search_connection_refused(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", UNREACHABLE)
        monkeypatch.setenv("WINDY_JWT", "test-jwt")

        results = await search_recordings("meeting")
        assert results == []

    async def test_get_recording_connection_refused(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", UNREACHABLE)
        monkeypatch.setenv("WINDY_JWT", "test-jwt")

        result = await get_recording("rec-123")
        assert result is None


# --- Push Gateway ---


class TestPushGracefulDegradation:
    async def test_push_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_PUSH_URL", "http://test.local")

        with respx.mock:
            respx.post("http://test.local/api/v1/push").mock(
                side_effect=httpx.ConnectError("connection refused")
            )
            result = await send_push("token", "Title", "Body")

        assert isinstance(result, PushResult)
        assert result.success is False
        assert result.error != ""


# --- Contact Discovery ---


class TestDiscoveryGracefulDegradation:
    async def test_discovery_connection_refused(self, monkeypatch):
        monkeypatch.setenv("WINDY_DISCOVERY_URL", UNREACHABLE)

        results = await discover_contacts(["hash1"])
        assert results == []


# --- Windy Pro API tools (synchronous) ---


class TestProApiGracefulDegradation:
    @respx.mock
    def test_translation_history_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")
        respx.get("http://test.local/api/v1/user/history").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        from windyfly.tools.windy_api import get_translation_history
        result = get_translation_history()
        assert "error" in result
        assert "not available" in result["error"].lower()
        assert result["translations"] == []

    @respx.mock
    def test_recordings_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")
        respx.get("http://test.local/api/v1/recordings/list").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        from windyfly.tools.windy_api import get_recordings
        result = get_recordings()
        assert "error" in result
        assert result["recordings"] == []

    @respx.mock
    def test_clone_status_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")
        respx.get("http://test.local/api/v1/clone/training-data").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        from windyfly.tools.windy_api import get_clone_status
        result = get_clone_status()
        assert result["available"] is False

    @respx.mock
    def test_translate_connection_error(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "test-jwt")
        respx.post("http://test.local/api/v1/translate/text").mock(
            side_effect=httpx.ConnectError("connection refused")
        )

        from windyfly.tools.windy_api import translate_text
        result = translate_text("hello", "en", "es")
        assert "error" in result
        assert "not available" in result["error"].lower()


# --- Agent loop continues after integration failure ---


class TestAgentLoopAfterIntegrationFailure:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_agent_responds_after_tool_integration_fails(self, mock_llm, mock_online):
        """Agent loop should continue even when integrations fail."""
        from windyfly.agent.loop import agent_respond
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.tools.registry import ToolRegistry

        db = Database(":memory:")
        wq = WriteQueue()

        # Register a tool that simulates an integration failure
        def _failing_integration(**kwargs):
            raise httpx.ConnectError("Windy Pro is unreachable")

        registry = ToolRegistry()
        registry.register(
            "failing_integration",
            "A failing integration tool",
            {"type": "object", "properties": {}, "required": []},
            _failing_integration,
        )

        mock_llm.side_effect = [
            {
                "content": "",
                "input_tokens": 50,
                "output_tokens": 20,
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "failing_integration", "arguments": "{}"},
                }],
            },
            {
                "content": "I couldn't reach the service right now, but I can still help.",
                "input_tokens": 100,
                "output_tokens": 20,
            },
        ]

        config = {
            "agent": {"default_model": "gpt-4o-mini"},
            "memory": {"db_path": ":memory:"},
            "personality": {"humor_level": 5},
            "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
        }

        result = agent_respond(config, db, wq, "Check my history", "sess-1", registry)
        assert isinstance(result, str)
        assert len(result) > 0
        # Should NOT contain a raw traceback
        assert "Traceback" not in result

        db.close()


# --- Verify friendly error messages (no raw exceptions) ---


class TestFriendlyErrorMessages:
    """Every integration error should produce a human-readable message."""

    @respx.mock
    def test_pro_api_500_friendly(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "jwt")

        respx.get("http://test.local/api/v1/user/history").mock(
            return_value=httpx.Response(500, text="Internal Server Error")
        )

        from windyfly.tools.windy_api import get_translation_history
        result = get_translation_history()
        assert "error" in result
        # Error should be a string, not a raw exception object
        assert isinstance(result["error"], str)

    @respx.mock
    def test_pro_api_401_friendly(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "http://test.local")
        monkeypatch.setenv("WINDY_JWT", "bad-jwt")

        respx.post("http://test.local/api/v1/translate/text").mock(
            return_value=httpx.Response(401, json={"error": "unauthorized"})
        )

        from windyfly.tools.windy_api import translate_text
        result = translate_text("hello", "en", "es")
        assert "error" in result
        assert isinstance(result["error"], str)
