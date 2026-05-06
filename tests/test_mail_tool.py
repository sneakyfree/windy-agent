"""Tests for the mail tool — send_email + list_inbox.

Covers:
  - Tool registration adds expected tools to the registry
  - send_email returns 'unavailable' when WINDYMAIL_EMAIL is unset
  - send_email single-recipient happy path through a mocked adapter
  - send_email comma-separated multi-recipient with mixed results
  - list_inbox returns adapter messages, trimmed by limit
  - list_inbox returns 'unavailable' when adapter init fails
  - The boot sequence registers tools.mail by default
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.mail import (
    list_inbox,
    register_mail_tools,
    send_email,
)
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def no_mail_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip WINDYMAIL_* so adapter init fails the way prod expects.

    Uses monkeypatch (not direct os.environ mutation) so removal is
    scoped to the test and doesn't leak into other test modules.
    """
    for var in ("WINDYMAIL_EMAIL", "WINDYMAIL_JMAP_TOKEN"):
        monkeypatch.delenv(var, raising=False)


class TestRegistration:
    def test_registers_send_email_and_list_inbox(self) -> None:
        registry = ToolRegistry()
        register_mail_tools(registry)
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "send_email" in names
        assert "list_inbox" in names

    def test_send_email_schema_requires_to_subject_body(self) -> None:
        registry = ToolRegistry()
        register_mail_tools(registry)
        send_schema = next(
            s["function"] for s in registry.get_schemas()
            if s["function"]["name"] == "send_email"
        )
        required = set(send_schema["parameters"]["required"])
        assert required == {"to", "subject", "body"}


class TestSendEmailUnavailable:
    def test_returns_unavailable_when_env_unset(self, no_mail_env: None) -> None:
        result = send_email(
            to="someone@example.com",
            subject="hi",
            body="hello",
        )
        assert result["status"] == "unavailable"
        assert "WINDYMAIL_EMAIL" in result["error"]

    def test_executes_via_registry(self, no_mail_env: None) -> None:
        # The registry path (LLM dispatch) should also surface unavailable
        # cleanly rather than raising, since adapter-init errors are caught
        # in the tool, not the registry.
        registry = ToolRegistry()
        register_mail_tools(registry)
        out = registry.execute(
            "send_email",
            {"to": "x@y.com", "subject": "s", "body": "b"},
        )
        assert "unavailable" in out


class TestSendEmailHappyPath:
    @patch("windyfly.tools.mail._adapter")
    def test_single_recipient_passes_through(self, mock_adapter: MagicMock) -> None:
        adapter = MagicMock()
        adapter.send_email.return_value = {"status": "sent", "message_id": "abc-123"}
        mock_adapter.return_value = adapter

        result = send_email(
            to="alice@example.com",
            subject="Hello",
            body="Body text",
        )

        adapter.send_email.assert_called_once_with("alice@example.com", "Hello", "Body text")
        assert result == {"status": "sent", "message_id": "abc-123"}

    @patch("windyfly.tools.mail._adapter")
    def test_no_recipients_after_split_is_failed(self, mock_adapter: MagicMock) -> None:
        # Mock so adapter is configured; the failure must come from validation,
        # not from the unavailable path.
        adapter = MagicMock()
        mock_adapter.return_value = adapter

        result = send_email(to="  ,  ,", subject="hi", body="b")
        assert result["status"] == "failed"
        adapter.send_email.assert_not_called()


class TestSendEmailMultiRecipient:
    @patch("windyfly.tools.mail._adapter")
    def test_all_succeed_returns_sent(self, mock_adapter: MagicMock) -> None:
        adapter = MagicMock()
        adapter.send_email.return_value = {"status": "sent", "message_id": "m"}
        mock_adapter.return_value = adapter

        result = send_email(
            to="a@x.com, b@x.com, c@x.com",
            subject="s",
            body="b",
        )

        assert result["status"] == "sent"
        assert result["successes"] == 3
        assert result["total"] == 3
        assert len(result["per_recipient"]) == 3
        assert adapter.send_email.call_count == 3

    @patch("windyfly.tools.mail._adapter")
    def test_mixed_results_returns_partial(self, mock_adapter: MagicMock) -> None:
        adapter = MagicMock()
        adapter.send_email.side_effect = [
            {"status": "sent", "message_id": "m1"},
            {"status": "failed", "error": "spam suspected"},
        ]
        mock_adapter.return_value = adapter

        result = send_email(to="a@x.com, b@x.com", subject="s", body="b")
        assert result["status"] == "partial"
        assert result["successes"] == 1
        assert result["total"] == 2

    @patch("windyfly.tools.mail._adapter")
    def test_all_fail_returns_failed(self, mock_adapter: MagicMock) -> None:
        adapter = MagicMock()
        adapter.send_email.return_value = {"status": "failed", "error": "boom"}
        mock_adapter.return_value = adapter

        result = send_email(to="a@x.com, b@x.com", subject="s", body="b")
        assert result["status"] == "failed"
        assert result["successes"] == 0

    @patch("windyfly.tools.mail._adapter")
    def test_adapter_exception_is_caught_per_recipient(self, mock_adapter: MagicMock) -> None:
        # Trust gate / rate limiter raise; per-recipient capture means one
        # failure doesn't abort the whole send.
        adapter = MagicMock()
        adapter.send_email.side_effect = [
            {"status": "sent", "message_id": "m"},
            RuntimeError("trust gate denied"),
        ]
        mock_adapter.return_value = adapter

        result = send_email(to="a@x.com, b@x.com", subject="s", body="b")
        assert result["status"] == "partial"
        assert result["per_recipient"][1]["status"] == "failed"
        assert "trust gate denied" in result["per_recipient"][1]["error"]


class TestListInbox:
    @patch("windyfly.tools.mail._adapter")
    def test_returns_messages_trimmed_by_limit(self, mock_adapter: MagicMock) -> None:
        adapter = MagicMock()
        adapter.check_inbox.return_value = [
            {"from": f"a{i}@x.com", "subject": f"s{i}"} for i in range(50)
        ]
        mock_adapter.return_value = adapter

        result = list_inbox(unread_only=False, limit=10)
        assert result["count"] == 10
        assert len(result["messages"]) == 10
        assert result["unread_only"] is False
        adapter.check_inbox.assert_called_once_with(unread_only=False)

    def test_unavailable_when_env_unset(self, no_mail_env: None) -> None:
        result = list_inbox()
        assert result["status"] == "unavailable"
        assert result["messages"] == []


class TestBootSequenceWiring:
    def test_default_sequence_includes_tools_mail(self) -> None:
        from windyfly.agent.boot import default_capability_registration_sequence

        sequence = default_capability_registration_sequence()
        names = [s.name for s in sequence]
        assert "tools.mail" in names
        # Mail registers between windy_api and web_search per design.
        assert names.index("tools.mail") == names.index("tools.windy_api") + 1
