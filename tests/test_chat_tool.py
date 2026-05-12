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
        # Chat registers right after mail. The slot after chat used to be
        # cloud; D.3.1 inserted sms between chat and cloud (both are "send
        # a message to a person" channels, paired in the registry).
        chat_idx = names.index("tools.chat")
        assert names[chat_idx - 1] == "tools.mail"
        assert names[chat_idx + 1] in ("tools.cloud", "tools.sms")


class TestTrustGate:
    """Trust gate (ADR-019 + ADR-020) — chat send gated by Eternitas
    Integrity Index when the agent has a passport.
    """

    @patch("windyfly.tools.chat.httpx.put")
    def test_no_passport_env_skips_trust_gate(
        self, mock_put: MagicMock, matrix_env: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ETERNITAS_PASSPORT set, the gate doesn't run and chat
        sends proceed normally. Matches test-rig + pre-hatch behavior."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"event_id": "$evt1"}
        mock_put.return_value = mock_response

        result = send_chat_message(body="hi", to_room="!a:test")
        assert result["status"] == "sent"
        # asyncio.run was NOT invoked
        mock_put.assert_called_once()

    @patch("windyfly.tools.chat.require_trust")
    @patch("windyfly.tools.chat.httpx.put")
    def test_passport_set_runs_trust_gate(
        self, mock_put: MagicMock, mock_require_trust: MagicMock,
        matrix_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """With ETERNITAS_PASSPORT set, the gate runs before HTTP."""
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-WIND-Y123")
        # require_trust is async; mock its coroutine to return a decision
        from windyfly.trust.check import TrustDecision, TrustSnapshot

        snapshot = TrustSnapshot(
            passport="ET26-WIND-Y123", status="active", band="good",
            clearance_level="verified", tier_multiplier=1.0,
            allowed_actions=("post_chat_message",), denied_actions=(),
            integrity_score=75.0, cache_ttl_seconds=300,
        )

        async def fake_require_trust(action, passport=None, db=None):
            return TrustDecision(allowed=True, snapshot=snapshot, reason="ok")

        mock_require_trust.side_effect = fake_require_trust
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"event_id": "$evt1"}
        mock_put.return_value = mock_response

        result = send_chat_message(body="hi", to_room="!a:test")
        assert result["status"] == "sent"
        mock_require_trust.assert_called_once_with("post_chat_message")

    @patch("windyfly.tools.chat.require_trust")
    @patch("windyfly.tools.chat.httpx.put")
    def test_trust_denied_returns_denied_status(
        self, mock_put: MagicMock, mock_require_trust: MagicMock,
        matrix_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When the gate denies, the chat tool returns status=denied and
        never makes the HTTP call. Matches the moat per ADR-019."""
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-WIND-Y123")
        from windyfly.trust.gate import TrustDenied

        async def fake_require_trust(action, passport=None, db=None):
            raise TrustDenied(
                action="post_chat_message", band="critical",
                reason="band=critical, all actions denied",
            )

        mock_require_trust.side_effect = fake_require_trust

        result = send_chat_message(body="hi", to_room="!a:test")
        assert result["status"] == "denied"
        assert result["band"] == "critical"
        assert result["action"] == "post_chat_message"
        assert "critical" in result["reason"]
        # HTTP was NOT called
        mock_put.assert_not_called()

    @patch("windyfly.tools.chat.require_trust")
    @patch("windyfly.tools.chat.httpx.put")
    def test_trust_check_exception_fails_open(
        self, mock_put: MagicMock, mock_require_trust: MagicMock,
        matrix_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """If the trust check ITSELF errors (Eternitas outage, network
        blip, SQLite missing), fail-open with a log line — don't block
        chat for transient outages of the trust kernel."""
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-WIND-Y123")

        async def fake_require_trust(action, passport=None, db=None):
            raise RuntimeError("eternitas unreachable")

        mock_require_trust.side_effect = fake_require_trust
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"event_id": "$evt1"}
        mock_put.return_value = mock_response

        result = send_chat_message(body="hi", to_room="!a:test")
        # Fail-open: chat still sent despite trust-check error
        assert result["status"] == "sent"
        mock_put.assert_called_once()
