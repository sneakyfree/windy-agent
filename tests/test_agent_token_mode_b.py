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
    ak.clear_mint_wait()
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


# ── resilience (Zero 2026-11-01: ~126 mints died at the network and fell back to the legacy EPT) ──────────────


class Flaky:
    """Fails the first ``fails`` mints with ``exc`` (or answers ``status``), then behaves like FakeTokens."""

    def __init__(self, kid: str, fails: int, exc: type | None = httpx.ConnectError, status: int = 0) -> None:
        self.ok, self.fails, self.exc, self.status, self.attempts = FakeTokens(kid), fails, exc, status, 0

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.attempts += 1
        if self.attempts <= self.fails:
            if self.exc is not None:
                raise self.exc("down")
            return _err(self.status, {503: "unavailable", 403: "passport_suspended"}.get(self.status, "x")) \
                if self.status != 429 else httpx.Response(429, headers={"Retry-After": "7"}, json={"detail": {}})
        return self.ok.handle(req)


@pytest.fixture
def fast(monkeypatch):
    monkeypatch.setattr(ak, "MINT_RETRY_S", 0)


def test_a_network_blip_with_nothing_cached_is_retried_once(registered, fast):
    f = Flaky(registered, fails=1)
    got = ak.request_agent_token("windy-mind", transport=httpx.MockTransport(f.handle))
    assert got["token_type"] == "EPT+agent" and f.attempts == 2


def test_two_network_failures_raise_unreachable_with_the_error_class(registered, fast):
    f = Flaky(registered, fails=2, exc=httpx.ConnectTimeout)
    with pytest.raises(ak.AgentTokenError) as ei:
        ak.request_agent_token("windy-mind", transport=httpx.MockTransport(f.handle))
    assert ei.value.code == "unreachable" and ei.value.message == "ConnectTimeout" and f.attempts == 2


def _cached_then(registered, monkeypatch, seconds_later: int, flaky: "Flaky") -> tuple[dict, dict | None, Exception | None]:
    good = ak.request_agent_token("windy-mind", transport=httpx.MockTransport(FakeTokens(registered).handle))
    real = time.time
    monkeypatch.setattr(ak.time, "time", lambda: real() + seconds_later)
    try:
        return good, ak.request_agent_token("windy-mind", transport=httpx.MockTransport(flaky.handle)), None
    except ak.AgentTokenError as e:
        return good, None, e


def test_refresh_starts_early_and_a_blip_keeps_the_valid_cached_token(registered, monkeypatch, fast):
    f = Flaky(registered, fails=5)
    good, got, err = _cached_then(registered, monkeypatch, 240, f)  # 60 s left: inside REFRESH_EARLY_S
    assert err is None and got["token"] == good["token"]
    assert f.attempts == 1  # tried to refresh early; no retry needed while a valid token is cached


@pytest.mark.parametrize("status", [503, 429])
def test_a_5xx_or_429_also_keeps_the_valid_cached_token(registered, monkeypatch, fast, status):
    good, got, err = _cached_then(registered, monkeypatch, 240, Flaky(registered, fails=5, exc=None, status=status))
    assert err is None and got["token"] == good["token"]


def test_a_refusal_never_reuses_the_cached_token_and_drops_it(registered, monkeypatch, fast):
    good, got, err = _cached_then(registered, monkeypatch, 240, Flaky(registered, fails=5, exc=None, status=403))
    assert got is None and err.code == "passport_suspended"
    assert not [k for k in ak._TOKEN_CACHE if k[1] == "windy-mind"]  # a suspended passport's token is gone


def test_a_cached_token_about_to_expire_is_not_reused(registered, monkeypatch, fast):
    f = Flaky(registered, fails=5)
    good, got, err = _cached_then(registered, monkeypatch, 297, f)  # 3 s left: under STALE_GRACE_S
    assert got is None and err.code == "unreachable" and f.attempts == 2  # no usable token: retried, then raised


def test_service_auth_keeps_the_error_class(registered, fast, monkeypatch):
    from windyfly.agent import service_auth

    f = Flaky(registered, fails=5, exc=httpx.ReadTimeout)
    real = ak.request_agent_token
    monkeypatch.setattr(ak, "request_agent_token",
                        lambda aud, **k: real(aud, transport=httpx.MockTransport(f.handle)))
    with pytest.raises(service_auth.ServiceAuthError) as ei:
        service_auth.agent_headers("windy-mind", "POST", "https://api.windymind.ai/v1/chat")
    assert ei.value.code == "unreachable" and ei.value.detail == "ReadTimeout"


def test_every_legacy_mind_request_is_counted_on_the_health_row():
    from windyfly.agent import mind_auth
    from windyfly.observability import agent_health as ah

    ah._reset_for_tests()
    mind_auth.legacy_headers("legacy-ept")
    mind_auth.legacy_headers("legacy-ept")
    assert ah._iv.mind_legacy_auth == 2
    ah._reset_for_tests()
    assert ah._iv.mind_legacy_auth == 0


# ── after a 429 the agent waits; it does not ask again (Zero 2026-11-08 to 11-10: 11,640 x 429) ──

