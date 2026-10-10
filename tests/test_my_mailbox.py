"""my_mailbox: the agent's own mailbox facts from Windy Mail GET /api/v1/mailbox (mail/mailbox.v1 1.0.0)."""

from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest

from windyfly.tools import mail

BODY = {"address": "fly@windymail.ai", "domain": "windymail.ai", "display_name": "Fly", "account_type": "bot",
        "status": "active", "passport": "ET26-TEST-0001", "aliases": [],
        "limits": {"tier": "free", "daily_sends": 50, "per_minute": 5, "recipients_per_message": 10}}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "fly@windymail.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "mbx-tok")
    monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.test/")


def _get(monkeypatch, status, body):
    calls = []

    def fake(url, **kw):
        calls.append((url, kw))
        r = MagicMock()
        r.status_code = status
        r.json.return_value = body
        return r

    monkeypatch.setattr("httpx.get", fake)
    return calls


def test_returns_the_contract_facts_with_my_own_bearer(monkeypatch):
    calls = _get(monkeypatch, 200, {**BODY, "extra_field": "x"})
    assert mail.my_mailbox() == BODY  # contract fields only
    url, kw = calls[0]
    assert url == "https://mail.test/api/v1/mailbox"
    assert kw["headers"] == {"Authorization": "Bearer mbx-tok"}


def test_no_mailbox_is_said_plainly(monkeypatch):
    _get(monkeypatch, 404, {"error": "no_mailbox"})
    assert mail.my_mailbox() == {"status": "error", "error": "Windy Mail has no mailbox for this agent."}


def test_mail_down_is_not_a_fact(monkeypatch):
    _get(monkeypatch, 503, {})
    assert mail.my_mailbox()["status"] == "error"
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    assert mail.my_mailbox() == {"status": "error", "error": "Could not reach Windy Mail."}


def test_not_configured(monkeypatch):
    monkeypatch.delenv("WINDYMAIL_EMAIL")
    assert mail.my_mailbox()["status"] == "unavailable"


def test_registered_with_no_arguments():
    from windyfly.tools.registry import ToolRegistry

    reg = ToolRegistry()
    mail.register_mail_tools(reg)
    schema = [s for s in reg.get_schemas() if "my_mailbox" in str(s)]
    assert len(schema) == 1 and '"properties": {}' in str(schema[0]).replace("'", '"')
