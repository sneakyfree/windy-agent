"""Mode B for the Windy Code builder (windy-code-web #96; Code's answers 2026-11-04), DARK behind
WINDY_CODE_EPT_AGENT=1.

- off (the default): nothing changes, the windy-cloud path is used exactly as before
- on: EPT+agent for aud windy-code + a DPoP proof (htm POST, htu = the full MCP URL, a new jti each request)
- 401 dpop_required / invalid_dpop / dpop_replay: ONE retry with a fresh proof, same token
- 401 wrong_audience / expired / invalid_token: ONE retry with a NEW token
- still 401, or a bare 401 (the builder's own switch is off): the old bearer ONCE
- 403 identity_locked and 503 revocation_status_unavailable never fall back to the old bearer
- no_forwardable_credential (Sites tools before KE5) reads as unavailable
"""

from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import service_auth
from windyfly.tools import windycode_web as wcw
from tests.test_agent_keys import env  # noqa: F401  (fixture)
from tests.test_agent_token_mode_b import registered  # noqa: F401  (fixture)

BASE = "https://cloud.windycloud.com"
MCP_URL = f"{BASE}/api/v1/projects/mcp"
POST = "windyfly.tools.windycode_web.httpx.post"
OLD = "Bearer ept_test_token"


@pytest.fixture
def builder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WINDY_CODE_WEB_URL", BASE)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.setenv("WINDY_CODE_EPT_AGENT", "1")


class Mint:
    """Stands in for service_auth.agent_headers: a new proof per call, a new token after forget_token()."""

    def __init__(self) -> None:
        self.generation = 0
        self.proofs = 0
        self.auds: list[str] = []
        self.urls: list[str] = []

    def headers(self, aud: str, method: str, url: str) -> dict[str, str]:
        self.auds.append(aud)
        self.urls.append(url)
        self.proofs += 1
        return {"Authorization": f"Bearer agent-token-{self.generation}", "DPoP": f"proof-{self.proofs}"}

    def forget(self) -> None:
        self.generation += 1


@pytest.fixture
def mint(monkeypatch: pytest.MonkeyPatch) -> Mint:
    m = Mint()
    monkeypatch.setattr(service_auth, "agent_headers", m.headers)
    monkeypatch.setattr(service_auth, "forget_token", m.forget)
    return m


def _ok() -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {}
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [], "structuredContent": {"projects": []}, "isError": False}}
    return resp


def _refused(status: int, code: str | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.headers = {"x-windy-code-auth-error": code} if code else {}
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32001, "message": "auth_required"}}
    return resp


def _auths(post: MagicMock) -> list[str]:
    return [c.kwargs["headers"]["Authorization"] for c in post.call_args_list]


