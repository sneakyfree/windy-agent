"""Cloud KE3 (Hub GO 10-10): Windy Cloud calls carry the agent's EPT+agent for aud windy-cloud (+ DPoP on writes)
first, and the old credential once on no token or a 401/403. WINDY_CLOUD_EPT_AGENT=0 = the old path."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from windyfly.agent import cloud_auth, service_auth

AGENT = {"Authorization": "DPoP ept-agent", "DPoP": "proof"}


@pytest.fixture
def minted(monkeypatch):
    calls = []

    def fake(aud, method, url):
        calls.append((aud, method, url))
        return dict(AGENT)

    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")
    monkeypatch.setattr(service_auth, "agent_headers", fake)
    return calls


def _resp(status, body=None):
    r = MagicMock(status_code=status)
    r.json.return_value = body if body is not None else {}
    r.text = ""
    return r


# ── the helper ───────────────────────────────────────────────────────────────────────────

def test_off_under_pytest_unless_set(monkeypatch):
    monkeypatch.delenv("WINDY_CLOUD_EPT_AGENT", raising=False)
    assert cloud_auth.enabled() is False
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")
    assert cloud_auth.enabled() is True
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "0")
    assert cloud_auth.enabled() is False and cloud_auth.agent_headers("POST", "https://x/y") is None


def test_mints_for_windy_cloud_with_the_public_url_and_no_query(minted):
    assert cloud_auth.agent_headers("POST", "https://cloud.windycloud.com/api/v1/archive/agent?x=1") == AGENT
    assert minted == [("windy-cloud", "POST", "https://cloud.windycloud.com/api/v1/archive/agent")]


def test_no_token_means_the_old_credential(monkeypatch):
    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "1")

    def boom(*a):
        raise service_auth.ServiceAuthError("no_key")

    monkeypatch.setattr(service_auth, "agent_headers", boom)
    assert cloud_auth.agent_headers("POST", "https://x/y") is None


@pytest.mark.parametrize("status,retry", [(401, True), (403, True), (200, False), (404, False), (500, False)])
def test_only_401_and_403_mean_retry(status, retry):
    assert cloud_auth.refused(status) is retry


# ── domains (cell 1: strict first) ───────────────────────────────────────────────────────

def test_domains_write_sends_the_agent_token_first(minted, monkeypatch):
    from windyfly.tools import windy_domains

    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "legacy-ept")
    sent = []
    monkeypatch.setattr("httpx.request", lambda m, u, **kw: sent.append((m, u, kw["headers"])) or _resp(200, {"ok": 1}))
    assert windy_domains._request("POST", "/buy", json={"fqdn": "a.com"}) == {"ok": 1}
    assert len(sent) == 1 and sent[0][2] == AGENT
    assert minted[0][:2] == ("windy-cloud", "POST")


def test_domains_refusal_retries_once_with_the_legacy_ept(minted, monkeypatch):
    from windyfly.tools import windy_domains

    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "legacy-ept")
    seq = [_resp(401), _resp(200, {"ok": 1})]
    sent = []
    monkeypatch.setattr("httpx.request", lambda m, u, **kw: sent.append(kw["headers"]) or seq.pop(0))
    assert windy_domains._request("GET", "/mine") == {"ok": 1}
    assert sent == [AGENT, {"Authorization": "Bearer legacy-ept"}]


def test_flag_off_is_the_old_path_exactly(monkeypatch):
    from windyfly.tools import windy_domains

    monkeypatch.setenv("WINDY_CLOUD_EPT_AGENT", "0")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "legacy-ept")
    sent = []
    monkeypatch.setattr("httpx.request", lambda m, u, **kw: sent.append(kw["headers"]) or _resp(200, {}))
    windy_domains._request("GET", "/mine")
    assert sent == [{"Authorization": "Bearer legacy-ept"}]


# ── files (upload re-sends the whole file on the retry) ──────────────────────────────────

def test_upload_retry_sends_the_whole_file_again(minted, monkeypatch, tmp_path):
    from windyfly.tools import cloud

    monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.windycloud.com")
    monkeypatch.setenv("WINDY_CLOUD_TOKEN", "old-cloud")
    f = tmp_path / "a.txt"
    f.write_bytes(b"hello cloud")
    seq = [_resp(403), _resp(201, {"file_id": "f1"})]
    seen = []

    def post(url, files, data, headers, timeout):
        seen.append((headers, files["file"][1].read()))
        return seq.pop(0)

    monkeypatch.setattr("httpx.post", post)
    out = cloud.upload_to_cloud(str(f))
    assert out.get("status") != "failed"
    assert seen == [(AGENT, b"hello cloud"), ({"Authorization": "Bearer old-cloud"}, b"hello cloud")]


# ── backups (core: Zero's ET26-T11V; the old path stays until the core flip) ─────────────

def test_backup_send_prefers_the_agent_token_and_falls_back_on_refusal(minted):
    from windyfly import cloud_backup

    calls = []

    class Client:
        async def post(self, url, headers=None, **kw):
            calls.append(headers)
            return _resp(401) if len(calls) == 1 else _resp(201)

    resp = asyncio.run(cloud_backup._cloud_send(Client(), "POST", "https://cloud.windycloud.com/api/v1/archive/agent",
                                                {"Authorization": "Bearer wk_old"}, "backup upload", files={}))
    assert resp.status_code == 201
    assert calls == [AGENT, {"Authorization": "Bearer wk_old"}]


# ── sites (the builder's MCP door is a write: DPoP) ──────────────────────────────────────

def test_builder_rpc_agent_token_then_old_token_on_refusal(minted, monkeypatch):
    from windyfly.tools import windycode_web

    seq = [_resp(401), _resp(200, {"result": {}})]
    sent = []
    monkeypatch.setattr("httpx.post", lambda url, json, headers, timeout: sent.append((url, headers)) or seq.pop(0))
    windycode_web._rpc("https://cloud.windycloud.com", "legacy-ept", "tools/call", {"name": "x", "arguments": {}})
    assert [h for _, h in sent] == [AGENT, {"Authorization": "Bearer legacy-ept"}]
    assert minted[0][1] == "POST" and minted[0][2].startswith("https://cloud.windycloud.com")
