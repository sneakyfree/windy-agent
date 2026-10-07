"""Tests for the Mind broker routing path in agent.models.call_llm.

Per ADR-010 §8 + ADR-022 §5 (intelligence kernel + free-tier buffet):
  - When the agent has an Eternitas passport, call_llm() tries Mind first.
  - When Mind returns 200, that response is returned (direct chain skipped).
  - When Mind returns non-200 OR throws, falls through to the direct chain.
  - When Anthropic Max OAuth is active, Mind is skipped entirely (per
    ADR-022 exception register #1 — Max sub billing preserved).
  - When no EPT is configured, Mind path is a no-op (pre-hatch / test rigs).
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# Force the module to use a non-existent overrides file so dashboard data
# doesn't bleed into test mocks (same pattern as test_provider_failover).
os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import models, providers  # noqa: E402


def _reset_provider_state() -> None:
    models._provider_cooldowns.clear()
    for prov in providers.BUILTIN_PROVIDERS.values():
        prov.pop("api_key", None)
        prov.pop("configured", None)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    _reset_provider_state()
    for k in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GROK_API_KEY",
        "ETERNITAS_PASSPORT_TOKEN",
        "ETERNITAS_PASSPORT",
        "MIND_API_URL",
    ):
        monkeypatch.delenv(k, raising=False)
    yield
    _reset_provider_state()


# ─── Mind path no-op cases ────────────────────────────────────────────


def test_no_passport_no_mind_call(monkeypatch):
    """Without ETERNITAS_PASSPORT env, Mind path is a complete no-op —
    call_llm walks the direct-provider chain like before."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")

    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post"
    ) as mock_post:
        mock_openai.return_value = {"choices": [{"message": {"content": "direct"}}]}
        result = models.call_llm(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )
        assert result["choices"] == [{"message": {"content": "direct"}}]
        # httpx.post was never called — Mind path skipped entirely
        mock_post.assert_not_called()
        mock_openai.assert_called_once()


# ─── Mind happy path ──────────────────────────────────────────────────


def test_passport_present_calls_mind_first(monkeypatch):
    """With ETERNITAS_PASSPORT set, call_llm tries Mind first.
    If Mind 200s, response is returned, direct chain not called."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    mind_response = {
        "id": "mind-resp-1",
        "model": "cerebras-llama-3.3-70b",
        "choices": [{"message": {"content": "from mind"}}],
    }
    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post"
    ) as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value=mind_response),
        )
        mock_openai.return_value = {"choices": [{"message": {"content": "direct"}}]}

        result = models.call_llm(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )
        # Sprint 5: Mind's near-OpenAI JSON is translated into the flat
        # windyfly result shape the loop consumes (raw passthrough
        # would KeyError downstream — the pre-fix behavior).
        assert result["content"] == "from mind"
        assert result["mind_model"] == "cerebras-llama-3.3-70b"
        mock_post.assert_called_once()
        # Direct chain never invoked
        mock_openai.assert_not_called()


def test_passport_present_uses_custom_mind_url(monkeypatch):
    """MIND_API_URL env overrides the api.windymind.ai default."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    monkeypatch.setenv("MIND_API_URL", "http://mind.local:8900")

    with patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"role": "assistant", "content": "from mind"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        )
        models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs.get("url")
        assert url == "http://mind.local:8900/v1/chat"
        # EPT bearer auth was sent
        assert "Bearer ept_test_token" in kwargs["headers"]["Authorization"]


# ─── Mind fallthrough cases ───────────────────────────────────────────


def test_mind_500_with_a_passport_never_calls_a_provider_key(monkeypatch):
    """A Windy agent (passport) gets compute ONLY through Mind, else the local lifeboat (Boss 10-07):
    a Mind 5xx must not fall through to a provider key."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    with patch.object(models, "_call_openai") as mock_openai, patch("httpx.post") as mock_post:
        mock_post.return_value = MagicMock(status_code=503, text="Service Unavailable")
        with pytest.raises(RuntimeError, match="mind-only"):
            models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        mock_openai.assert_not_called()


def test_mind_500_without_a_passport_still_uses_the_own_key_chain(monkeypatch):
    """A standalone install (no passport) has no Mind: it keeps its own-key chain."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)

    with patch.object(models, "_call_openai") as mock_openai, patch("httpx.post") as mock_post:
        mock_openai.return_value = {"choices": [{"message": {"content": "direct"}}]}
        result = models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        assert result["choices"] == [{"message": {"content": "direct"}}]
        mock_openai.assert_called_once()
        mock_post.assert_not_called()


