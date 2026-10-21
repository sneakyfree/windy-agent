"""Birth reservation v1 (eternitas #265): mid-canal, Eternitas answers credential routes with 409
passport_not_born + Retry-After: 30. That is a quiet WAIT: never revoked wording, never the revoked hint,
never a warning, and a trust check never treats being_born / not_born as usable."""
from __future__ import annotations

import logging

import httpx
import pytest

from tests.test_agent_token_mode_b import registered  # noqa: F401  (fixture)
from tests.test_agent_keys import env  # noqa: F401  (fixture)
from tests.test_ept_refresh import PASSPORT as EPT_PASSPORT, _isolate, make_ept, DAY  # noqa: F401
from windyfly.eternitas import agent_keys as ak

NOT_BORN = {"detail": {"code": "passport_not_born", "message": "this passport is still being born"}}


def _not_born(_req: httpx.Request) -> httpx.Response:
    return httpx.Response(409, headers={"Retry-After": "30"}, json=NOT_BORN)


def test_token_mint_raises_not_born_with_retry_after(registered):  # noqa: F811
    with pytest.raises(ak.AgentTokenError) as e:
        ak.request_agent_token("windy-mind", transport=httpx.MockTransport(_not_born))
    assert e.value.code == ak.NOT_BORN and ak.is_not_born(e.value.code)
    assert e.value.status == 409 and e.value.retry_after == 30.0


def test_retry_after_is_kept_on_429_too(registered):  # noqa: F811
    t = httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "7"},
                                                     json={"detail": {"code": "rate_limited"}}))
    with pytest.raises(ak.AgentTokenError) as e:
        ak.request_agent_token("windy-mind", transport=t)
    assert e.value.retry_after == 7.0 and not ak.is_not_born(e.value.code)


def test_mind_auth_waits_quietly_on_not_born(monkeypatch, caplog):
    from windyfly.agent import mind_auth, service_auth

    monkeypatch.setenv(mind_auth.ENV_FLAG, "1")
    monkeypatch.setattr(mind_auth, "_warned", set())

    def refuse(aud, method, url):
        raise service_auth.ServiceAuthError("passport_not_born")

    monkeypatch.setattr(service_auth, "agent_headers", refuse)
    with caplog.at_level(logging.INFO, logger="windyfly.agent.mind_auth"):
        h, agent = mind_auth.headers("GET", "https://api.windymind.ai/v1/agents/me", "legacy")
    assert agent is False
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("being born" in r.getMessage() for r in caplog.records)


def test_ept_refresh_not_born_is_a_quiet_wait_not_the_revoked_hint(monkeypatch, caplog):
    from windyfly.eternitas import ept_refresh as er

    monkeypatch.setenv(er.ENV_KEY, make_ept(exp_in=10 * DAY))
    with caplog.at_level(logging.INFO, logger="windyfly.eternitas.ept_refresh"):
        r = er.refresh_ept(force=True, transport=httpx.MockTransport(_not_born))
    assert r == {"status": "not_born_yet", "retry_after": 30.0}
    assert "hint" not in r
    assert not [x for x in caplog.records if x.levelno >= logging.WARNING]


def test_texting_says_not_set_up_yet_not_standing(monkeypatch):
    from windyfly.tools import sms

    monkeypatch.setenv("WINDY_TEXT_BYO", "1")

    def refuse(aud):
        raise ak.AgentTokenError("passport_not_born", status=409, retry_after=30.0)

    monkeypatch.setattr(ak, "request_agent_token", refuse)
    calls = []
    monkeypatch.setattr(sms.httpx, "get", lambda *a, **k: calls.append(1))
    out = sms.send_sms(body="hi")
    assert out["sent"] is False and "not fully set up yet" in out["error"]
    assert "standing" not in out["error"] and calls == []


@pytest.mark.parametrize("status", ["being_born", "not_born", "pending", "something_new"])
def test_trust_allows_only_an_active_passport(status):
    from windyfly.trust.check import TrustSnapshot

    snap = TrustSnapshot(passport="ET26-X", status=status, band="good", allowed_actions=["*"])
    assert snap.allows("send_email") is False
    assert TrustSnapshot(passport="ET26-X", status="active", band="good", allowed_actions=["*"]).allows("send_email")
