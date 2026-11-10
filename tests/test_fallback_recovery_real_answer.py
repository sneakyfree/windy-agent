"""The fallback ends on a real answer, never on "Mind is up" (Windy Zero, 2026-11-08 to 11-10).

What happened: Grant's own Anthropic key ran out of credit. Windy Mind stayed healthy, so the recovery probe's
GET /v1/models answered 200, the agent logged "paid LLM recovered" and cleared the fallback flag, and the real
chat call in the same second was refused (400). The next turn fell back again. 13 false recoveries in two days.

The rule now: on the fallback, Mind is asked for ONE tiny real answer with the model the agent was using. An
answer ends the fallback. A refusal, or no answer at all, keeps it.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent import models
from windyfly.agent import resurrect as _r


class _Resp:
    def __init__(self, status_code: int, body: dict | None = None, headers: dict | None = None):
        self.status_code = status_code
        self._body = body or {}
        self.headers = headers or {}

    def json(self):
        return self._body


@pytest.fixture
def on_the_fallback(monkeypatch, tmp_path):
    """A keyless agent that fell back from claude-sonnet-5-5, with every marker in a temp folder."""
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST", str(tmp_path / ".recovery_probe_last"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".post_recovery_grace"))
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-abc")
    monkeypatch.setenv("MIND_API_URL", "https://mind.test")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with patch.object(_r, "list_installed_ollama_models",
                      return_value=[{"name": "llama3.2:3b", "size": 2_000_000_000}]):
        out = _r.resurrect(actor="auto-chain-exhausted", previous_model="claude-sonnet-5-5")
    assert out.get("ok"), out
    assert _r.is_resurrected()
    return tmp_path


def _mind_answers(monkeypatch, *responses):
    """Mind's chat endpoint answers with ``responses`` in order; the calls are returned."""
    calls: list[dict] = []
    queue = list(responses)

    def fake_post(url, ept, body, timeout):
        calls.append({"url": url, "body": body})
        r = queue.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(models.mind_auth, "post", fake_post)
    return calls


def test_mind_up_but_the_chat_call_refused_keeps_the_fallback(on_the_fallback, monkeypatch):
    """Zero's exact case: Mind healthy, the own key refused. No recovery, no flag change."""
    calls = _mind_answers(monkeypatch, _Resp(400, {"detail": "anthropic rejected the request: credit balance"}))
    with patch("httpx.get", side_effect=AssertionError("no /v1/models call for Mind")):
        out = _r.attempt_paid_recovery()
    assert out["recovered"] is False and out["reason"] == "still_offline"
    assert out["probe"]["last_provider"] == "windy-mind" and out["probe"]["last_status"] == 400
    assert _r.is_resurrected() is True
    assert len(calls) == 1 and calls[0]["url"] == "https://mind.test/v1/chat"


def test_a_real_answer_ends_the_fallback(on_the_fallback, monkeypatch):
    _mind_answers(monkeypatch, _Resp(200, {"content": "OK"}))
    out = _r.attempt_paid_recovery()
    assert out["recovered"] is True and out["provider"] == "windy-mind"
    assert _r.is_resurrected() is False


def test_mind_unreachable_keeps_the_fallback(on_the_fallback, monkeypatch):
    _mind_answers(monkeypatch, ConnectionError("down"))
    out = _r.attempt_paid_recovery()
    assert out["recovered"] is False
    assert _r.is_resurrected() is True


def test_the_check_asks_for_the_model_the_agent_fell_back_from(on_the_fallback, monkeypatch):
    calls = _mind_answers(monkeypatch, _Resp(200))
    assert _r._paid_health_probe()["ok"] is True
    body = calls[0]["body"]
    assert body["model"] == "claude-sonnet-5-5"
    assert body["max_tokens"] == models._MIND_PING_MAX_TOKENS
    assert len(body["messages"]) == 1


def test_a_model_name_mind_does_not_know_is_retried_without_it(on_the_fallback, monkeypatch):
    """The same rule as a real turn (422 = catalog drift, the broker picks)."""
    calls = _mind_answers(monkeypatch, _Resp(422), _Resp(200))
    assert _r._paid_health_probe() == {"ok": True, "provider": "windy-mind", "status": 200}
    assert "model" in calls[0]["body"] and "model" not in calls[1]["body"]


def test_the_check_never_touches_the_cool_downs(on_the_fallback, monkeypatch):
    monkeypatch.setattr(models, "_provider_cooldowns", {})
    _mind_answers(monkeypatch, _Resp(503))
    assert models.mind_can_answer("claude-sonnet-5-5")["ok"] is False
    assert models._provider_cooldowns == {}


def test_no_passport_means_no_mind_check(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    assert models.mind_can_answer("anything") == {"ok": False, "status": None, "reason": "no_ept"}
