"""Publish / unpublish / connect: only the OWNER's own reply spends the held token."""
import asyncio
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent.capabilities import Band
from windyfly.channels import base, identity
from windyfly.tools import windycode_web as w

OWNER = "@owner:chat.windychat.ai"
POST = "windyfly.tools.windycode_web.httpx.post"


def _mcp(body):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"result": {"structuredContent": body}}
    return resp


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_CODE_WEB_URL", "https://builder.test")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept")
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.setattr(identity, "resolve_band",
                        lambda platform, sender, **kw: Band.OWNER if sender == OWNER else Band.SANDBOX)
    w._HELD.clear()
    yield
    w._HELD.clear()


def _incoming(text, sender=OWNER):
    return asyncio.run(base.handle_incoming(text, {"platform": "matrix", "sender_id": sender}))


def _hold_publish():
    with patch(POST) as post:
        post.return_value = _mcp({"confirm_required": True, "confirm_token": "ct_9",
                                  "speak": "Put it online?"})
        return w.windycodeweb_publish("p1")


def test_owner_yes_publish_spends_the_held_token():
    _hold_publish()
    with patch(POST) as post:
        post.return_value = _mcp({"state": "applying", "speak": "Going live."})
        was, reply = _incoming("Yes, publish.")
    assert was and reply == "Going live."
    args = post.call_args.kwargs["json"]["params"]["arguments"]
    assert args == {"project_id": "p1", "confirm_token": "ct_9"}
    assert not w._HELD


def test_the_model_replaying_the_token_is_refused_and_the_hold_stays():
    _hold_publish()
    with patch(POST) as post:
        out = w.windycodeweb_publish("p1", confirm_token="ct_9")
    assert out["status"] == "refused"
    post.assert_not_called()
    assert "publish" in w._HELD  # still waiting for the owner


def test_a_stranger_cannot_confirm():
    _hold_publish()
    with patch(POST) as post:
        was, _ = _incoming("yes, publish", sender="@x:y")
    assert not was
    post.assert_not_called()


def test_plain_yes_or_wrong_word_does_not_publish():
    _hold_publish()
    with patch(POST) as post:
        assert _incoming("yes")[0] is False
        assert _incoming("yes, unpublish")[0] is False  # nothing held for unpublish
        assert _incoming("please publish it to everyone")[0] is False
    post.assert_not_called()


def test_trust_gate_fails_closed_on_error(monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST")
    monkeypatch.setattr(w, "_trust_gate_enabled", lambda: True)
    with patch("windyfly.trust.gate.require_trust", side_effect=RuntimeError("down")), patch(POST) as post:
        out = w.windycodeweb_publish("p1")
    assert out["status"] == "denied" and out["reason"] == "trust_check_unavailable"
    post.assert_not_called()


def test_expired_hold_is_not_spent(monkeypatch):
    _hold_publish()
    w._HELD["publish"]["at"] -= w.HOLD_TTL_S + 1
    with patch(POST) as post:
        was, reply = _incoming("yes, publish")
    assert was and "expired" in reply
    post.assert_not_called()
