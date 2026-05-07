"""Tests for the voice tool — make_call via the windy-call service.

Master plan codon **D.3.2**. Mirror of test_sms_tool.py for the voice
channel. Covers:
  - Tool registration adds expected tool with required args
  - Returns 'unavailable' when WINDY_PASSPORT_EPT unset
  - Returns 'failed' on bad E.164 input
  - Happy path: POSTs to windy-call /voice/call with right shape + auth
  - Voice value gets clamped to allow-list (alice on unknown)
  - Surfaces windy-call error_code verbatim on 4xx (e.g. 21219 trial)
  - 503 unavailable from windy-call mapped to status=failed
  - Boot sequence registers tools.voice after tools.sms
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.voice import make_call, register_voice_tools


@pytest.fixture
def windy_call_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate WINDY_CALL_BASE_URL + WINDY_PASSPORT_EPT for live path."""
    monkeypatch.setenv("WINDY_CALL_BASE_URL", "https://api.windycall.test")
    monkeypatch.setenv("WINDY_PASSPORT_EPT", "test_ept_jwt_value")


@pytest.fixture
def no_ept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip WINDY_PASSPORT_EPT so the tool returns the unavailable shape."""
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)


class TestRegistration:
    def test_registers_make_call(self) -> None:
        registry = ToolRegistry()
        register_voice_tools(registry)
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "make_call" in names

    def test_make_call_schema_requires_to_and_message(self) -> None:
        registry = ToolRegistry()
        register_voice_tools(registry)
        schema = next(
            s["function"] for s in registry.get_schemas()
            if s["function"]["name"] == "make_call"
        )
        assert set(schema["parameters"]["required"]) == {"to", "message"}
        # voice is optional with an enum
        assert "voice" in schema["parameters"]["properties"]
        assert "alice" in schema["parameters"]["properties"]["voice"]["enum"]


class TestUnavailable:
    def test_returns_unavailable_when_ept_unset(self, no_ept: None) -> None:
        result = make_call(to="+15551234567", message="hi")
        assert result["status"] == "unavailable"
        assert "WINDY_PASSPORT_EPT" in result["error"]


class TestValidation:
    @pytest.mark.parametrize("bad_to", [
        "5551234567",       # missing +
        "+",                # too short
        "+0123456789",      # leading 0 in country code
        "abc",              # not numeric
        "+1-555-123-4567",  # has dashes
    ])
    def test_bad_e164_rejected(
        self, windy_call_env: None, bad_to: str,
    ) -> None:
        result = make_call(to=bad_to, message="hi")
        assert result["status"] == "failed"
        assert "E.164" in result["error"]


class TestHappyPath:
    @patch("windyfly.tools.voice.httpx.post")
    def test_sends_to_correct_url_with_auth(
        self, mock_post: MagicMock, windy_call_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "sid": "CA1234",
            "status": "queued",
            "to": "+15551234567",
            "from": "+17542772201",
            "integrity_event_posted": True,
        }
        mock_post.return_value = mock_response

        result = make_call(to="+15551234567", message="hello", voice="alice")

        assert result["status"] == "sent"
        assert result["sid"] == "CA1234"
        assert result["to"] == "+15551234567"
        assert result["from"] == "+17542772201"
        assert result["integrity_event_posted"] is True

        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert url == "https://api.windycall.test/voice/call"
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test_ept_jwt_value"
        assert call_args.kwargs["json"] == {
            "to": "+15551234567",
            "message": "hello",
            "voice": "alice",
        }

    @patch("windyfly.tools.voice.httpx.post")
    def test_unknown_voice_falls_back_to_alice(
        self, mock_post: MagicMock, windy_call_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sid": "CA-2", "to": "+1", "from": "+2"}
        mock_post.return_value = mock_response

        make_call(to="+15551234567", message="x", voice="darth-vader")

        sent_json = mock_post.call_args.kwargs["json"]
        assert sent_json["voice"] == "alice"


class TestErrorPaths:
    @patch("windyfly.tools.voice.httpx.post")
    def test_4xx_with_error_code_surfaces_verbatim(
        self, mock_post: MagicMock, windy_call_env: None,
    ) -> None:
        # Twilio voice trial unverified-destination is 21219.
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "detail": "Twilio rejected: unverified destination",
            "error_code": 21219,
        }
        mock_post.return_value = mock_response

        result = make_call(to="+15551234567", message="hi")
        assert result["status"] == "failed"
        assert result["http_status"] == 400
        assert result["error_code"] == 21219
        assert "unverified" in result["error"]

    @patch("windyfly.tools.voice.httpx.post")
    def test_503_unavailable_from_windy_call(
        self, mock_post: MagicMock, windy_call_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {"detail": "Twilio client not configured"}
        mock_post.return_value = mock_response

        result = make_call(to="+15551234567", message="hi")
        assert result["status"] == "failed"
        assert result["http_status"] == 503

    @patch("windyfly.tools.voice.httpx.post")
    def test_connect_error_returns_failed(
        self, mock_post: MagicMock, windy_call_env: None,
    ) -> None:
        import httpx
        mock_post.side_effect = httpx.ConnectError("nope")

        result = make_call(to="+15551234567", message="hi")
        assert result["status"] == "failed"
        assert "Cannot reach" in result["error"]


class TestBootSequenceWiring:
    def test_default_sequence_includes_tools_voice(self) -> None:
        from windyfly.agent.boot import default_capability_registration_sequence

        sequence = default_capability_registration_sequence()
        names = [s.name for s in sequence]
        assert "tools.voice" in names
        # voice registers between sms and cloud per design.
        voice_idx = names.index("tools.voice")
        assert names[voice_idx - 1] == "tools.sms"
        assert names[voice_idx + 1] == "tools.cloud"
