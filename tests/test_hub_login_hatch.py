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


# ── hatch prompt ─────────────────────────────────────────────────────

# ── identity derivation ──────────────────────────────────────────────

def test_identity_prefers_windy_identity_id_over_sub():
    assert identity_from_jwt(make_jwt(HUMAN)) == "5e1b9569-aaaa"
    assert identity_from_jwt(make_jwt({"windyIdentityId": "camel", "sub": "s"})) == "camel"


def test_hub_human_token_never_falls_back_to_sub():
    assert identity_from_jwt(make_jwt({"type": "human", "sub": "2f94efa7-hubuser"})) == ""


def test_legacy_token_without_identity_claim_still_uses_sub():
    assert identity_from_jwt(make_jwt({"sub": "legacy-id"}, alg="ES256")) == "legacy-id"


def test_sign_in_url_is_flushed_before_waiting(monkeypatch):
    """0.7.2 PyPI proof: in a container (stdout a pipe) `windy login` printed
    nothing, because the URL sat in the block buffer while login() waited."""
    import sys

    class PipeStdout:
        """Block-buffered like a pipe: text is only visible after flush()."""
        def __init__(self):
            self.pending, self.visible = "", ""

        def write(self, s):
            self.pending += s
            return len(s)

        def flush(self):
            self.visible += self.pending
            self.pending = ""

    pipe = PipeStdout()
    monkeypatch.setattr(sys, "stdout", pipe)
    seen = {}

    def on_url(url):
        seen["visible"] = url in pipe.visible
        raise KeyboardInterrupt  # stop before waiting for a browser

    with pytest.raises(KeyboardInterrupt):
        hub_login.login(open_browser=False, timeout=0.2, on_url=on_url)
    assert seen == {"visible": True}
