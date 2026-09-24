"""Building the OAuth client must not make the key vanish for other threads.

Reproduced on Windy 0, 2026-09-13, ten concurrent bridge turns: exactly one
logged ``chain exhausted (attempted=[], skipped=['anthropic(claude-opus-5):
no-key'])``, auto-resurrected, and showed grandma a "hit a rate limit"
banner — while all ten were actually served by claude-opus-5 and the flag
self-cleared 13s later.

Mechanism: ``_call_anthropic`` POPPED ``ANTHROPIC_API_KEY`` out of
``os.environ`` around ``anthropic.Anthropic(auth_token=…)`` so the SDK would
not also emit ``X-Api-Key`` (Anthropic rejects an OAuth token on that
header), restoring it in ``finally``. ``providers.get_provider_for_model``
reads that same env var on every request. A sibling request resolving its
chain inside the window saw "" → ``no-key`` → skip → chain exhausted →
lifeboat. Not concurrency-only in practice: the in-process maintenance jobs
(inbox watch, journal) make LLM calls alongside real turns.

The first test widens the window with a slow fake client and races a
provider lookup against it. The second proves the fix's actual promise at
the wire: with the env var SET, the request carries ``Authorization:
Bearer`` and NO ``X-Api-Key``.
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

OAUTH_TOKEN = "sk-ant-oat01-" + "x" * 80


def _fake_response():
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text="ok", citations=None)],
        usage=SimpleNamespace(input_tokens=10, output_tokens=2),
        stop_reason="end_turn",
    )


class _SlowFakeAnthropic:
    """Stands in for anthropic.Anthropic; construction takes 0.5s so the
    (former) pop window is wide enough to race deterministically."""

    def __init__(self, *args, **kwargs):
        time.sleep(0.5)
        self.messages = SimpleNamespace(create=lambda **kw: _fake_response())


def test_provider_lookup_during_client_construction_still_sees_the_key(monkeypatch):
    from windyfly.agent import models as models_mod
    from windyfly.agent.providers import get_provider_for_model

    monkeypatch.setenv("ANTHROPIC_API_KEY", OAUTH_TOKEN)
    with patch("windyfly.agent.oauth.get_oauth_manager", return_value=None), \
         patch("anthropic.Anthropic", _SlowFakeAnthropic):

        seen: dict[str, str] = {}

        def caller():
            models_mod._call_anthropic(
                [{"role": "user", "content": "hi"}], "claude-opus-5",
                0.0, 64, None, api_key=OAUTH_TOKEN,
            )

        def sibling():
            time.sleep(0.15)  # land squarely inside the 0.5s construction
            seen["api_key"] = get_provider_for_model("claude-opus-5", {}).get("api_key", "")

        a = threading.Thread(target=caller)
        b = threading.Thread(target=sibling)
        a.start(); b.start(); a.join(); b.join()

    assert seen["api_key"] == OAUTH_TOKEN, (
        "a sibling request resolving its provider chain while another request "
        "builds the OAuth client saw NO key — that is the race that fires "
        "'no-key' → chain exhausted → auto-resurrect under concurrency"
    )


def test_oauth_client_sends_bearer_and_never_x_api_key(monkeypatch):
    """The reason the pop existed. Prove the replacement keeps the promise
    at the wire, with the env var present the whole time."""
    from windyfly.agent import models as models_mod

    monkeypatch.setenv("ANTHROPIC_API_KEY", OAUTH_TOKEN)
    captured: dict[str, httpx.Headers] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        return httpx.Response(200, json={
            "id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5",
            "content": [{"type": "text", "text": "ok"}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 2},
        })

    import anthropic as _sdk
    real_anthropic = _sdk.Anthropic

    def anthropic_with_mock_transport(*args, **kwargs):
        kwargs["http_client"] = httpx.Client(transport=httpx.MockTransport(handler))
        return real_anthropic(*args, **kwargs)

    with patch("windyfly.agent.oauth.get_oauth_manager", return_value=None), \
         patch("anthropic.Anthropic", anthropic_with_mock_transport):
        models_mod._call_anthropic(
            [{"role": "user", "content": "hi"}], "claude-opus-5",
            0.0, 64, None, api_key=OAUTH_TOKEN,
        )

    h = captured["headers"]
    assert h.get("authorization") == f"Bearer {OAUTH_TOKEN}"
    assert "x-api-key" not in h, "OAuth token must never ride on X-Api-Key (Anthropic 401s it)"
    assert "oauth-2025-04-20" in h.get("anthropic-beta", "")
    # And the env var is untouched afterwards — no pop/restore dance.
    assert os.environ.get("ANTHROPIC_API_KEY") == OAUTH_TOKEN


def test_banner_names_the_real_reason_not_a_rate_limit():
    """The auto-resurrect notice said 'hit a rate limit' whatever happened.
    It must describe what actually happened."""
    from windyfly.agent import loop as loop_mod
    fn = getattr(loop_mod, "_auto_resurrect_banner", None)
    if fn is None:
        pytest.skip("banner helper not present yet")
    txt = fn("llama3.2:3b", "LLM call failed across all providers in chain (attempted=[], skipped=['anthropic(claude-opus-5):no-key'])")
    assert "rate limit" not in txt.lower()
    assert "llama3.2:3b" in txt


def test_banner_names_busy_mind_not_a_missing_credential():
    """A Mind-routed agent has no direct key by design, so after Mind 503s
    the direct chain says no-key. The banner must blame Mind being busy."""
    from windyfly.agent import loop as loop_mod
    err = ("LLM call failed across all providers in chain (attempted=[], "
           "skipped=['openai(windy-mind-auto):no-key'], mind http 503): None")
    txt = loop_mod._auto_resurrect_banner("llama3.2:3b", err).lower()
    assert "busy" in txt
    assert "credential" not in txt
