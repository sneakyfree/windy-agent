"""Mind's refusals about the CALLER never cool Mind down; Mind's "pick another model" codes retry once
with no model (Windy Mind, Strand v2 5g, 10-12; mind-refusal 1.9.0).

A cool-down benches Mind for every later turn of this agent, so it is only for Mind itself failing
(503 mind_unavailable, other 5xx, timeouts). A 4xx (429 rate limits included) is about this request
or this caller. And Mind never answers 422 for a model it won't serve: it says 404
model_not_available, 403 model_not_offered or 403 model_disabled, each meaning "send auto or another
model". The 422 path stays as a harmless backstop. Mocked Mind only: no network.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import models  # noqa: E402

OK = {"model": "gpt-oss:20b", "choices": [{"message": {"content": "from mind"}}]}


def _resp(status: int, error: str = "", body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {"x-mind-error": error} if error else {}
    r.json.return_value = body or {}
    r.text = ""
    return r


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-test")
    monkeypatch.setattr(models, "_provider_cooldowns", {})
    monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)
    monkeypatch.setattr(models.time, "sleep", lambda s: None)


def _broker(monkeypatch, answers, model=None):
    bodies: list[dict] = []
    it = iter(answers)
    monkeypatch.setattr("httpx.post", lambda url, json=None, **k: bodies.append(dict(json)) or next(it))
    out = models._try_mind_broker([{"role": "user", "content": "x"}], model, None, 100, None)
    return out, bodies


def test_429_does_not_cool_mind_down(monkeypatch):
    out, bodies = _broker(monkeypatch, [_resp(429, "switch_rate"), _resp(429, "switch_rate")])
    assert out is None and len(bodies) == 2                 # Retry-After honoured once, then stop
    assert not models._is_provider_in_cooldown("windy-mind")


@pytest.mark.parametrize("status,error", [(403, "not_allowed_for_agent"), (401, "unknown_kid"),
                                          (403, "model_not_offered"), (418, "")])
def test_4xx_refusal_does_not_cool_mind_down(monkeypatch, status, error):
    out, _ = _broker(monkeypatch, [_resp(status, error)])
    assert out is None and models._last_mind_failure == f"mind http {status}"
    assert not models._is_provider_in_cooldown("windy-mind")


def test_a_real_mind_outage_still_cools_mind_down(monkeypatch):
    out, _ = _broker(monkeypatch, [_resp(503, "mind_unavailable"), _resp(503, "mind_unavailable")])
    assert out is None and models._is_provider_in_cooldown("windy-mind")


@pytest.mark.parametrize("status,error", [(404, "model_not_available"), (403, "model_not_offered"),
                                          (403, "model_disabled")])
def test_pick_another_model_codes_retry_once_with_no_model(monkeypatch, status, error):
    out, bodies = _broker(monkeypatch, [_resp(status, error), _resp(200, body=OK)], model="old-model-x")
    assert out["content"] == "from mind"
    assert [("model" in b) for b in bodies] == [True, False]


def test_the_422_backstop_is_kept(monkeypatch):
    out, bodies = _broker(monkeypatch, [_resp(422), _resp(200, body=OK)], model="old-model-x")
    assert out["content"] == "from mind" and [("model" in b) for b in bodies] == [True, False]


def test_a_real_timeout_still_cools_mind_down(monkeypatch):
    import httpx

    def timeout(*a, **k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("httpx.post", timeout)
    assert models._try_mind_broker([{"role": "user", "content": "x"}], None, None, 100, None) is None
    assert models._is_provider_in_cooldown("windy-mind")


@pytest.mark.parametrize("headers", [{"x-mind-error": "grant_off"}, {"x-mind-breaker-owner": "self"}])
def test_every_owner_stop_still_latches_the_pause_and_does_not_cool_mind(monkeypatch, headers):
    r = _resp(403)
    r.headers = headers
    out, bodies = _broker(monkeypatch, [r])
    try:
        assert out is None and len(bodies) == 1
        assert models.owner_paused() is not None and models._last_mind_stop
        assert not models._is_provider_in_cooldown("windy-mind")
    finally:
        models.clear_owner_pause()
