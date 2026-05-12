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
        assert result == {"choices": [{"message": {"content": "direct"}}]}
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
        assert result == mind_response
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
            json=MagicMock(return_value={"choices": []}),
        )
        models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        args, kwargs = mock_post.call_args
        url = args[0] if args else kwargs.get("url")
        assert url == "http://mind.local:8900/v1/chat"
        # EPT bearer auth was sent
        assert "Bearer ept_test_token" in kwargs["headers"]["Authorization"]


# ─── Mind fallthrough cases ───────────────────────────────────────────


def test_mind_500_falls_through_to_direct_chain(monkeypatch):
    """Mind broker returns 5xx → direct-provider chain takes over.
    Critical regression-safety: agent stays functional even if Mind dies."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post"
    ) as mock_post:
        mock_post.return_value = MagicMock(
            status_code=503,
            text="Service Unavailable",
        )
        mock_openai.return_value = {"choices": [{"message": {"content": "direct"}}]}

        result = models.call_llm(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )
        # Direct chain succeeded
        assert result == {"choices": [{"message": {"content": "direct"}}]}
        mock_openai.assert_called_once()


def test_mind_network_error_falls_through(monkeypatch):
    """httpx.post raises (network down, DNS fail, etc.) → direct chain wins."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post", side_effect=ConnectionError("network down")
    ):
        mock_openai.return_value = {"choices": [{"message": {"content": "direct"}}]}
        result = models.call_llm(
            [{"role": "user", "content": "hi"}],
            model="gpt-4o-mini",
        )
        assert result == {"choices": [{"message": {"content": "direct"}}]}
        mock_openai.assert_called_once()


# ─── ADR-022 exception register: Max OAuth ────────────────────────────


def test_max_oauth_active_skips_mind_entirely(monkeypatch):
    """Per ADR-022 exception #1: when Anthropic Max OAuth is active,
    Mind path is bypassed completely so Max sub billing is preserved.
    All other LLM calls (when OAuth NOT active) MUST route through Mind."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai")
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test_token")

    # Mock Max OAuth being active
    mock_oauth = MagicMock(access_token="oauth-token")

    with patch.object(models, "_call_openai") as mock_openai, patch(
        "httpx.post"
    ) as mock_post, patch(
        "windyfly.agent.oauth.get_oauth_manager", return_value=mock_oauth
    ):
        mock_openai.return_value = {"choices": [{"message": {"content": "direct"}}]}
        models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")

        # Mind was never called because Max OAuth is active
        mock_post.assert_not_called()
        mock_openai.assert_called_once()


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
            json=MagicMock(return_value={"choices": []}),
        )
        models.call_llm([{"role": "user", "content": "hi"}], model="gpt-4o-mini")
        mock_post.assert_called_once()