def test_off_by_default_keeps_todays_path(builder: None, mint: Mint, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WINDY_CODE_EPT_AGENT")
    with patch("windyfly.agent.cloud_auth.agent_headers", return_value=None), patch(POST) as post:
        post.return_value = _ok()
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert _auths(post) == [OLD] and mint.auds == []  # no windy-code mint at all while dark


def test_on_sends_windy_code_token_and_proof_to_the_full_url(builder: None, mint: Mint) -> None:
    with patch(POST) as post:
        post.return_value = _ok()
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert post.call_count == 1
    assert mint.auds == ["windy-code"] and mint.urls == [MCP_URL]
    sent = post.call_args.kwargs["headers"]
    assert sent == {"Authorization": "Bearer agent-token-0", "DPoP": "proof-1"}


def test_every_request_gets_a_new_proof(builder: None, mint: Mint) -> None:
    with patch(POST) as post:
        post.return_value = _ok()
        wcw.windycodeweb_list_projects()
        wcw.windycodeweb_list_projects()
    assert [c.kwargs["headers"]["DPoP"] for c in post.call_args_list] == ["proof-1", "proof-2"]


@pytest.mark.parametrize("code", ["dpop_required", "invalid_dpop", "dpop_replay"])
def test_proof_refusal_retries_once_with_a_fresh_proof_same_token(builder: None, mint: Mint, code: str) -> None:
    with patch(POST) as post:
        post.side_effect = [_refused(401, code), _ok()]
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert _auths(post) == ["Bearer agent-token-0", "Bearer agent-token-0"]
    assert [c.kwargs["headers"]["DPoP"] for c in post.call_args_list] == ["proof-1", "proof-2"]


@pytest.mark.parametrize("code", ["wrong_audience", "expired", "invalid_token"])
def test_token_refusal_retries_once_with_a_new_token(builder: None, mint: Mint, code: str) -> None:
    with patch(POST) as post:
        post.side_effect = [_refused(401, code), _ok()]
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert _auths(post) == ["Bearer agent-token-0", "Bearer agent-token-1"]


def test_refused_twice_then_the_old_bearer_once(builder: None, mint: Mint) -> None:
    with patch(POST) as post:
        post.side_effect = [_refused(401, "invalid_token"), _refused(401, "invalid_token"), _ok()]
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert _auths(post) == ["Bearer agent-token-0", "Bearer agent-token-1", OLD]
    assert mint.generation == 1  # re-minted ONCE, never a loop


def test_bare_401_while_the_builder_switch_is_off_goes_straight_to_the_old_bearer(builder: None, mint: Mint) -> None:
    with patch(POST) as post:
        post.side_effect = [_refused(401), _ok()]
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert _auths(post) == ["Bearer agent-token-0", OLD] and mint.generation == 0


def test_old_bearer_is_logged_so_an_after_proof_can_see_it(builder: None, mint: Mint,
                                                           caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="windyfly.tools.windycode_web")
    with patch(POST) as post:
        post.side_effect = [_refused(401), _ok()]
        wcw.windycodeweb_list_projects()
    assert "retried with the old one" in caplog.text
    caplog.clear()
    with patch(POST) as post:
        post.return_value = _ok()
        wcw.windycodeweb_list_projects()
    assert "retried with the old one" not in caplog.text


@pytest.mark.parametrize("status", [403, 503])
def test_locked_or_revocation_unknown_never_falls_back(builder: None, mint: Mint, status: int) -> None:
    with patch(POST) as post:
        post.return_value = _refused(status)
        out = wcw.windycodeweb_list_projects()
    assert out["status"] == "failed" and out["http_status"] == status
    assert post.call_count == 1 and OLD not in _auths(post) and mint.generation == 0


def test_no_mint_means_the_old_bearer(builder: None, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(aud: str, method: str, url: str) -> dict[str, str]:
        raise service_auth.ServiceAuthError("no_registered_key")

    monkeypatch.setattr(service_auth, "agent_headers", refuse)
    with patch(POST) as post:
        post.return_value = _ok()
        assert wcw.windycodeweb_list_projects()["status"] == "ok"
    assert _auths(post) == [OLD]


@pytest.mark.parametrize("is_error", [False, True])
def test_no_forwardable_credential_reads_as_unavailable(builder: None, mint: Mint, is_error: bool) -> None:
    # The builder's real shape (Code, 11-04, its #96 follow-up e6cf623): HTTP 200, isError FALSE,
    # structuredContent {code, speak, failed: true, remediation_tool}. Keyed on failed/code, never isError alone.
    resp = _ok()
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {
        "content": [], "isError": is_error,
        "structuredContent": {"code": "no_forwardable_credential", "speak": "builder words", "failed": True,
                              "remediation_tool": "get_capabilities"}}}
    with patch(POST) as post:
        post.return_value = resp
        out = wcw.windycodeweb_create_project("Garden Club")
    assert out["status"] == "unavailable" and out["speak"] == "builder words"
    assert out["remediation_tool"] == "get_capabilities" and "failed" not in out


def _claims(jws: str, part: int) -> dict:
    s = jws.split(".")[part]
    return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))


def test_the_real_proof_names_post_and_the_full_mcp_url(builder: None, registered: str,  # noqa: F811
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    from windyfly.eternitas import agent_keys as ak

    auds: list[str] = []

    def fake_mint(aud: str, **_: object) -> dict[str, str]:
        auds.append(aud)
        return {"token": "agent-token"}

    monkeypatch.setattr(ak, "request_agent_token", fake_mint)
    with patch(POST) as post:
        post.return_value = _ok()
        wcw.windycodeweb_list_projects()
        wcw.windycodeweb_list_projects()
    proofs = [c.kwargs["headers"]["DPoP"] for c in post.call_args_list]
    head, body = _claims(proofs[0], 0), _claims(proofs[0], 1)
    assert auds == ["windy-code", "windy-code"]
    assert head["typ"] == "dpop+jwt" and head["alg"] == "ES256" and "d" not in head["jwk"]
    assert body["htm"] == "POST" and body["htu"] == MCP_URL
    assert 8 <= len(body["jti"]) <= 128
    assert _claims(proofs[1], 1)["jti"] != body["jti"]  # single-use: a new jti every request
