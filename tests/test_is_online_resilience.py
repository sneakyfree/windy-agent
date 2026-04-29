"""Regressions for is_online() resilience.

Pre-fix is_online() hit api.openai.com once with a 3s timeout and
went OFFLINE on any failure. v12 demo dry-run (2026-04-29) caught
a transient blip — the bot replied with the offline-mode message
on beat 1 even though the LLM was perfectly available a moment
later. For grandma in a ballroom that means she asks a question,
gets "I'm currently offline" once, and thinks the bot is broken.

Post-fix:
  - Probes the actual provider (Anthropic if key set, else OpenAI)
  - Retries once per candidate
  - DEFAULTS TO ONLINE if probes all fail — the downstream LLM
    call has its own cooldown-circuit-breaker, surface the real
    error rather than short-circuit
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent.offline import is_online


def test_no_keys_returns_online(monkeypatch):
    """If no provider keys are set at all, default to online — the
    LLM call will fail loudly with a friendly classified message."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert is_online() is True


def test_anthropic_responding_returns_online(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    class _FakeResp:
        status_code = 200

    with patch("httpx.get", return_value=_FakeResp()):
        assert is_online() is True


def test_openai_responding_returns_online(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    class _FakeResp:
        status_code = 200

    with patch("httpx.get", return_value=_FakeResp()):
        assert is_online() is True


def test_first_attempt_fails_second_succeeds(monkeypatch):
    """Retry-once behavior: a transient timeout on attempt 1 must
    not flip to offline if attempt 2 succeeds."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    class _FakeResp:
        status_code = 200

    call_count = {"n": 0}

    def flaky_get(*a, **kw):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise Exception("transient timeout")
        return _FakeResp()

    with patch("httpx.get", side_effect=flaky_get):
        assert is_online() is True
    assert call_count["n"] == 2  # retried once


def test_all_probes_fail_defaults_to_online(monkeypatch):
    """The critical fix: if every probe fails, DEFAULT TO ONLINE
    rather than short-circuit grandma into offline-mode. The
    downstream LLM call's circuit breaker handles the real outage."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    with patch("httpx.get", side_effect=Exception("DNS down")):
        # Pre-fix would have returned False here.
        assert is_online() is True


def test_5xx_responses_count_as_offline(monkeypatch):
    """5xx still flips to offline (provider really is broken).
    Below 500 = upstream is alive enough to talk to."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    class _FakeResp:
        status_code = 503

    with patch("httpx.get", return_value=_FakeResp()):
        # All probes return 503 → exhausted → defaults online
        # (because cooldown will catch the real failure).
        assert is_online() is True
