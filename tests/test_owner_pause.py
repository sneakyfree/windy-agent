"""The owner's stop in Windy Mind (kill switch / Mind OFF): STOP, never fall back.

Mind contract (10-02): 403 + x-mind-error: grant_off, or 403 + x-mind-breaker-owner:
self (without model_disabled) = stopped by the owner. 403 model_disabled = only that
model is off. GET /v1/grants/me (no model call) says on / throttled / off.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import models  # noqa: E402

# conftest stubs check_owner_pause per test; keep the real one to exercise it here.
_REAL = models.check_owner_pause


def _resp(status, headers=None, body=None):
    r = MagicMock()
    r.status_code = status
    r.headers = {k.lower(): v for k, v in (headers or {}).items()}
    r.json.return_value = body or {}
    r.text = ""
    return r


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY", "MIND_API_URL", "ETERNITAS_PASSPORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test")
    models._provider_cooldowns.clear()


@pytest.mark.parametrize("status,headers,stop", [
    (403, {"x-mind-error": "grant_off", "x-mind-grant-reason": "stopped by owner"}, "stopped by owner"),
    (403, {"x-mind-error": "grant_off"}, "paused by its owner"),
    (403, {"x-mind-breaker": "actor:abc", "x-mind-breaker-owner": "self"}, "paused by its owner"),
    (403, {"x-mind-error": "model_disabled", "x-mind-breaker-owner": "self"}, None),
    (403, {"x-mind-error": "model_disabled"}, None),
    (403, {}, None),
    (503, {"x-mind-breaker-owner": "house"}, None),
    (200, {"x-mind-error": "grant_off"}, None),
])
def test_classifier_keys_on_headers(status, headers, stop):
    assert models._mind_stop_reason(status, {k.lower(): v for k, v in headers.items()}) == stop


def test_grant_off_raises_and_never_falls_back(monkeypatch):
    posts = []

    def fake_post(url, **kw):
        posts.append(url)
        return _resp(403, {"x-mind-error": "grant_off", "x-mind-grant-reason": "stopped by owner"},
                     {"detail": "This helper is paused by its owner."})

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr(models, "_build_chain", lambda *a, **k: pytest.fail("direct chain must not run"))
    with pytest.raises(models.AgentPausedByOwner) as exc:
        models.call_llm([{"role": "user", "content": "hi"}], config={"agent": {"default_model": "auto"}})
    assert exc.value.reason == "stopped by owner"
    assert len(posts) == 1  # no retry
    assert models.owner_paused()["reason"] == "stopped by owner"


def test_while_stopped_nothing_is_called(monkeypatch):
    models._latch_pause("stopped by owner")
    monkeypatch.setattr("httpx.post", lambda *a, **k: pytest.fail("no model call while stopped"))
    with pytest.raises(models.AgentPausedByOwner):
        models.call_llm([{"role": "user", "content": "hi"}])


def test_model_disabled_retries_once_with_minds_choice(monkeypatch):
    bodies = []

    def fake_post(url, json=None, **kw):
        bodies.append(dict(json))
        if "model" in json:
            return _resp(403, {"x-mind-error": "model_disabled", "x-mind-breaker": "model:claude-opus-5-5"})
        return _resp(200, {"x-mind-model": "gpt-oss:20b"}, {
            "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}, "model": "gpt-oss:20b"})

    monkeypatch.setattr("httpx.post", fake_post)
    out = models.call_llm([{"role": "user", "content": "hi"}], model="claude-opus-5-5")
    assert out["content"] == "ok" and "model" in bodies[0] and "model" not in bodies[1]
    assert models.owner_paused() is None


def test_real_check_off_latches_on_clears_and_fails_closed(monkeypatch):
    monkeypatch.setattr(models, "check_owner_pause", _REAL)
    answers = iter([_resp(200, body={"state": "off", "state_reason": "stopped by owner"}),
                    _resp(500), _resp(200, body={"state": "on"})])
    monkeypatch.setattr("httpx.get", lambda *a, **k: next(answers))
    assert models.check_owner_pause(force=True)["reason"] == "stopped by owner"
    assert models.check_owner_pause(force=True) is not None          # check failed: stays stopped
    assert models.check_owner_pause(force=True) is None              # owner turned it back on


def test_real_check_fails_open_when_running(monkeypatch):
    monkeypatch.setattr(models, "check_owner_pause", _REAL)
    monkeypatch.setattr("httpx.get", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    assert models.check_owner_pause(force=True) is None


def test_real_check_is_rate_limited(monkeypatch):
    monkeypatch.setattr(models, "check_owner_pause", _REAL)
    calls = []
    monkeypatch.setattr("httpx.get", lambda *a, **k: calls.append(1) or _resp(200, body={"state": "on"}))
    models.check_owner_pause(force=True)
    models.check_owner_pause()
    models.check_owner_pause()
    assert len(calls) == 1


def test_loop_says_one_plain_line_when_stopped(monkeypatch):
    from windyfly.agent import loop

    monkeypatch.setattr(models, "check_owner_pause", lambda force=False: {"reason": "stopped by owner"})
    assert "stopped by my owner" in loop.owner_paused_reply()
    assert "🛟" not in loop.owner_paused_reply()
