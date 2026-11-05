"""WINDY_HUB_URL is https only (http only to this computer): a stored EPT or an owner token never goes to a non-https
host (Hub's note on #724; Boss made it an item 11-05). The refusal comes BEFORE any request, for every caller."""

from __future__ import annotations

import logging

import httpx
import pytest
from rich.console import Console

from windyfly import hub_login


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("WINDY_HUB_URL", raising=False)
    monkeypatch.setattr(hub_login, "_refused_logged", False)


@pytest.mark.parametrize("url, want", [
    (None, "https://account.windyword.ai"),
    ("", "https://account.windyword.ai"),
    ("https://hub.lab.example/", "https://hub.lab.example"),
    ("http://localhost:8080", "http://localhost:8080"),
    ("http://127.0.0.1:9", "http://127.0.0.1:9"),
    ("http://[::1]:7000", "http://[::1]:7000"),
    ("HTTPS://Account.WindyWord.ai", "HTTPS://Account.WindyWord.ai"),
])
def test_https_and_local_http_are_used(monkeypatch, url, want):
    if url is not None:
        monkeypatch.setenv("WINDY_HUB_URL", url)
    assert hub_login.hub_url() == want


@pytest.mark.parametrize("url", [
    "http://account.windyword.ai", "http://evil.example", "http://user:hunter2@evil.example",
    "http://localhost.evil.example", "http://127.0.0.2", "ftp://account.windyword.ai",
    "account.windyword.ai", "https://", "https://[bad",
])
def test_anything_else_is_refused_and_never_named(monkeypatch, caplog, url):
    monkeypatch.setenv("WINDY_HUB_URL", url)
    with caplog.at_level(logging.WARNING, logger=hub_login.__name__):
        with pytest.raises(hub_login.HubUrlRefused) as got:
            hub_login.hub_url()
    assert isinstance(got.value, hub_login.LoginError) and isinstance(got.value, httpx.HTTPError)
    assert "https://" in str(got.value) and "hunter2" not in str(got.value) + caplog.text
    assert "evil" not in caplog.text


def test_the_warning_is_logged_once(monkeypatch, caplog):
    monkeypatch.setenv("WINDY_HUB_URL", "http://evil.example")
    with caplog.at_level(logging.WARNING, logger=hub_login.__name__):
        for _ in range(3):
            with pytest.raises(hub_login.HubUrlRefused):
                hub_login.hub_url()
    assert caplog.text.count("WINDY_HUB_URL is not https") == 1


def _no_request_transport(sent):
    return httpx.MockTransport(lambda req: sent.append(str(req.url)) or httpx.Response(200, json={}))


def test_a_hatch_ticket_stops_before_any_request(monkeypatch):
    from windyfly import hub_hatch

    monkeypatch.setenv("WINDY_HUB_URL", "http://evil.example")
    sent: list[str] = []
    t = hub_hatch.create_ticket("owner-token", "Pip", transport=_no_request_transport(sent))
    assert t.kind == "failed" and sent == []


def test_the_my_windy_read_stops_before_any_request(monkeypatch):
    from windyfly.agent import service_auth
    from windyfly.agent import your_windy as yw

    monkeypatch.setenv("WINDY_HUB_URL", "http://evil.example")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "stored-ept")
    monkeypatch.setattr(service_auth, "agent_headers",
                        lambda *a: (_ for _ in ()).throw(service_auth.ServiceAuthError("no_key")))
    sent: list[str] = []
    monkeypatch.setattr(httpx, "get", lambda url, **k: sent.append(url) or httpx.Response(200, json={}))
    with pytest.raises(hub_login.HubUrlRefused):   # inside the session read this is one omitted fact (got())
        yw._my_windy()
    assert sent == []


def test_bring_home_stops_before_the_sign_in_and_the_key_reset(monkeypatch, tmp_path):
    import io

    from windyfly import bring_home, hub_hatch
    from windyfly.eternitas import agent_keys

    monkeypatch.setenv("WINDY_HUB_URL", "http://evil.example")
    monkeypatch.setattr(hub_hatch, "cloud_agent", lambda: {"passport_number": "ET26-TEST-HUBU", "where": "cloud"})
    monkeypatch.setattr(bring_home, "_seal_path", lambda p: tmp_path / "none.json")
    monkeypatch.setattr(agent_keys, "reset", lambda **k: pytest.fail("no key reset"))
    buf = io.StringIO()
    rc = bring_home.run(Console(file=buf, width=200, color_system=None),
                        owner_token=lambda: pytest.fail("no sign-in"))
    assert rc == 1 and "isn't an https:// address" in buf.getvalue() and "Nothing was changed." in buf.getvalue()
