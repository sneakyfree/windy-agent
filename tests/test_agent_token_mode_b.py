"""Eternitas mode B (agent-keys v1 step 4): request_agent_token + service_dpop,
against a fake /api/v1/tokens/agent that checks the DPoP proof like Eternitas da728b9."""

from __future__ import annotations

import base64
import json
import time

import httpx
import pytest

from windyfly.eternitas import agent_keys as ak

from tests.test_agent_keys import EPT, PASSPORT, FakeEternitas, _err, _jwt, env  # noqa: F401


def _seg(token: str, i: int) -> dict:
    s = token.split(".")[i]
    return json.loads(base64.urlsafe_b64decode(s + "=" * (-len(s) % 4)))


class FakeTokens:
    """POST /api/v1/tokens/agent as built: DPoP-only auth, jkt-bound EPT+agent."""

    def __init__(self, registered_kid: str, *, status: int = 200, jkt: str | None = None) -> None:
        self.kid, self.status, self.jkt = registered_kid, status, jkt
        self.calls: list[httpx.Request] = []
        self.jtis: set[str] = set()

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls.append(req)
        assert req.url.path == ak.TOKEN_PATH
        assert "authorization" not in req.headers  # DPoP only: a leaked bearer can't mint
        proof = req.headers.get("dpop")
        if not proof:
            return _err(401, "dpop_required")
        head, claims = _seg(proof, 0), _seg(proof, 1)
        assert head["typ"] == "dpop+jwt" and head["alg"] == "ES256"
        assert ak.verify_jws(proof, head["jwk"])
        assert claims["htm"] == "POST" and claims["htu"].endswith(ak.TOKEN_PATH)
        assert abs(claims["iat"] - time.time()) < 300 and claims["jti"] not in self.jtis
        self.jtis.add(claims["jti"])
        if ak.thumbprint(head["jwk"]) != self.kid:
            return _err(401, "invalid_dpop_proof")
        if self.status == 429:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": {"code": "rate_limited"}})
        if self.status != 200:
            return _err(self.status, {422: "unknown_audience", 403: "passport_suspended"}[self.status])
        aud = json.loads(req.content)["aud"]
        tok = _jwt({"sub": PASSPORT, "aud": [aud], "exp": int(time.time()) + 300,
                    "cnf": {"jkt": self.jkt or self.kid}, "ei": 612, "band": 3})
        return httpx.Response(200, json={"token": tok, "token_type": "EPT+agent",
                                         "expires_in": 300, "aud": aud, "passport": PASSPORT})


@pytest.fixture
def registered(env):
    ak.clear_token_cache()
    r = ak.ensure_registered(transport=FakeEternitas().transport())
    assert r["status"] == "registered"
    yield r["kid"]
    ak.clear_token_cache()


def test_mode_b_happy_path_is_key_bound_and_cached(registered):
    fake = FakeTokens(registered)
    t = httpx.MockTransport(fake.handle)
    got = ak.request_agent_token("windy-calendar", transport=t)
    assert got["token_type"] == "EPT+agent" and got["aud"] == "windy-calendar"
    assert _seg(got["token"], 1)["cnf"]["jkt"] == registered
    assert got["expires_at"] > time.time() + 250
    again = ak.request_agent_token("windy-calendar", transport=t)
    assert again["token"] == got["token"] and len(fake.calls) == 1  # cached
    ak.request_agent_token("windy-mail", transport=t)
    assert len(fake.calls) == 2  # per-audience


def test_cache_refreshes_near_expiry(registered, monkeypatch):
    fake = FakeTokens(registered)
    t = httpx.MockTransport(fake.handle)
    ak.request_agent_token("windy-chat", transport=t)
    real = time.time
    monkeypatch.setattr(ak.time, "time", lambda: real() + 290)  # inside min_ttl
    ak.request_agent_token("windy-chat", transport=t)
    assert len(fake.calls) == 2


def test_no_registered_key_raises_no_key(env):
    ak.clear_token_cache()
    with pytest.raises(ak.AgentTokenError) as ei:
        ak.request_agent_token("windy-calendar", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert ei.value.code == "no_key"


@pytest.mark.parametrize("status,code", [(422, "unknown_audience"), (403, "passport_suspended")])
def test_refusals_carry_eternitas_codes(registered, status, code):
    t = httpx.MockTransport(FakeTokens(registered, status=status).handle)
    with pytest.raises(ak.AgentTokenError) as ei:
        ak.request_agent_token("windy-nope", transport=t)
    assert ei.value.code == code and ei.value.status == status


def test_rate_limit_exposes_retry_after(registered):
    t = httpx.MockTransport(FakeTokens(registered, status=429).handle)
    with pytest.raises(ak.AgentTokenError) as ei:
        ak.request_agent_token("windy-calendar", transport=t)
    assert ei.value.status == 429 and ei.value.retry_after == 7.0


def test_token_bound_to_another_key_is_rejected(registered):
    t = httpx.MockTransport(FakeTokens(registered, jkt="someone-elses-kid").handle)
    with pytest.raises(ak.AgentTokenError) as ei:
        ak.request_agent_token("windy-calendar", transport=t)
    assert ei.value.code == "jkt_mismatch"


def test_unreachable_is_a_typed_error(registered):
    def boom(req):
        raise httpx.ConnectError("down")
    with pytest.raises(ak.AgentTokenError) as ei:
        ak.request_agent_token("windy-calendar", transport=httpx.MockTransport(boom))
    assert ei.value.code == "unreachable"


def test_service_dpop_is_fresh_per_request_and_strips_query(registered):
    a = ak.service_dpop("post", "https://api.windycalendar.com/v1/events?x=1#f")
    b = ak.service_dpop("POST", "https://api.windycalendar.com/v1/events")
    ca, cb = _seg(a, 1), _seg(b, 1)
    assert ca["htm"] == "POST" and ca["htu"] == "https://api.windycalendar.com/v1/events"
    assert ca["jti"] != cb["jti"]
    assert ak.thumbprint(_seg(a, 0)["jwk"]) == registered
