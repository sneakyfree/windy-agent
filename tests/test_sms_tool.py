"""Tests for the sms tool — send_sms via the windy-text service.

Master plan codon **D.3.1**. Mirror of test_chat_tool.py for the SMS
channel. Covers:
  - Tool registration adds expected tool
  - Returns 'unavailable' when WINDY_PASSPORT_EPT unset
  - Returns 'failed' on bad E.164 input
  - Happy path: POSTs to windy-text /sms/send with right shape
  - Surfaces windy-text error_code verbatim on 4xx (e.g. 21608 trial)
  - 503 unavailable from windy-text mapped to status=failed
  - Boot sequence registers tools.sms after tools.chat
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.sms import register_sms_tools, send_sms


@pytest.fixture
def windy_text_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Populate WINDY_TEXT_BASE_URL + WINDY_PASSPORT_EPT for live path."""
    monkeypatch.setenv("WINDY_TEXT_BASE_URL", "https://api.windytext.test")
    monkeypatch.setenv("WINDY_PASSPORT_EPT", "test_ept_jwt_value")


@pytest.fixture
def no_ept(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip WINDY_PASSPORT_EPT so the tool returns the unavailable shape."""
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)


class TestRegistration:
    def test_registers_send_sms(self) -> None:
        registry = ToolRegistry()
        register_sms_tools(registry)
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "send_sms" in names

    def test_send_sms_schema_requires_to_and_body(self) -> None:
        registry = ToolRegistry()
        register_sms_tools(registry)
        schema = next(
            s["function"] for s in registry.get_schemas()
            if s["function"]["name"] == "send_sms"
        )
        assert set(schema["parameters"]["required"]) == {"to", "body"}


class TestUnavailable:
    def test_returns_unavailable_when_ept_unset(self, no_ept: None) -> None:
        result = send_sms(to="+15551234567", body="hi")
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
        self, windy_text_env: None, bad_to: str,
    ) -> None:
        result = send_sms(to=bad_to, body="hi")
        assert result["status"] == "failed"
        assert "E.164" in result["error"]


class TestHappyPath:
    @patch("windyfly.tools.sms.httpx.post")
    def test_sends_to_correct_url_with_auth(
        self, mock_post: MagicMock, windy_text_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 201
        mock_response.json.return_value = {
            "sid": "SM1234",
            "status": "queued",
            "to": "+15551234567",
            "from": "+17542772201",
            "integrity_event_posted": True,
        }
        mock_post.return_value = mock_response

        result = send_sms(to="+15551234567", body="hello world")

        assert result["status"] == "sent"
        assert result["sid"] == "SM1234"
        assert result["to"] == "+15551234567"
        assert result["from"] == "+17542772201"
        assert result["integrity_event_posted"] is True

        call_args = mock_post.call_args
        url = call_args.args[0] if call_args.args else call_args.kwargs.get("url", "")
        assert url == "https://api.windytext.test/sms/send"
        assert call_args.kwargs["headers"]["Authorization"] == "Bearer test_ept_jwt_value"
        assert call_args.kwargs["json"] == {"to": "+15551234567", "body": "hello world"}

    @patch("windyfly.tools.sms.httpx.post")
    def test_200_status_treated_as_sent(
        self, mock_post: MagicMock, windy_text_env: None,
    ) -> None:
        # Some windy-text responses use 200 instead of 201 — both are happy.
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"sid": "SM-2", "to": "+1", "from": "+2"}
        mock_post.return_value = mock_response

        result = send_sms(to="+15551234567", body="x")
        assert result["status"] == "sent"
        assert result["sid"] == "SM-2"


class TestErrorPaths:
    @patch("windyfly.tools.sms.httpx.post")
    def test_4xx_with_error_code_surfaces_verbatim(
        self, mock_post: MagicMock, windy_text_env: None,
    ) -> None:
        # Twilio trial unverified-destination response (passes through windy-text).
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {
            "detail": "Twilio rejected: unverified destination",
            "error_code": 21608,
        }
        mock_post.return_value = mock_response

        result = send_sms(to="+15551234567", body="hi")
        assert result["status"] == "failed"
        assert result["http_status"] == 400
        assert result["error_code"] == 21608
        assert "unverified" in result["error"]

    @patch("windyfly.tools.sms.httpx.post")
    def test_503_unavailable_from_windy_text(
        self, mock_post: MagicMock, windy_text_env: None,
    ) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 503
        mock_response.json.return_value = {"detail": "Twilio client not configured"}
        mock_post.return_value = mock_response

        result = send_sms(to="+15551234567", body="hi")
        assert result["status"] == "failed"
        assert result["http_status"] == 503

    @patch("windyfly.tools.sms.httpx.post")
    def test_connect_error_returns_failed(
        self, mock_post: MagicMock, windy_text_env: None,
    ) -> None:
        import httpx
        mock_post.side_effect = httpx.ConnectError("nope")

        result = send_sms(to="+15551234567", body="hi")
        assert result["status"] == "failed"
        assert "Cannot reach" in result["error"]


class TestBootSequenceWiring:
    def test_default_sequence_includes_tools_sms(self) -> None:
        from windyfly.agent.boot import default_capability_registration_sequence

        sequence = default_capability_registration_sequence()
        names = [s.name for s in sequence]
        assert "tools.sms" in names
        # SMS registers between chat and cloud per design.
        sms_idx = names.index("tools.sms")
        assert names[sms_idx - 1] == "tools.chat"
        assert names[sms_idx + 1] == "tools.cloud"
