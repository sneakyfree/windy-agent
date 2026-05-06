"""Tests for the chat tool — send_chat_message via Matrix REST.

Covers:
  - Tool registration adds expected tool
  - Returns 'unavailable' when MATRIX_HOMESERVER / TOKEN unset
  - Returns 'failed' when no room specified and MATRIX_DM_ROOM unset
  - Returns 'failed' on empty body
  - Happy path: PUTs the right URL with msgtype/body
  - Falls back to MATRIX_DM_ROOM when to_room omitted
  - Surfaces Matrix errcode/error verbatim on 4xx
  - Default sequence registers tools.chat
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.chat import (
    register_chat_tools,
    send_chat_message,
)
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def matrix_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate MATRIX_* env so the tool runs the live path."""
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.windyword.test")
    monkeypatch.setenv("MATRIX_BOT_USER", "@grant-fly:windyword.test")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "syt_test_token")


@pytest.fixture
def no_matrix_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip MATRIX_* so the tool returns the unavailable shape."""
    for var in ("MATRIX_HOMESERVER", "MATRIX_BOT_USER", "MATRIX_BOT_TOKEN", "MATRIX_DM_ROOM"):
        monkeypatch.delenv(var, raising=False)


class TestRegistration:
    def test_registers_send_chat_message(self) -> None:
        registry = ToolRegistry()
        register_chat_tools(registry)
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "send_chat_message" in names

    def test_send_chat_schema_requires_body(self) -> None:
        registry = ToolRegistry()
        register_chat_tools(registry)
        schema = next(
            s["function"] for s in registry.get_schemas()
            if s["function"]["name"] == "send_chat_message"
        )
        assert set(schema["parameters"]["required"]) == {"body"}


class TestUnavailable:
    def test_returns_unavailable_when_env_unset(self, no_matrix_env: None) -> None:
        result = send_chat_message(body="hello", to_room="!abc:test")
        assert result["status"] == "unavailable"
        assert "MATRIX_HOMESERVER" in result["error"]


class TestValidation:
    def test_no_room_and_no_default_returns_failed(
        self, matrix_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("MATRIX_DM_ROOM", raising=False)
        result = send_chat_message(body="hi")
        assert result["status"] == "failed"
        assert "to_room" in result["error"]

    def test_empty_body_returns_failed(self, matrix_env: None) -> None:
        result = send_chat_message(body="   ", to_room="!abc:test")
        assert result["status"] == "failed"
        assert "empty" in result["error"].lower()


class TestHappyPath:
    @patch("windyfly.tools.chat.httpx.put")
    def test_explicit_room_sends_to_correct_url(
        self, mock_put: MagicMock, matrix_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"event_id": "$evt:1"}
        mock_put.return_value = mock_response

        result = send_chat_message(body="hello world", to_room="!room1:test")

        assert result["status"] == "sent"
        assert result["event_id"] == "$evt:1"
        assert result["room"] == "!room1:test"

        call_args = mock_put.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert "/_matrix/client/v3/rooms/!room1:test/send/m.room.message/" in url
        assert call_args.kwargs["params"] == {"access_token": "syt_test_token"}
        assert call_args.kwargs["json"] == {"msgtype": "m.text", "body": "hello world"}

    @patch("windyfly.tools.chat.httpx.put")
    def test_default_room_falls_back_to_MATRIX_DM_ROOM(
        self, mock_put: MagicMock, matrix_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("MATRIX_DM_ROOM", "!default-dm:test")
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {"event_id": "$evt:2"}
        mock_put.return_value = mock_response

        result = send_chat_message(body="auto-routed")
        assert result["status"] == "sent"
        assert result["room"] == "!default-dm:test"
        url = mock_put.call_args.args[0]
        assert "!default-dm:test" in url


class TestErrorPaths:
    @patch("windyfly.tools.chat.httpx.put")
    def test_403_with_matrix_errcode_surfaces_verbatim(
        self, mock_put: MagicMock, matrix_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 403
        mock_response.json.return_value = {
            "errcode": "M_FORBIDDEN",
            "error": "User not in room",
        }
        mock_put.return_value = mock_response

        result = send_chat_message(body="hi", to_room="!nope:test")
        assert result["status"] == "failed"
        assert result["errcode"] == "M_FORBIDDEN"
        assert result["error"] == "User not in room"
        assert result["http_status"] == 403

    @patch("windyfly.tools.chat.httpx.put")
    def test_connect_error_returns_failed(
        self, mock_put: MagicMock, matrix_env: None,
    ) -> None:
        import httpx
        mock_put.side_effect = httpx.ConnectError("nope")

        result = send_chat_message(body="hi", to_room="!a:test")
        assert result["status"] == "failed"
        assert "Cannot reach" in result["error"]


class TestBootSequenceWiring:
    def test_default_sequence_includes_tools_chat(self) -> None:
        from windyfly.agent.boot import default_capability_registration_sequence

        sequence = default_capability_registration_sequence()
        names = [s.name for s in sequence]
        assert "tools.chat" in names
        # Chat registers between mail and cloud per design.
        chat_idx = names.index("tools.chat")
        assert names[chat_idx - 1] == "tools.mail"
        assert names[chat_idx + 1] == "tools.cloud"