def test_passport_agent_falls_to_the_local_lifeboat_and_says_so(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    local = {"provider_key": "ollama", "type": "openai", "api_key": "ollama",
             "base_url": "http://localhost:11434/v1"}
    with patch.object(models, "_call_openai") as mock_openai, patch("httpx.post") as mock_post, \
            patch.object(models, "_build_chain", return_value=["gpt-4o-mini", "llama3.2:3b"]), \
            patch.object(models, "get_provider_for_model",
                         side_effect=lambda m, c=None: local if m.startswith("llama") else
                         {"provider_key": "openai", "type": "openai", "api_key": "k",
                          "base_url": "https://api.openai.com/v1"}):
        mock_post.return_value = MagicMock(status_code=503, text="down")
        mock_openai.return_value = {"content": "lifeboat", "input_tokens": 1, "output_tokens": 1,
                                    "tool_calls": [], "citations": [], "server_tools_used": []}
        with caplog.at_level("WARNING"):
            result = models.call_llm([{"role": "user", "content": "hi"}])
        assert result["content"] == "lifeboat"
        assert mock_openai.call_args.args[5].startswith("http://localhost")
        assert "served by the local lifeboat" in caplog.text


def test_mind_network_error_with_a_passport_does_not_use_a_provider_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post", side_effect=ConnectionError("network down")
    ):
        with pytest.raises(RuntimeError, match="mind-only"):
            models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        mock_openai.assert_not_called()


# ─── ADR-022 exception register: Max OAuth ────────────────────────────


def test_max_oauth_no_longer_bypasses_mind_for_a_passport_agent(monkeypatch):
    """Boss 10-07 (retires ADR-022 exception #1 for Windy agents): Max is reached THROUGH Mind, so a
    passport agent always tries Mind first even when a Max OAuth token is present."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")
    mock_oauth = MagicMock(access_token="oauth-token")

    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post"
    ) as mock_post, patch("windyfly.agent.oauth.get_oauth_manager", return_value=mock_oauth):
        mock_post.return_value = MagicMock(status_code=503, text="down")
        with pytest.raises(RuntimeError):
            models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        mock_post.assert_called()  # Mind WAS tried
        mock_openai.assert_not_called()


def test_max_oauth_unavailable_falls_through_to_mind(monkeypatch):
    """When oauth import fails (no oauth module on this build) or oauth
    manager returns None, treat as non-Max — Mind path still runs."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    with patch("httpx.post") as mock_post, patch(
        "windyfly.agent.oauth.get_oauth_manager", return_value=None
    ):
        mock_post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"choices": [{"message": {"role": "assistant", "content": "from mind"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}}),
        )
        models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        mock_post.assert_called_once()


# ─── Catalog-drift 422 retry (2026-07-17 live-caught) ────────────────


def _mind_ok(payload_model="auto-picked"):
    return MagicMock(
        status_code=200,
        json=MagicMock(return_value={
            "model": payload_model,
            "choices": [{
                "message": {"role": "assistant", "content": "from mind"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }),
    )


def test_mind_422_on_model_retries_modelless(monkeypatch):
    """Mind's /v1/chat validates `model` against a hardcoded enum. A slug
    Mind doesn't know (e.g. claude-haiku-4-5) must NOT knock the agent off
    its primary brain — retry once without `model` and let the broker
    route. Live-caught 2026-07-17."""
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    rejected = MagicMock(status_code=422, text='{"detail":[{"type":"enum","loc":["body","model"]}]}')
    with patch("httpx.post") as mock_post:
        mock_post.side_effect = [rejected, _mind_ok()]
        result = models.call_llm(
            [{"role": "user", "content": "hi"}],
            model="claude-haiku-4-5",
        )
        assert result["content"] == "from mind"
        assert mock_post.call_count == 2
        first_body = mock_post.call_args_list[0].kwargs["json"]
        retry_body = mock_post.call_args_list[1].kwargs["json"]
        assert first_body["model"] == "claude-haiku-4-5"
        assert "model" not in retry_body
        # Everything else survives the retry untouched
        assert retry_body["messages"] == first_body["messages"]


def test_mind_422_retry_fails_with_a_passport_stops_at_the_lifeboat_rule(monkeypatch):
    """The model-less retry also fails: no infinite retries and no provider key for a passport agent."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    rejected = MagicMock(status_code=422, text="enum")
    with patch.object(models, "_call_openai") as mock_openai, patch("httpx.post") as mock_post:
        mock_post.side_effect = [rejected, MagicMock(status_code=422, text="enum")]
        with pytest.raises(RuntimeError, match="mind-only"):
            models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        assert mock_post.call_count == 2
        mock_openai.assert_not_called()
