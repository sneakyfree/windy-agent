"""make_call phones the OWNER only, through windy-text's owner-call route; dark unless WINDY_OWNER_CALL=1."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from windyfly.tools import voice
from windyfly.tools.registry import ToolRegistry


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("WINDY_OWNER_CALL", "1")
    monkeypatch.setenv("WINDY_TEXT_BASE_URL", "https://text.example")
    monkeypatch.setattr("windyfly.tools.sms.agent_auth", lambda url: ({"Authorization": "Bearer t", "DPoP": "p"}, None))


def _resp(status, body):
    r = MagicMock(status_code=status)
    r.json.return_value = body
    return r


def test_dark_by_default(monkeypatch):
    monkeypatch.delenv("WINDY_OWNER_CALL", raising=False)
    reg = ToolRegistry()
    voice.register_voice_tools(reg)
    assert "make_call" not in [s["function"]["name"] if "function" in s else s.get("name") for s in reg.get_schemas()]


def test_registered_with_only_a_message_when_on(on):
    reg = ToolRegistry()
    voice.register_voice_tools(reg)
    schema = next(s for s in reg.get_schemas() if "make_call" in str(s))
    params = schema.get("function", schema).get("parameters") or schema.get("input_schema")
    assert list(params["properties"]) == ["message"] and params["additionalProperties"] is False
    assert "owner" in str(schema) and "prefer" not in str(schema).lower()


@pytest.mark.parametrize("msg", ["", "   ", "x" * 501])
def test_message_length_is_checked_before_any_call(on, msg):
    with patch("httpx.post") as post:
        assert voice.make_call(msg)["status"] == "failed"
    post.assert_not_called()


def test_posts_the_owner_call_and_returns_the_facts(on):
    with patch("httpx.post", return_value=_resp(200, {"call_id": "c1", "status": "queued",
                                                       "time_limit_seconds": 60})) as post:
        out = voice.make_call("Your 3 pm moved to 4 pm.")
    assert out == {"status": "calling", "call_id": "c1", "call_status": "queued", "time_limit_seconds": 60}
    (url,), kw = post.call_args
    assert url == "https://text.example/call/v1/owner-call"
    assert kw["json"] == {"mode": "announce", "message": "Your 3 pm moved to 4 pm."}
    assert kw["headers"]["DPoP"] == "p"


@pytest.mark.parametrize("status,body,code", [
    (503, {"detail": "voice_unavailable"}, "voice_unavailable"),
    (409, {"detail": "no_verified_phone"}, "no_verified_phone"),
    (425, {"detail": "quiet_hours"}, "quiet_hours"),
    (429, {"detail": "spend_cap_reached:daily"}, "spend_cap_reached:daily"),
    (422, {"detail": [{"loc": ["body", "message"], "msg": "too long"}]}, None),
])
def test_refusals_come_back_verbatim_and_are_never_retried(on, status, body, code):
    with patch("httpx.post", return_value=_resp(status, body)) as post:
        out = voice.make_call("hi")
    assert out == {"status": "refused", "http_status": status, "code": code}
    assert post.call_count == 1


@pytest.mark.parametrize("exc", [httpx.ConnectError("x"), httpx.ConnectTimeout("x"), httpx.PoolTimeout("x")])
def test_nothing_reached_windy_text_is_one_failure_not_a_retry(on, exc):
    with patch("httpx.post", side_effect=exc) as post:
        assert voice.make_call("hi") == {"status": "failed", "error": "windy-text unreachable"}
    assert post.call_count == 1


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("x"), httpx.WriteTimeout("x"), httpx.RemoteProtocolError("x")])
def test_no_answer_after_the_request_may_have_arrived_is_unknown_not_failed(on, exc):
    with patch("httpx.post", side_effect=exc) as post:
        out = voice.make_call("hi")
    assert out["status"] == "unknown" and "may still be placed" in out["error"] and post.call_count == 1


def test_the_timeout_covers_the_server_s_clip_rendering(on):
    with patch("httpx.post", return_value=_resp(200, {})) as post:
        voice.make_call("hi")
    assert post.call_args.kwargs["timeout"] >= 90


@pytest.mark.parametrize("code", ["no_key", "passport_suspended", "unreachable"])
def test_without_an_agent_token_and_proof_it_refuses_and_never_sends_the_passport(monkeypatch, code):
    from windyfly.eternitas import agent_keys as ak

    monkeypatch.setenv("WINDY_OWNER_CALL", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "long-passport-token")

    def refuse(aud):
        raise ak.AgentTokenError(code)

    monkeypatch.setattr(ak, "request_agent_token", refuse)
    with patch("httpx.post") as post:
        assert voice.make_call("hi") == {"status": "refused", "code": code}
    post.assert_not_called()


def test_a_missing_key_store_refuses_too(monkeypatch):
    from windyfly.eternitas import agent_keys as ak

    monkeypatch.setenv("WINDY_OWNER_CALL", "1")
    monkeypatch.setattr(ak, "request_agent_token", lambda aud: {"token": "t"})
    monkeypatch.setattr(ak, "service_dpop", lambda m, u: (_ for _ in ()).throw(OSError("no key file")))
    with patch("httpx.post") as post:
        assert voice.make_call("hi") == {"status": "refused", "code": "no_agent_key"}
    post.assert_not_called()


def test_the_dpop_proof_is_for_the_owner_call_url(monkeypatch):
    from windyfly.eternitas import agent_keys as ak

    monkeypatch.setenv("WINDY_OWNER_CALL", "1")
    monkeypatch.setenv("WINDY_TEXT_BASE_URL", "https://text.example")
    seen = []
    monkeypatch.setattr(ak, "request_agent_token", lambda aud: seen.append(aud) or {"token": "t"})
    monkeypatch.setattr(ak, "service_dpop", lambda m, u: seen.append((m, u)) or "p")
    with patch("httpx.post", return_value=_resp(200, {})) as post:
        voice.make_call("hi")
    assert seen == ["windy-telephony", ("POST", "https://text.example/call/v1/owner-call")]
    assert post.call_args.kwargs["headers"] == {"Authorization": "Bearer t", "DPoP": "p"}
