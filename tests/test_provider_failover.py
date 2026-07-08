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


def test_oauth_reload_and_retry_on_stale_token_401(monkeypatch):
    """A mid-run OAuth rotation shows up as a 401 on the Anthropic path.
    The chain must reload the token and retry the SAME provider once,
    recovering without falling to the lifeboat (grandma self-heal, 2026-07-06)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "stale-token")
    config = {"agent": {"failover_chain": ["claude-opus-4-8"]}}
    calls: list[str] = []

    def fake_anthropic(messages, model, temperature, max_tokens, tools,
                       api_key="", **kwargs):
        calls.append(api_key)
        if api_key == "stale-token":
            raise RuntimeError("401 invalid x-api-key")
        return {"content": "ok", "tool_calls": None}

    def fake_reload():
        os.environ["ANTHROPIC_API_KEY"] = "fresh-token"
        return True

    with patch.object(models, "_call_anthropic", fake_anthropic), \
         patch.object(models, "_reload_oauth_token", fake_reload), \
         patch.object(models, "_try_mind_broker", return_value=None):
        out = models.call_llm([{"role": "user", "content": "hi"}], config=config)

    assert out["content"] == "ok"
    assert calls == ["stale-token", "fresh-token"]  # retried with the new key


def test_no_reload_retry_on_non_auth_error(monkeypatch):
    """A 500 is not an auth problem — don't reload/retry, just fail forward."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "tok")
    config = {"agent": {"failover_chain": ["claude-opus-4-8"]}}
    reloaded = []

    def fake_anthropic(messages, model, *a, **k):
        raise RuntimeError("500 internal error")

    with patch.object(models, "_call_anthropic", fake_anthropic), \
         patch.object(models, "_reload_oauth_token",
                      lambda: reloaded.append(1) or True), \
         patch.object(models, "_try_mind_broker", return_value=None):
        with pytest.raises(RuntimeError):
            models.call_llm([{"role": "user", "content": "hi"}], config=config)
    assert reloaded == []  # never attempted a token reload for a 500


def test_api_key_re_read_from_env_on_every_call(monkeypatch):
    """A rotated env token must be visible on the NEXT call — the
    provider table cannot fossilize the first key it ever resolved
    (windy-0 went dark on a rotated OAuth token, 2026-07-08)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "token-one")
    assert providers.get_all_providers()["anthropic"]["api_key"] == "token-one"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "token-two")
    assert providers.get_all_providers()["anthropic"]["api_key"] == "token-two"


def test_builtin_providers_not_polluted_by_env_resolution(monkeypatch):
    """get_all_providers must not write resolved keys back into the
    module-level BUILTIN_PROVIDERS dicts."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "leaky-token")
    providers.get_all_providers()
    assert "api_key" not in providers.BUILTIN_PROVIDERS["anthropic"]
    assert "configured" not in providers.BUILTIN_PROVIDERS["anthropic"]


def test_401_retries_with_env_token_even_when_reload_reports_no_change(monkeypatch):
    """The 401 retry must fire whenever the failed key differs from the
    current env token, even if _reload_oauth_token() returns False —
    the failed key may be a fossil (baked config / stale snapshot)
    while a previous turn already refreshed the env. Without this,
    the second 401 lands in the permanent-auth 1h cooldown with a
    perfectly good token sitting in the env (windy-0, 2026-07-08)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fresh-token")
    config = {
        "agent": {"failover_chain": ["claude-opus-4-8"]},
        "providers": {"anthropic": {"api_key": "stale-token"}},
    }
    calls: list[str] = []

    def fake_anthropic(messages, model, temperature, max_tokens, tools,
                       api_key="", **kwargs):
        calls.append(api_key)
        if api_key == "stale-token":
            raise RuntimeError("401 invalid x-api-key")
        return {"content": "ok", "tool_calls": None}

    with patch.object(models, "_call_anthropic", fake_anthropic), \
         patch.object(models, "_reload_oauth_token", return_value=False), \
         patch.object(models, "_try_mind_broker", return_value=None):
        out = models.call_llm([{"role": "user", "content": "hi"}], config=config)

    assert out["content"] == "ok"
    assert calls == ["stale-token", "fresh-token"]
    assert "anthropic" not in models._provider_cooldowns
