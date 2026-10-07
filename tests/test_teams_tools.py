"""Agent teams v1 (dark WINDY_TEAMS=1): list_my_agents + message_agent against Chat's routes (team-tools.v1)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import teams
from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.agent.capabilities.teams import register_teams_capabilities
from windyfly.channels import identity

ME = "@agent_et26-me00-0001:chat.windychat.ai"
SIB = "@agent_et26-sib0-0002:chat.windychat.ai"
ROW_ME = {"name": "Zero", "passport": "ET26-ME00-0001", "matrix_id": ME, "self": True}
ROW_SIB = {"name": "Scout", "passport": "ET26-SIB0-0002", "matrix_id": SIB, "about": "finds things"}


def _resp(status=200, body=None):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = body if body is not None else {}
    return r


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "1")
    monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.example")
    monkeypatch.setenv("MATRIX_BOT_TOKEN", "tok")
    monkeypatch.delenv("WINDY_CHAT_API_URL", raising=False)
    teams._reset_for_tests()
    sent = []
    monkeypatch.setattr("windyfly.eternitas.agent_keys.request_agent_token", lambda aud: {"token": f"t-{aud}"})
    monkeypatch.setattr("windyfly.eternitas.agent_keys.service_dpop", lambda m, u: f"dpop:{m}:{u}")
    monkeypatch.setattr("windyfly.tools.chat.send_chat_message",
                        lambda body, to_room=None: sent.append((body, to_room)) or {"status": "sent", "room": to_room})
    return sent


def _route(monkeypatch, *responses):
    calls, seq = [], list(responses)

    def fake(method, url, **kw):
        calls.append((method, url, kw))
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr("httpx.request", fake)
    return calls


def test_list_signs_a_get_with_dpop_and_remembers_siblings(monkeypatch):
    calls = _route(monkeypatch, _resp(200, {"ok": True, "agents": [ROW_ME, ROW_SIB]}))
    out = teams.list_my_agents()
    assert out == {"ok": True, "agents": [ROW_ME, ROW_SIB]}
    m, url, kw = calls[0]
    assert (m, url) == ("GET", "https://chat.windychat.ai/api/v1/onboarding/agent/my-agents")
    assert kw["headers"] == {"Authorization": "Bearer t-windy-chat", "DPoP": f"dpop:GET:{url}"}
    assert teams.sibling_ids() == frozenset({SIB})  # never myself


def test_list_when_chat_has_no_owner_link_is_an_empty_list(monkeypatch):
    _route(monkeypatch, _resp(404, {"ok": False, "error": "unknown_agent"}))
    assert teams.list_my_agents() == {"ok": True, "agents": []}


def test_list_unreachable_is_unavailable_not_an_exception(monkeypatch):
    import httpx

    monkeypatch.setattr("httpx.request", lambda *a, **k: (_ for _ in ()).throw(httpx.ConnectError("x")))
    out = teams.list_my_agents()
    assert out["ok"] is False and out["error"] == "unavailable" and "Windy Chat" in out["detail"]


def test_message_pairs_room_joins_then_posts_as_the_agent(monkeypatch, _env):
    calls = _route(monkeypatch, _resp(200, {"ok": True, "room_id": "!pair:chat.example", "created": True}))
    joined = []
    monkeypatch.setattr("httpx.post", lambda url, **kw: joined.append((url, kw)) or _resp(200, {}))
    out = teams.message_agent("Scout", "please check the logs")
    assert out == {"ok": True, "to": "Scout"}
    assert calls[0][0] == "POST" and calls[0][2]["json"] == {"to": "Scout"}
    assert calls[0][2]["headers"]["DPoP"].startswith("dpop:POST:https://chat.windychat.ai/")
    assert joined[0][0] == "https://chat.example/_matrix/client/v3/rooms/!pair:chat.example/join"
    assert _env == [("please check the logs", "!pair:chat.example")]  # join happened BEFORE the post


@pytest.mark.parametrize("status,err", [(404, "unknown_agent"), (409, "ambiguous"), (403, "not_your_agent"),
                                        (400, "self")])
def test_chat_refusals_pass_through_in_the_contract_shape(monkeypatch, _env, status, err):
    _route(monkeypatch, _resp(status, {"ok": False, "error": err, "detail": "d"}))
    assert teams.message_agent("x", "hi") == {"ok": False, "error": err, "detail": "d"}
    assert _env == []


def test_chat_down_is_unavailable_and_nothing_is_posted(monkeypatch, _env):
    _route(monkeypatch, _resp(503, {"ok": False, "error": "unavailable"}))
    out = teams.message_agent("Scout", "hi")
    assert out["ok"] is False and out["error"] == "unavailable" and _env == []


def test_join_failure_is_unavailable_and_nothing_is_posted(monkeypatch, _env):
    _route(monkeypatch, _resp(200, {"ok": True, "room_id": "!r:x", "created": False}))
    monkeypatch.setattr("httpx.post", lambda url, **kw: _resp(403, {}))
    out = teams.message_agent("Scout", "hi")
    assert out["error"] == "unavailable" and "join" in out["detail"] and _env == []


def test_empty_and_too_long_text_never_reach_chat(monkeypatch):
    calls = _route(monkeypatch, _resp(200, {}))
    assert teams.message_agent("Scout", "  ")["ok"] is False
    assert teams.message_agent("Scout", "x" * 4001)["ok"] is False
    assert calls == []


def test_siblings_are_trusted_strangers_are_sandbox(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_OWNER_IDS", "matrix:@owner:chat.example")
    monkeypatch.setenv("WINDY_OWNER_BINDINGS_PATH", str(tmp_path / "o.json"))
    _route(monkeypatch, _resp(200, {"ok": True, "agents": [ROW_ME, ROW_SIB]}))
    teams.list_my_agents()
    assert identity.resolve_band("matrix", SIB) == Band.TRUSTED
    assert identity.resolve_band("matrix", "@agent_et26-evil-0003:chat.windychat.ai") == Band.SANDBOX
    monkeypatch.setenv("WINDY_TEAMS", "0")
    assert identity.resolve_band("matrix", SIB) == Band.SANDBOX  # flag off: unchanged


def test_tools_are_trusted_band_and_dark_without_the_flag(monkeypatch):
    r = CapabilityRegistry()
    register_teams_capabilities(r)
    assert r.get("list_my_agents").band_required == Band.TRUSTED
    assert r.get("message_agent").band_required == Band.TRUSTED and r.get("message_agent").audit_required
    assert [c.id for c in r.list_for_band(Band.SANDBOX) if c.id in ("list_my_agents", "message_agent")] == []
    monkeypatch.setenv("WINDY_TEAMS", "0")
    r2 = CapabilityRegistry()
    register_teams_capabilities(r2)
    assert r2.get("list_my_agents") is None


def test_descriptions_are_the_contract_words():
    r = CapabilityRegistry()
    register_teams_capabilities(r)
    assert r.get("list_my_agents").description == "List the agents that belong to your owner (including you)."
    assert r.get("message_agent").description == (
        "Send a message to another of your owner's agents. They answer in an ordinary message.")
