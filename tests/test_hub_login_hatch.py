"""Terminal hatch signs in with the owner's Windy account (hub JWT).

Eternitas is closing the anonymous /bots/auto-hatch door
(AUTO_HATCH_REQUIRE_PRO_JWT). The terminal's credential is the owner's
hub login token, obtained by loopback + PKCE (windyfly.hub_login).
"""

from __future__ import annotations

import base64
import json
import stat
import threading
import time
import urllib.parse
import urllib.request

import httpx
import pytest

from windyfly import hub_login
from windyfly.auth.jwt_claims import identity_from_jwt


def _b64(obj: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()


def make_jwt(claims: dict, alg: str = "RS256") -> str:
    return f"{_b64({'alg': alg, 'typ': 'JWT'})}.{_b64(claims)}.c2ln"


HUMAN = {"type": "human", "windy_identity_id": "5e1b9569-aaaa", "sub": "2f94efa7-hubuser",
         "exp": int(time.time()) + 900}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    for var in ("ETERNITAS_OPERATOR_JWT", "WINDY_HUB_JWT", "WINDY_HUB_URL",
                "HUB_OAUTH_CLIENT_ID", "WINDY_HATCH_NONINTERACTIVE", "WINDY_JWT",
                "WINDY_IDENTITY_ID"):
        monkeypatch.delenv(var, raising=False)


def _token_transport(record: list, body: dict | None = None, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        record.append(dict(urllib.parse.parse_qsl(request.content.decode())))
        assert request.url.path == "/api/v1/oauth/token"
        return httpx.Response(status, json=body if body is not None else {
            "access_token": make_jwt(HUMAN), "refresh_token": "rt-1", "expires_in": 900,
        })
    return httpx.MockTransport(handler)


def _play_browser(state_override: str | None = None, code: str = "code-123"):
    """on_url callback: behave like the hub redirecting the browser back."""
    def on_url(url: str) -> None:
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        redirect = q["redirect_uri"]
        state = state_override if state_override is not None else q["state"]

        def go():
            urllib.request.urlopen(
                f"{redirect}?{urllib.parse.urlencode({'code': code, 'state': state})}", timeout=5
            ).read()

        threading.Thread(target=go, daemon=True).start()
    return on_url


# ── PKCE loopback login ───────────────────────────────────────────────

def test_authorize_url_is_pkce_s256_on_ip_literal_loopback():
    verifier, challenge = hub_login.new_pkce_pair()
    assert 43 <= len(verifier) <= 128
    url = hub_login.build_authorize_url(hub_login.redirect_uri_for(51234), "st", challenge)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    assert url.startswith("https://account.windyword.ai/api/v1/oauth/authorize?")
    assert q["client_id"] == "windy-fly-dashboard"
    assert q["code_challenge_method"] == "S256" and q["code_challenge"] == challenge
    assert q["redirect_uri"] == "http://127.0.0.1:51234/api/auth/hub/callback"
    assert "localhost" not in url


def test_login_round_trip_stores_0600_session_keyed_on_windy_identity_id():
    calls: list = []
    who = hub_login.login(open_browser=False, timeout=10, transport=_token_transport(calls),
                          on_url=_play_browser(), echo=lambda _m: None)
    assert who == {"windy_identity_id": "5e1b9569-aaaa"}  # NOT sub
    form = calls[0]
    assert form["grant_type"] == "authorization_code" and form["code"] == "code-123"
    assert form["code_verifier"] and form["client_id"] == "windy-fly-dashboard"
    assert form["redirect_uri"].startswith("http://127.0.0.1:")
    path = hub_login.session_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    saved = json.loads(path.read_text())
    assert saved["windy_identity_id"] == "5e1b9569-aaaa" and saved["refresh_token"] == "rt-1"


def test_state_mismatch_is_rejected_and_no_session_written():
    calls: list = []
    with pytest.raises(hub_login.LoginError, match="state mismatch"):
        hub_login.login(open_browser=False, timeout=10, transport=_token_transport(calls),
                        on_url=_play_browser(state_override="forged"), echo=lambda _m: None)
    assert calls == []  # never exchanged the code
    assert not hub_login.session_path().exists()


def test_login_times_out_cleanly():
    with pytest.raises(hub_login.LoginError, match="No sign-in"):
        hub_login.login(open_browser=False, timeout=0.3, echo=lambda _m: None)


def test_token_without_identity_claim_is_refused():
    calls: list = []
    no_id = {"access_token": make_jwt({"type": "human", "sub": "x"}), "expires_in": 900}
    with pytest.raises(hub_login.LoginError, match="identity"):
        hub_login.login(open_browser=False, timeout=10, transport=_token_transport(calls, no_id),
                        on_url=_play_browser(), echo=lambda _m: None)


def test_login_never_prints_tokens():
    printed: list[str] = []
    hub_login.login(open_browser=False, timeout=10, transport=_token_transport([]),
                    on_url=_play_browser(), echo=printed.append)
    out = "\n".join(printed)
    assert "rt-1" not in out and make_jwt(HUMAN) not in out


# ── refresh ───────────────────────────────────────────────────────────

def test_get_access_token_returns_valid_token_without_network():
    hub_login._write_session({"access_token": "live", "refresh_token": "rt",
                              "expires_at": time.time() + 600, "windy_identity_id": "id"})
    assert hub_login.get_access_token(transport=httpx.MockTransport(
        lambda r: pytest.fail("should not refresh"))) == "live"


def test_get_access_token_refreshes_near_expiry_and_keeps_refresh_token():
    hub_login._write_session({"access_token": "old", "refresh_token": "rt-keep",
                              "expires_at": time.time() + 10, "windy_identity_id": "5e1b9569-aaaa"})
    calls: list = []
    fresh = make_jwt(HUMAN)
    tok = hub_login.get_access_token(transport=_token_transport(
        calls, {"access_token": fresh, "expires_in": 900}))
    assert tok == fresh
    assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "rt-keep"
    assert json.loads(hub_login.session_path().read_text())["refresh_token"] == "rt-keep"


def test_get_access_token_none_when_refresh_fails_or_no_session():
    assert hub_login.get_access_token() is None
    hub_login._write_session({"access_token": "old", "refresh_token": "rt",
                              "expires_at": time.time() - 5, "windy_identity_id": "id"})
    assert hub_login.get_access_token(transport=_token_transport([], {"error": "x"}, status=400)) is None


# ── auto_hatch credential precedence ─────────────────────────────────

def test_precedence_operator_then_hub_env_then_session_then_none(monkeypatch):
    from windyfly.eternitas.client import auto_hatch_credential

    assert auto_hatch_credential() == ("", "none")
    hub_login._write_session({"access_token": "sess", "refresh_token": "",
                              "expires_at": time.time() + 600, "windy_identity_id": "id"})
    assert auto_hatch_credential() == ("sess", "windy login session")
    hub = make_jwt(HUMAN)
    monkeypatch.setenv("WINDY_HUB_JWT", hub)
    assert auto_hatch_credential() == (hub, "WINDY_HUB_JWT")
    monkeypatch.setenv("ETERNITAS_OPERATOR_JWT", "op-jwt")
    assert auto_hatch_credential() == ("op-jwt", "operator JWT")


def test_windy_hub_jwt_must_look_like_a_human_rs256_hub_token(monkeypatch):
    from windyfly.eternitas.client import auto_hatch_credential

    for bad in ("not-a-jwt",
                make_jwt(HUMAN, alg="ES256"),                      # an EPT-style token
                make_jwt({**HUMAN, "type": "agent"}),
                make_jwt({"type": "human", "sub": "only-sub"})):  # no identity claim
        monkeypatch.setenv("WINDY_HUB_JWT", bad)
        assert auto_hatch_credential() == ("", "none")


def _eternitas_401(monkeypatch, captured: dict):
    import windyfly.eternitas.client as client_mod

    real = httpx.AsyncClient

    def factory(*a, **kw):
        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            return httpx.Response(401, json={"detail": "auth required"})
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)

    monkeypatch.setattr(client_mod.httpx, "AsyncClient", factory)


def _request():
    from windyfly.eternitas.models import RegistrationRequest
    return RegistrationRequest(name="Test Fly", description="t", bot_type="personal_assistant",
                               contact_email="", intended_platforms=["windy_chat"])


def _client():
    from windyfly.eternitas.client import EternitasClient
    import inspect
    params = inspect.signature(EternitasClient).parameters
    kwargs = {}
    if "api_url" in params:
        kwargs["api_url"] = "https://eternitas.test"
    return EternitasClient(**kwargs)


@pytest.mark.asyncio
async def test_401_without_credential_tells_user_to_windy_login(monkeypatch):
    from windyfly.eternitas.client import HatchAuthRequired

    captured: dict = {}
    _eternitas_401(monkeypatch, captured)
    with pytest.raises(HatchAuthRequired, match="windy login"):
        await _client().auto_hatch(_request())
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_401_with_session_sends_bearer_and_says_sign_in_rejected(monkeypatch):
    from windyfly.eternitas.client import HatchAuthRequired

    hub_login._write_session({"access_token": "sess-tok", "refresh_token": "",
                              "expires_at": time.time() + 600, "windy_identity_id": "id"})
    captured: dict = {}
    _eternitas_401(monkeypatch, captured)
    with pytest.raises(HatchAuthRequired, match="didn't accept your Windy sign-in"):
        await _client().auto_hatch(_request())
    assert captured["auth"] == "Bearer sess-tok"


# ── hatch prompt ─────────────────────────────────────────────────────

def test_interactive_hatch_without_credential_runs_sign_in(monkeypatch):
    from windyfly import hatch_orchestrator as ho

    ran = []
    monkeypatch.setattr(ho, "_hatch_is_interactive", lambda: True)
    monkeypatch.setattr(hub_login, "login",
                        lambda open_browser=True, **_: ran.append(open_browser) or {"windy_identity_id": "5e1b9569"})
    ho._ensure_hatch_sign_in()
    assert ran == [True]


def test_noninteractive_hatch_never_prompts(monkeypatch):
    from windyfly import hatch_orchestrator as ho

    monkeypatch.setenv("WINDY_HATCH_NONINTERACTIVE", "1")
    monkeypatch.setattr(hub_login, "login", lambda **_: pytest.fail("must not prompt"))
    ho._ensure_hatch_sign_in()
    assert ho._hatch_is_interactive() is False


def test_existing_credential_skips_prompt(monkeypatch):
    from windyfly import hatch_orchestrator as ho

    monkeypatch.setenv("ETERNITAS_OPERATOR_JWT", "op")
    monkeypatch.setattr(ho, "_hatch_is_interactive", lambda: True)
    monkeypatch.setattr(hub_login, "login", lambda **_: pytest.fail("must not prompt"))
    ho._ensure_hatch_sign_in()


# ── identity derivation ──────────────────────────────────────────────

def test_identity_prefers_windy_identity_id_over_sub():
    assert identity_from_jwt(make_jwt(HUMAN)) == "5e1b9569-aaaa"
    assert identity_from_jwt(make_jwt({"windyIdentityId": "camel", "sub": "s"})) == "camel"


def test_hub_human_token_never_falls_back_to_sub():
    assert identity_from_jwt(make_jwt({"type": "human", "sub": "2f94efa7-hubuser"})) == ""


def test_legacy_token_without_identity_claim_still_uses_sub():
    assert identity_from_jwt(make_jwt({"sub": "legacy-id"}, alg="ES256")) == "legacy-id"


def test_resolve_identity_falls_back_to_login_session(monkeypatch):
    from windyfly.hatch_orchestrator import _resolve_windy_identity_id

    hub_login._write_session({"access_token": "a", "refresh_token": "",
                              "expires_at": time.time() + 600, "windy_identity_id": "sess-id"})
    assert _resolve_windy_identity_id("") == "sess-id"
    monkeypatch.setenv("WINDY_IDENTITY_ID", "explicit")
    assert _resolve_windy_identity_id("") == "explicit"
