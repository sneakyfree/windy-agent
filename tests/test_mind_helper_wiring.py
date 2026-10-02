"""WINDY_MIND_HELPER=1 path: request-shape fixes kept, the helper owns outage handling."""
import json

import httpx
import pytest

from windyfly.agent import loop, mind_helper, models


def _ok(model="claude-opus-5-5", content="hi", status=200, headers=None):
    return httpx.Response(status, json={
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    }, headers={"x-mind-model": model, "x-mind-switched-off": "none", **(headers or {})})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("WINDY_MIND_HELPER", "1")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
    monkeypatch.setattr(models.time, "sleep", lambda s: None)
    mind_helper._reset_for_tests()
    yield
    mind_helper._reset_for_tests()


def _run(transport, **kw):
    orig = mind_helper._ensure

    def ensure(base_url, transport_=None):
        return orig(base_url, transport)

    mind_helper._ensure = ensure
    try:
        return models._try_mind_broker([{"role": "user", "content": "x"}], kw.get("model", "claude-opus-5-5"),
                                       None, 100, None)
    finally:
        mind_helper._ensure = orig


def test_success_carries_served_model():
    def h(req):
        if req.url.path == "/v1/chat":
            return _ok()
        return httpx.Response(503)

    r = _run(httpx.MockTransport(h))
    assert r["content"] == "hi" and r["mind_model"] == "claude-opus-5-5"
    assert "mind_fallback" not in r


def test_429_paced_once_then_ok():
    calls = []

    def h(req):
        if req.url.path != "/v1/chat":
            return httpx.Response(503)
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "3"}, json={"detail": "slow down"})
        return _ok()

    r = _run(httpx.MockTransport(h))
    assert r and r["content"] == "hi" and len(calls) == 2


def test_422_retries_model_less():
    bodies = []

    def h(req):
        if req.url.path != "/v1/chat":
            return httpx.Response(503)
        b = json.loads(req.content)
        bodies.append(b)
        return httpx.Response(422, json={"detail": "bad model"}) if "model" in b else _ok()

    r = _run(httpx.MockTransport(h))
    assert r and "model" not in bodies[-1] and len(bodies) == 2


def test_403_model_disabled_retries_once_with_minds_choice_and_no_cooldown():
    """Mind 10-02: 403 model_disabled = only that model is off: ONE retry without a
    model (Mind picks), never a loop, never a cooldown, never an owner stop."""
    import json as _json

    calls = []

    def h(req):
        if req.url.path != "/v1/chat":
            return httpx.Response(503)
        calls.append(_json.loads(req.content))
        return httpx.Response(403, headers={"x-mind-error": "model_disabled"}, json={"detail": "off"})

    assert _run(httpx.MockTransport(h)) is None
    assert len(calls) == 2 and "model" in calls[0] and "model" not in calls[1]
    assert not models._is_provider_in_cooldown("windy-mind")
    assert models.owner_paused() is None


def test_outage_falls_through_after_one_helper_retry():
    calls = []

    def h(req):
        if req.url.path != "/v1/chat":
            return httpx.Response(503)
        calls.append(1)
        return httpx.Response(503)

    assert _run(httpx.MockTransport(h)) is None
    assert len(calls) == 2  # the helper's ONE retry, nothing stacked on top
    assert "unreachable" in (models._last_mind_failure or "")


def test_fallback_notice_is_never_empty():
    assert loop._mind_fallback_notice("mind_slow").startswith("\U0001f6df")
    assert loop._mind_fallback_notice("mind_down") != loop._mind_fallback_notice("mind_slow")
    assert loop._mind_fallback_notice("weird").startswith("\U0001f6df")


def test_403_grant_off_via_helper_is_an_owner_stop_not_retried():
    calls = []

    def h(req):
        if req.url.path != "/v1/chat":
            return httpx.Response(503)
        calls.append(1)
        return httpx.Response(403, headers={"x-mind-error": "grant_off", "x-mind-grant-reason": "stopped by owner"},
                              json={"detail": "This helper is paused by its owner."})

    assert _run(httpx.MockTransport(h)) is None
    assert len(calls) == 1
    assert models.owner_paused()["reason"] == "stopped by owner"
    assert models._last_mind_stop == "stopped by owner"