class Limited:
    """429 for the first ``refusals`` calls (with or without Retry-After), then real tokens."""

    def __init__(self, kid: str, refusals: int, retry_after: str | None = "60") -> None:
        self.ok, self.refusals, self.retry_after, self.calls = FakeTokens(kid), refusals, retry_after, 0

    def handle(self, req: httpx.Request) -> httpx.Response:
        self.calls += 1
        if self.calls <= self.refusals:
            headers = {"Retry-After": self.retry_after} if self.retry_after else {}
            return httpx.Response(429, headers=headers, json={"detail": {"code": "rate_limited"}})
        return self.ok.handle(req)


def _clock(monkeypatch, start: float = 1_800_000_000.0):
    t = {"now": start}
    monkeypatch.setattr(ak.time, "time", lambda: t["now"])
    return t


def test_after_a_429_the_next_calls_do_not_reach_eternitas(registered, monkeypatch):
    clock = _clock(monkeypatch)
    f = Limited(registered, refusals=99)
    t = httpx.MockTransport(f.handle)
    with pytest.raises(ak.AgentTokenError) as first:
        ak.request_agent_token("windy-mind", transport=t)
    assert first.value.status == 429 and f.calls == 1
    for aud in ("windy-mind", "windy-text", "windy-calendar"):  # the limit is per passport: every audience waits
        clock["now"] += 5
        with pytest.raises(ak.AgentTokenError) as again:
            ak.request_agent_token(aud, transport=t)
        assert again.value.status == 429 and again.value.code == "rate_limited"
        assert 0 < again.value.retry_after <= 121
    assert f.calls == 1  # 3 more asks, 0 more network calls: Eternitas's window can close


def test_when_the_wait_is_over_the_agent_asks_again_and_a_token_clears_it(registered, monkeypatch):
    clock = _clock(monkeypatch)
    f = Limited(registered, refusals=1)
    t = httpx.MockTransport(f.handle)
    with pytest.raises(ak.AgentTokenError):
        ak.request_agent_token("windy-mind", transport=t)
    clock["now"] += 120.5  # twice Retry-After 60 plus the 1 s edge: still waiting
    with pytest.raises(ak.AgentTokenError):
        ak.request_agent_token("windy-mind", transport=t)
    assert f.calls == 1
    clock["now"] += 1
    assert ak.request_agent_token("windy-mind", transport=t)["token"]
    assert f.calls == 2 and not ak._MINT_WAIT


def test_a_429_without_retry_after_waits_twice_the_60_second_window(registered, monkeypatch):
    clock = _clock(monkeypatch)
    f = Limited(registered, refusals=1, retry_after=None)
    t = httpx.MockTransport(f.handle)
    with pytest.raises(ak.AgentTokenError):
        ak.request_agent_token("windy-mind", transport=t)
    clock["now"] += 119
    with pytest.raises(ak.AgentTokenError):
        ak.request_agent_token("windy-mind", transport=t)
    clock["now"] += 3
    assert ak.request_agent_token("windy-mind", transport=t)["token"] and f.calls == 2


def _asks_until_free(wait: float, limit: int = 20) -> int | None:
    """Two processes of one agent against Eternitas as it is today: a refused ask re-arms the 60 s window.
    A is refused at 0, B at 30; each asks again the moment its own wait ends. Returns how many more asks it
    takes until one gets through, or None if none does within ``limit``."""
    window_until = 30.0 + 60.0  # B's refused ask at 30 re-armed A's window
    next_ask = {"A": wait, "B": 30.0 + wait}
    for n in range(1, limit + 1):
        who = min(next_ask, key=lambda k: next_ask[k])
        now = next_ask[who]
        if now >= window_until:
            return n
        window_until = now + 60.0
        next_ask[who] = now + wait
    return None


def test_two_processes_of_one_agent_cannot_keep_each_other_locked():
    # Retry-After + 1 s alone never frees the passport: each process's ask lands inside the other's window.
    assert _asks_until_free(ak.RATE_LIMIT_WAIT_S + 1.0) is None
    # The shipped wait (twice Retry-After, + 1 s) gets through on the very next ask.
    assert _asks_until_free(ak.RATE_LIMIT_WAIT_S * ak.RATE_LIMIT_WAIT_FACTOR + 1.0) == 1


def test_a_huge_retry_after_is_capped(registered, monkeypatch):
    clock = _clock(monkeypatch)
    f = Limited(registered, refusals=1, retry_after="86400")
    t = httpx.MockTransport(f.handle)
    with pytest.raises(ak.AgentTokenError):
        ak.request_agent_token("windy-mind", transport=t)
    clock["now"] += ak.RATE_LIMIT_WAIT_MAX_S + 2
    assert ak.request_agent_token("windy-mind", transport=t)["token"]


def test_a_valid_cached_token_keeps_working_during_the_wait_with_no_network_call(registered, monkeypatch):
    clock = _clock(monkeypatch)
    good = ak.request_agent_token("windy-mind", transport=httpx.MockTransport(FakeTokens(registered).handle))
    f = Limited(registered, refusals=99)
    t = httpx.MockTransport(f.handle)
    clock["now"] += 240  # past the early-refresh point, the token still valid for 60 s
    assert ak.request_agent_token("windy-mind", transport=t)["token"] == good["token"] and f.calls == 1
    clock["now"] += 10
    assert ak.request_agent_token("windy-mind", transport=t)["token"] == good["token"]
    assert f.calls == 1  # the second ask stayed home
