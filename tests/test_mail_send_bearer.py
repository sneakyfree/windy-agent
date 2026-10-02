"""/api/v1/send always uses the agent's EPT when present (Hub, 10-02; was WINDY_MAIL_SEND_EPT=1);
inbox reads keep the JMAP token."""
import pytest

from windyfly.channels.email import WindyMailAdapter


@pytest.fixture
def adapter(monkeypatch):
    monkeypatch.setenv("WINDYMAIL_EMAIL", "zero@windymail.ai")
    monkeypatch.setenv("WINDYMAIL_JMAP_TOKEN", "jmap-tok")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-tok")
    monkeypatch.delenv("WINDY_MAIL_SEND_EPT", raising=False)
    return WindyMailAdapter()


def test_sends_with_the_ept_by_default(adapter):
    assert adapter._send_bearer() == "ept-tok"
    assert adapter.jmap_token == "jmap-tok"  # inbox reads unchanged


def test_without_an_ept_still_mail_with_the_jmap_token(adapter, monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", " ")
    assert adapter._send_bearer() == "jmap-tok"


def test_send_request_uses_the_ept_bearer(adapter, monkeypatch):
    import httpx

    seen = {}

    def post(url, json=None, headers=None, timeout=None):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        return httpx.Response(202, json={"id": "m1"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", post)
    adapter.db = None
    adapter.send_email("a@b.com", "hi", "body")
    assert seen["auth"] == "Bearer ept-tok"
    assert seen["url"].endswith("/api/v1/send")
