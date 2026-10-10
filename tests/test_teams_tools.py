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


def _members(monkeypatch, members):
    chunk = [{"state_key": u, "content": {"membership": m}} for u, m in members]
    monkeypatch.setattr("httpx.get", lambda url, **kw: _resp(200, {"chunk": chunk}))


def test_a_room_with_another_agent_is_a_team_room(monkeypatch):
    _members(monkeypatch, [(ME, "join"), (SIB, "invite"), ("@owner:chat.example", "join")])
    assert teams.room_has_other_agent("!r:x", ME) is True


def test_a_room_with_only_humans_and_me_is_not(monkeypatch):
    _members(monkeypatch, [(ME, "join"), ("@owner:chat.example", "join")])
    assert teams.room_has_other_agent("!r:x", ME) is False
    _members(monkeypatch, [(ME, "join"), (SIB, "leave")])
    assert teams.room_has_other_agent("!r:x", ME) is False  # left


def test_unknown_membership_keeps_the_old_welcome(monkeypatch):
    monkeypatch.setattr("httpx.get", lambda url, **kw: _resp(500, {}))
    assert teams.room_has_other_agent("!r:x", ME) is False
    import httpx

    monkeypatch.setattr("httpx.get", lambda url, **kw: (_ for _ in ()).throw(httpx.ConnectError("x")))
    assert teams.room_has_other_agent("!r:x", ME) is False


# ── session-free pair rooms (team-tools.v1 1.3.0): a claim, createRoom as myself, confirm ───────────

CLAIM = {"create": True, "claim": "c-1", "expires_in": 60, "name": "Zero + Scout",
         "invite": [SIB, "@owner:chat.example"], "owner_mxid": "@owner:chat.example"}


def _matrix_post(monkeypatch, *responses):
    calls, seq = [], list(responses)

    def fake(url, **kw):
        calls.append((url, kw))
        return seq.pop(0) if len(seq) > 1 else seq[0]

    monkeypatch.setattr("httpx.post", fake)
    return calls


def test_claim_creates_the_room_as_me_confirms_then_posts(monkeypatch, _env):
    chat = _route(monkeypatch, _resp(200, CLAIM), _resp(200, {"room_id": "!new:x", "created": True, "partner_joined": True}))
    mx = _matrix_post(monkeypatch, _resp(200, {"room_id": "!new:x"}))
    out = teams.message_agent("Scout", "hello")
    assert out == {"ok": True, "to": "Scout"}
    url, kw = mx[0]
    assert url == "https://chat.example/_matrix/client/v3/createRoom"
    assert kw["headers"] == {"Authorization": "Bearer tok"}  # MY token, nobody's session
    assert kw["json"] == {"name": "Zero + Scout", "preset": "private_chat", "invite": [SIB, "@owner:chat.example"],
                          "creation_content": {"ai.windy.pair_claim": "c-1"},
                          "power_level_content_override": {"users": {"@owner:chat.example": 100}}}
    assert "initial_state" not in kw["json"]  # unencrypted: no m.room.encryption
    assert chat[1][1].endswith("/pair-room/confirm") and chat[1][2]["json"] == {"claim": "c-1", "room_id": "!new:x"}
    assert _env == [("hello", "!new:x")]


def test_partner_not_joined_is_said_plainly(monkeypatch, _env):
    _route(monkeypatch, _resp(200, CLAIM), _resp(200, {"room_id": "!new:x", "created": True, "partner_joined": False}))
    _matrix_post(monkeypatch, _resp(200, {"room_id": "!new:x"}))
    out = teams.message_agent("Scout", "hello")
    assert out == {"ok": True, "to": "Scout", "note": "Scout has not joined the room yet; your message is there for them"}


def test_existing_room_with_partner_not_joined_carries_the_note(monkeypatch, _env):
    _route(monkeypatch, _resp(200, {"ok": True, "room_id": "!r:x", "created": False, "partner_joined": False}))
    _matrix_post(monkeypatch, _resp(200, {}))
    assert teams.message_agent("Scout", "hi")["note"].startswith("Scout has not joined")


def test_confirm_5xx_then_retry_confirms_the_same_room_never_a_second(monkeypatch, _env):
    _route(monkeypatch, _resp(200, CLAIM), _resp(503, {}))
    mx = _matrix_post(monkeypatch, _resp(200, {"room_id": "!new:x"}))
    first = teams.message_agent("Scout", "hello")
    assert first["ok"] is False and first["error"] == "unavailable" and _env == []
    _route(monkeypatch, _resp(200, CLAIM), _resp(200, {"room_id": "!new:x", "created": True, "partner_joined": True}))
    assert teams.message_agent("Scout", "hello")["ok"] is True
    assert len(mx) == 1  # createRoom ran once


def test_confirm_refusal_drops_the_room_and_says_why(monkeypatch, _env):
    _route(monkeypatch, _resp(200, CLAIM), _resp(403, {"error": "room_check_failed", "reason": "encrypted"}))
    _matrix_post(monkeypatch, _resp(200, {"room_id": "!new:x"}))
    out = teams.message_agent("Scout", "hello")
    assert out["error"] == "unavailable" and "encrypted" in out["detail"] and _env == []
    assert teams._created_for_claim == {}


@pytest.mark.parametrize("err,words", [("not_siblings_yet", "isn't registered"), ("agent_revoked", "no longer active"),
                                       ("claim_in_use", "being set up")])
def test_claim_refusals_are_plain_facts(monkeypatch, _env, err, words):
    _route(monkeypatch, _resp(403, {"error": err, "retry": False}))
    out = teams.message_agent("Scout", "hi")
    assert out["ok"] is False and words in out["detail"] and _env == []


def test_create_room_rate_limited_is_said_plainly(monkeypatch, _env):
    _route(monkeypatch, _resp(200, CLAIM))
    _matrix_post(monkeypatch, _resp(429, {"errcode": "M_LIMIT_EXCEEDED", "retry_after_ms": 4200}))
    out = teams.message_agent("Scout", "hi")
    assert out["error"] == "unavailable" and "rate-limiting" in out["detail"] and "4 s" in out["detail"]
    assert teams._created_for_claim == {} and _env == []


def test_claim_without_matrix_config_cannot_pretend(monkeypatch, _env):
    monkeypatch.delenv("MATRIX_BOT_TOKEN")
    _route(monkeypatch, _resp(200, CLAIM))
    out = teams.message_agent("Scout", "hi")
    assert out["ok"] is False and "not configured" in out["detail"] and _env == []
