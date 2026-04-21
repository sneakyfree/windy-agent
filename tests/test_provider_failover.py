"""Tests for the provider failover chain in agent.models.call_llm.

Verifies that when a provider fails, the chain walks forward, the dead
provider goes into cooldown, unconfigured providers are skipped silently,
and an explicit ``model=`` argument bypasses the chain entirely.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Force the module to use a non-existent overrides file so the dashboard's
# data/providers.json doesn't clobber test mocks with real keys.
os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import models, providers  # noqa: E402


def _reset_provider_state() -> None:
    """Wipe per-provider api_key/configured caches between tests.

    BUILTIN_PROVIDERS is module-level and shallow-copied by
    get_all_providers, so api_key bleeds across tests if not cleared.
    """
    models._provider_cooldowns.clear()
    for prov in providers.BUILTIN_PROVIDERS.values():
        prov.pop("api_key", None)
        prov.pop("configured", None)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _reset_provider_state()
    # Ensure the test controls every relevant env var
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GROK_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    yield
    _reset_provider_state()


def test_failover_walks_to_next_provider_on_failure(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")

    config = {"agent": {"failover_chain": ["gpt-4o-mini", "claude-haiku-4-5-20251001"]}}
    calls: list[tuple[str, str]] = []

    def fake_openai(messages, model, *args, **kwargs):
        calls.append(("openai", model))
        raise RuntimeError("503")

    def fake_anthropic(messages, model, *args, **kwargs):
        calls.append(("anthropic", model))
        return {
            "content": "ok",
            "model": model,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": None,
        }

    with patch.object(models, "_call_openai", side_effect=fake_openai), \
         patch.object(models, "_call_anthropic", side_effect=fake_anthropic):
        result = models.call_llm([{"role": "user", "content": "hi"}], config=config)

    assert result["content"] == "ok"
    assert calls == [("openai", "gpt-4o-mini"), ("anthropic", "claude-haiku-4-5-20251001")]
    assert "openai" in models._provider_cooldowns
    assert "anthropic" not in models._provider_cooldowns


def test_cooled_down_provider_is_skipped_on_subsequent_call(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")

    config = {"agent": {"failover_chain": ["gpt-4o-mini", "claude-haiku-4-5-20251001"]}}
    calls: list[tuple[str, str]] = []

    def fake_openai(messages, model, *args, **kwargs):
        calls.append(("openai", model))
        raise RuntimeError("503")

    def fake_anthropic(messages, model, *args, **kwargs):
        calls.append(("anthropic", model))
        return {
            "content": "ok",
            "model": model,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": None,
        }

    with patch.object(models, "_call_openai", side_effect=fake_openai), \
         patch.object(models, "_call_anthropic", side_effect=fake_anthropic):
        models.call_llm([{"role": "user", "content": "hi"}], config=config)
        calls.clear()
        models.call_llm([{"role": "user", "content": "hi"}], config=config)

    # Second call skips openai (cooldown) and goes straight to anthropic
    assert calls == [("anthropic", "claude-haiku-4-5-20251001")]


def test_unconfigured_provider_is_skipped_silently(monkeypatch):
    # Only anthropic key is set
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")

    config = {"agent": {"failover_chain": ["gpt-4o-mini", "claude-haiku-4-5-20251001"]}}
    calls: list[tuple[str, str]] = []

    def fake_openai(*args, **kwargs):
        calls.append(("openai", args[1]))
        raise AssertionError("openai should not be called when no key is set")

    def fake_anthropic(messages, model, *args, **kwargs):
        calls.append(("anthropic", model))
        return {
            "content": "ok",
            "model": model,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": None,
        }

    with patch.object(models, "_call_openai", side_effect=fake_openai), \
         patch.object(models, "_call_anthropic", side_effect=fake_anthropic):
        models.call_llm([{"role": "user", "content": "hi"}], config=config)

    assert calls == [("anthropic", "claude-haiku-4-5-20251001")]


def test_explicit_model_bypasses_failover_chain(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")

    config = {"agent": {"failover_chain": ["gpt-4o-mini", "claude-haiku-4-5-20251001"]}}
    calls: list[tuple[str, str]] = []

    def fake_openai(messages, model, *args, **kwargs):
        calls.append(("openai", model))
        raise RuntimeError("503")

    def fake_anthropic(messages, model, *args, **kwargs):
        calls.append(("anthropic", model))
        return {
            "content": "ok",
            "model": model,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": None,
        }

    with patch.object(models, "_call_openai", side_effect=fake_openai), \
         patch.object(models, "_call_anthropic", side_effect=fake_anthropic):
        with pytest.raises(RuntimeError):
            models.call_llm(
                [{"role": "user", "content": "hi"}],
                model="gpt-4o-mini",
                config=config,
            )

    # Explicit model means no chain — caller asked for THIS one.
    assert calls == [("openai", "gpt-4o-mini")]


def test_single_entry_chain_attempted_even_when_cooled_down(monkeypatch):
    """Last-resort behavior: if every chain entry is cooled down, attempt
    the primary anyway rather than stalling completely."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")

    # Pre-cool openai
    models._provider_cooldowns["openai"] = (1e20, 5)

    config = {"agent": {"failover_chain": ["gpt-4o-mini"]}}
    calls: list[tuple[str, str]] = []

    def fake_openai(messages, model, *args, **kwargs):
        calls.append(("openai", model))
        raise RuntimeError("503")

    with patch.object(models, "_call_openai", side_effect=fake_openai):
        with pytest.raises(RuntimeError):
            models.call_llm([{"role": "user", "content": "hi"}], config=config)

    # Single-entry chain is attempted as last resort
    assert calls == [("openai", "gpt-4o-mini")]


def test_no_chain_falls_back_to_default_model(monkeypatch):
    """Back-compat: configs without failover_chain still work."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")

    config = {"agent": {"default_model": "gpt-4o-mini"}}
    calls: list[tuple[str, str]] = []

    def fake_openai(messages, model, *args, **kwargs):
        calls.append(("openai", model))
        return {
            "content": "ok",
            "model": model,
            "input_tokens": 1,
            "output_tokens": 1,
            "tool_calls": None,
        }

    with patch.object(models, "_call_openai", side_effect=fake_openai):
        models.call_llm([{"role": "user", "content": "hi"}], config=config)

    assert calls == [("openai", "gpt-4o-mini")]


def test_exhausted_chain_raises_with_attempted_summary(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-anthropic")

    config = {"agent": {"failover_chain": ["gpt-4o-mini", "claude-haiku-4-5-20251001"]}}

    def fake_call(messages, model, *args, **kwargs):
        raise RuntimeError(f"500 from {model}")

    with patch.object(models, "_call_openai", side_effect=fake_call), \
         patch.object(models, "_call_anthropic", side_effect=fake_call):
        with pytest.raises(RuntimeError, match="attempted"):
            models.call_llm([{"role": "user", "content": "hi"}], config=config)
