"""Strand v2, Part 5 Phase 7: providers (docs/strand/STRAND_V2.md). Temp state, stub HTTP, fake
tokens; no network, no home."""

from __future__ import annotations

import json
import os
import stat
import time
from unittest.mock import MagicMock

from windyfly.agent import oauth
from windyfly.agent.providers import BUILTIN_PROVIDERS, get_provider_for_model

# The strand's table (it says 11; it lists 12)
SPEC_PROVIDERS = {"openai", "anthropic", "grok", "gemini", "deepseek", "mistral", "openrouter",
                  "together", "groq", "perplexity", "fireworks", "ollama"}


def test_p5_71_the_strands_built_in_providers_are_registered():
    assert SPEC_PROVIDERS <= set(BUILTIN_PROVIDERS)
    for key in SPEC_PROVIDERS:
        prov = BUILTIN_PROVIDERS[key]
        assert prov["type"] and prov["base_url"].startswith("http"), key
        assert "api_key" not in prov, key                                   # keys only from the env


def test_p5_77_a_custom_provider_from_windyfly_toml_is_used_for_its_models(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_PROVIDERS_PATH", str(tmp_path / "none.json"))
    monkeypatch.setenv("WINDY_PROVIDER_MYCO_API_KEY", "test-key-not-real")
    config = {"providers": {"myco": {"base_url": "https://llm.example.test/v1", "models": ["myco-1"]}}}
    prov = get_provider_for_model("myco-1", config)
    assert (prov["provider_key"], prov["base_url"]) == ("myco", "https://llm.example.test/v1")
    assert prov["type"] == "openai" and prov["api_key"] == "test-key-not-real" and prov["configured"]


def _token_server(monkeypatch):
    post = MagicMock(return_value=MagicMock(json=lambda: {
        "access_token": "new-access", "refresh_token": "new-refresh", "expires_in": 3600}))
    monkeypatch.setattr(oauth.httpx, "post", post)
    return post


def test_p5_78_a_token_near_expiry_is_refreshed_and_cached_privately(tmp_path, monkeypatch):
    cache = tmp_path / "oauth.json"
    monkeypatch.setattr(oauth, "_TOKEN_CACHE_PATH", cache)
    post = _token_server(monkeypatch)
    soon = int(time.time() * 1000) + 60_000                                  # inside the 5-minute buffer
    mgr = oauth.OAuthManager("old-access", "old-refresh", soon)
    assert mgr.access_token == "new-access"
    assert post.call_args.kwargs["json"] == {"grant_type": "refresh_token", "refresh_token": "old-refresh"}
    assert json.loads(cache.read_text())["refresh_token"] == "new-refresh"
    assert stat.S_IMODE(os.stat(cache).st_mode) == 0o600


def test_p5_78_a_token_far_from_expiry_is_used_as_is(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth, "_TOKEN_CACHE_PATH", tmp_path / "oauth.json")
    post = _token_server(monkeypatch)
    later = int(time.time() * 1000) + 3_600_000
    assert oauth.OAuthManager("old-access", "old-refresh", later).access_token == "old-access"
    assert not post.called


def test_p5_76_a_model_chosen_with_slash_model_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_SESSION_COUNTER_PATH", str(tmp_path / "counters.json"))
    from windyfly.agent import session_reset
    session_reset._reset_module_state_for_tests()
    session_reset.set_model("telegram", "chat-1", "claude-opus-4-7")
    session_reset._reset_module_state_for_tests()                          # a restart: memory gone
    assert session_reset.get_model("telegram", "chat-1") == "claude-opus-4-7"


def test_p5_78_without_an_oauth_token_the_manager_steps_aside_for_the_api_key(tmp_path, monkeypatch):
    for var in ("ANTHROPIC_OAUTH_ACCESS_TOKEN", "ANTHROPIC_OAUTH_REFRESH_TOKEN", "ANTHROPIC_OAUTH_EXPIRES_AT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(oauth, "_TOKEN_CACHE_PATH", tmp_path / "none.json")
    monkeypatch.setattr(oauth, "_manager", None)
    assert oauth.get_oauth_manager() is None
    monkeypatch.setenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", "test-access-not-real")
    assert oauth.get_oauth_manager().access_token == "test-access-not-real"
