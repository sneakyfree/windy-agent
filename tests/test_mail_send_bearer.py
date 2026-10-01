"""WINDY_MAIL_SEND_EPT=1: /api/v1/send uses the agent's EPT; inbox reads keep the JMAP token."""
import pytest

from windyfly.channels.email import WindyMailAdapter


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "zero@windymail.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "jmap-tok")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-tok")
    return WindyMailAdapter()


def test_default_keeps_legacy_order(adapter, monkeypatch):
    monkeypatch.delenv("WINDY_MAIL_SEND_EPT", raising=False)
    assert adapter._send_bearer() == "jmap-tok"


def test_flag_sends_with_the_ept(adapter, monkeypatch):
    monkeypatch.setenv("WINDY_MAIL_SEND_EPT", "1")
    assert adapter._send_bearer() == "ept-tok"
    assert adapter.jmap_token == "jmap-tok"  # inbox reads unchanged


def test_flag_without_ept_falls_back(adapter, monkeypatch):
    monkeypatch.setenv("WINDY_MAIL_SEND_EPT", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", " ")
    assert adapter._send_bearer() == "jmap-tok"


def test_send_request_uses_the_chosen_bearer(adapter, monkeypatch):
    import httpx

    monkeypatch.setenv("WINDY_MAIL_SEND_EPT", "1")
    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen["auth"] = headers["Authorization"]
        return httpx.Response(202, json={"id": "m1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    adapter.db = None
    adapter.send_email("a@b.com", "hi", "body")
    assert seen["auth"] == "Bearer ept-tok"
