"""Anthropic native web_search Tier 0 integration regression suite.

PR #164. Strategic context: provider-native server-side web search
(Anthropic's web_search_20250305) gives the model a research
capability tuned at training time — better quality than our client-
side web_search tool because the model knows when/how to search.
Cost is billed directly to the user's API key (BYOK passthrough).

Six test classes pin the contract:

  TestModelAllowlist — Claude 4.x prefixes accepted, others rejected
  TestKillSwitch — env var instantly disables Tier 0
  TestDailyCounter — date-rollover, atomic write, cap detection
  TestShouldInjectDecision — the single-call decision used by the loop
  TestCitationFormatter — Anthropic citations → Telegram footer
  TestUnsupportedToolDetector — heuristic for the defensive retry path
  TestModelsPyIntegration — _openai_tools_to_anthropic + response loop
  TestLoopIntegration — agent_respond inject/skip/retry/footer flow
"""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest


# ─── 1. Model allowlist ──────────────────────────────────────────


class TestModelAllowlist:

    def test_opus_4_supported(self):
        from windyfly.tools.native_web_search import is_model_supported
        assert is_model_supported("claude-opus-4-7") is True
        assert is_model_supported("claude-opus-4-6-20251014") is True

    def test_sonnet_4_supported(self):
        from windyfly.tools.native_web_search import is_model_supported
        assert is_model_supported("claude-sonnet-4-6") is True

    def test_haiku_4_supported_optimistically(self):
        """We optimistically allowlist Haiku 4.x even though docs
        don't enumerate basic-tool support — defensive retry covers
        false positives."""
        from windyfly.tools.native_web_search import is_model_supported
        assert is_model_supported("claude-haiku-4-5-20251001") is True

    def test_claude_3_NOT_supported(self):
        """3.x is too old for current web_search tool versions."""
        from windyfly.tools.native_web_search import is_model_supported
        assert is_model_supported("claude-3-5-sonnet-20241022") is False
        assert is_model_supported("claude-3-opus-20240229") is False

    def test_non_anthropic_NOT_supported(self):
        from windyfly.tools.native_web_search import is_model_supported
        assert is_model_supported("gpt-4o") is False
        assert is_model_supported("grok-4") is False
        assert is_model_supported("llama3.2:3b") is False

    def test_empty_or_none(self):
        from windyfly.tools.native_web_search import is_model_supported
        assert is_model_supported("") is False
        assert is_model_supported(None) is False


# ─── 2. Kill switch ──────────────────────────────────────────────


class TestKillSwitch:

    def test_env_unset_means_enabled(self, monkeypatch):
        monkeypatch.delenv("WINDY_NATIVE_WEB_SEARCH", raising=False)
        from windyfly.tools.native_web_search import is_killswitched
        assert is_killswitched() is False

    def test_zero_kills(self, monkeypatch):
        monkeypatch.setenv("WINDY_NATIVE_WEB_SEARCH", "0")
        from windyfly.tools.native_web_search import is_killswitched
        assert is_killswitched() is True

    def test_false_off_no_all_kill(self, monkeypatch):
        from windyfly.tools.native_web_search import is_killswitched
        for val in ("false", "off", "no", "FALSE", "Off"):
            monkeypatch.setenv("WINDY_NATIVE_WEB_SEARCH", val)
            assert is_killswitched() is True, f"failed for {val!r}"

    def test_one_does_not_kill(self, monkeypatch):
        monkeypatch.setenv("WINDY_NATIVE_WEB_SEARCH", "1")
        from windyfly.tools.native_web_search import is_killswitched
        assert is_killswitched() is False


# ─── 3. Daily counter ────────────────────────────────────────────


class TestDailyCounter:

    def test_default_zero(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".daily_search_count"))
        from windyfly.tools.native_web_search import daily_search_count
        assert daily_search_count() == 0

    def test_bump_persists(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".daily_search_count"))
        from windyfly.tools.native_web_search import (
            bump_daily_search_count, daily_search_count,
        )
        assert bump_daily_search_count(1) == 1
        assert daily_search_count() == 1
        assert bump_daily_search_count(3) == 4
        assert daily_search_count() == 4

    def test_date_rollover_resets_counter(self, monkeypatch, tmp_path):
        """A counter file from yesterday returns 0 today."""
        import json
        path = tmp_path / ".daily_search_count"
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER", str(path))
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        path.write_text(json.dumps({"date": yesterday, "count": 999}))

        from windyfly.tools.native_web_search import daily_search_count
        assert daily_search_count() == 0

    def test_corrupt_file_returns_zero(self, monkeypatch, tmp_path):
        path = tmp_path / ".daily_search_count"
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER", str(path))
        path.write_text("{not valid json")
        from windyfly.tools.native_web_search import daily_search_count
        assert daily_search_count() == 0

    def test_cap_reached_below_default(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".c"))
        from windyfly.tools.native_web_search import (
            bump_daily_search_count, cap_reached,
        )
        bump_daily_search_count(10)
        assert cap_reached() is False

    def test_cap_reached_at_threshold(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".c"))
        monkeypatch.setenv("WINDY_DAILY_SEARCH_CAP", "5")
        from windyfly.tools.native_web_search import (
            bump_daily_search_count, cap_reached,
        )
        bump_daily_search_count(5)
        assert cap_reached() is True

    def test_invalid_cap_env_falls_back_to_default(self, monkeypatch):
        from windyfly.tools.native_web_search import (
            DEFAULT_DAILY_SEARCH_CAP, daily_search_cap,
        )
        monkeypatch.setenv("WINDY_DAILY_SEARCH_CAP", "not-a-number")
        assert daily_search_cap() == DEFAULT_DAILY_SEARCH_CAP


# ─── 4. should_inject_native_tool decision ───────────────────────


class TestShouldInjectDecision:

    def test_supported_model_under_cap_no_killswitch(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WINDY_NATIVE_WEB_SEARCH", raising=False)
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".c"))
        from windyfly.tools.native_web_search import should_inject_native_tool
        out = should_inject_native_tool("claude-sonnet-4-6")
        assert out["inject"] is True
        assert out["reason"] == "ok"

    def test_killswitch_wins_even_on_supported_model(self, monkeypatch):
        monkeypatch.setenv("WINDY_NATIVE_WEB_SEARCH", "0")
        from windyfly.tools.native_web_search import should_inject_native_tool
        out = should_inject_native_tool("claude-sonnet-4-6")
        assert out["inject"] is False
        assert out["reason"] == "killswitched"

    def test_unsupported_model_skipped(self, monkeypatch):
        monkeypatch.delenv("WINDY_NATIVE_WEB_SEARCH", raising=False)
        from windyfly.tools.native_web_search import should_inject_native_tool
        out = should_inject_native_tool("gpt-4o")
        assert out["inject"] is False
        assert out["reason"] == "model_unsupported"

    def test_cap_reached_skipped(self, monkeypatch, tmp_path):
        monkeypatch.delenv("WINDY_NATIVE_WEB_SEARCH", raising=False)
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".c"))
        monkeypatch.setenv("WINDY_DAILY_SEARCH_CAP", "3")
        from windyfly.tools.native_web_search import (
            bump_daily_search_count, should_inject_native_tool,
        )
        bump_daily_search_count(3)
        out = should_inject_native_tool("claude-sonnet-4-6")
        assert out["inject"] is False
        assert out["reason"] == "cap_reached"


# ─── 5. Citation formatter ───────────────────────────────────────


class TestCitationFormatter:

    def test_empty_returns_empty_string(self):
        from windyfly.tools.native_web_search import format_citations_footer
        assert format_citations_footer(None) == ""
        assert format_citations_footer([]) == ""

    def test_single_citation(self):
        from windyfly.tools.native_web_search import format_citations_footer
        out = format_citations_footer([
            {"url": "https://example.com/a", "title": "Page A"},
        ])
        assert "Sources:" in out
        assert "Page A" in out
        assert "https://example.com/a" in out

    def test_dedupes_by_url(self):
        from windyfly.tools.native_web_search import format_citations_footer
        out = format_citations_footer([
            {"url": "https://example.com/a", "title": "Page A"},
            {"url": "https://example.com/a", "title": "Page A"},
            {"url": "https://example.com/a", "title": "Page A"},
        ])
        # Only one entry rendered.
        assert out.count("https://example.com/a") == 1

    def test_skips_citations_without_url(self):
        from windyfly.tools.native_web_search import format_citations_footer
        out = format_citations_footer([
            {"title": "no url"},
            {"url": "", "title": "empty url"},
            {"url": "https://example.com/c", "title": "good"},
        ])
        # Only the third entry rendered.
        assert "good" in out
        assert "no url" not in out

    def test_uses_url_when_title_missing(self):
        from windyfly.tools.native_web_search import format_citations_footer
        out = format_citations_footer([
            {"url": "https://example.com/x"},
        ])
        # Title falls back to URL.
        assert "https://example.com/x" in out


# ─── 6. Unsupported-tool error detector ──────────────────────────


class TestUnsupportedToolDetector:

    def test_anthropic_unsupported_message(self):
        from windyfly.tools.native_web_search import is_unsupported_tool_error
        e = Exception("tool web_search_20250305 is not supported on this model")
        assert is_unsupported_tool_error(e) is True

    def test_invalid_request_with_tool_mention(self):
        from windyfly.tools.native_web_search import is_unsupported_tool_error
        e = Exception("invalid_request_error: tool spec invalid")
        assert is_unsupported_tool_error(e) is True

    def test_unrelated_error_NOT_treated_as_unsupported(self):
        """A 401 or 500 isn't an unsupported-tool error and shouldn't
        trigger the retry-without-native path — that would mask
        legitimate provider failures."""
        from windyfly.tools.native_web_search import is_unsupported_tool_error
        assert is_unsupported_tool_error(Exception("rate limit exceeded")) is False
        assert is_unsupported_tool_error(Exception("401 unauthorized")) is False
        assert is_unsupported_tool_error(Exception("connection timed out")) is False


# ─── 7. models.py integration: tool translator + response parser ─


class TestModelsPyIntegration:

    def test_server_side_tool_passes_through_translator(self):
        """``_openai_tools_to_anthropic`` must NOT wrap server-side
        tools in the OpenAI ``function`` envelope. They go to
        Anthropic by ``type`` directly."""
        from windyfly.agent.models import _openai_tools_to_anthropic
        tools = [
            {"type": "web_search_20250305", "name": "web_search",
             "max_uses": 5},
            {"type": "function", "function": {
                "name": "fetch_url",
                "description": "fetch",
                "parameters": {"type": "object", "properties": {}},
            }},
        ]
        out = _openai_tools_to_anthropic(tools)
        assert len(out) == 2
        # Server-side tool: passed through, NOT wrapped.
        assert out[0]["type"] == "web_search_20250305"
        assert out[0]["name"] == "web_search"
        assert out[0]["max_uses"] == 5
        assert "input_schema" not in out[0]
        # Client-side tool: translated to Anthropic shape.
        assert out[1]["name"] == "fetch_url"
        assert "input_schema" in out[1]

    def test_response_parser_skips_server_tool_use_blocks(self):
        """``server_tool_use`` and ``web_search_tool_result`` blocks
        must NOT enter ``tool_calls`` (they're already executed
        server-side; dispatching client-side would fail)."""
        from windyfly.agent.models import _call_anthropic

        # Build a fake response with mixed block types.
        text_block = MagicMock()
        text_block.type = "text"
        text_block.text = "Brian Hill is a loan officer in Austin."
        text_block.citations = [
            MagicMock(model_dump=lambda: {
                "url": "https://nmls.org/brian", "title": "NMLS - Brian Hill",
                "cited_text": "Brian Hill, NMLS #12345",
            }),
        ]

        server_tool_block = MagicMock()
        server_tool_block.type = "server_tool_use"

        result_block = MagicMock()
        result_block.type = "web_search_tool_result"

        fake_resp = MagicMock()
        fake_resp.content = [server_tool_block, result_block, text_block]
        fake_resp.usage = MagicMock(input_tokens=100, output_tokens=50)

        with patch("anthropic.Anthropic") as ANC:
            ANC.return_value.messages.create.return_value = fake_resp
            result = _call_anthropic(
                messages=[{"role": "user", "content": "research brian"}],
                model="claude-sonnet-4-6",
                temperature=0.7, max_tokens=2000,
                tools=[{"type": "web_search_20250305", "name": "web_search"}],
                api_key="sk-ant-test",
            )

        # Text content extracted.
        assert "Brian Hill" in result["content"]
        # No tool_calls — server-side blocks were filtered.
        assert result.get("tool_calls") is None
        # Citation harvested.
        assert len(result["citations"]) == 1
        assert result["citations"][0]["url"] == "https://nmls.org/brian"
        # Server-tool-use counter incremented.
        assert result["server_tools_used"] == 1

    def test_openai_response_returns_empty_citation_defaults(self):
        """Non-Anthropic providers should return empty citations and
        zero server_tools_used so the agent loop doesn't have to
        branch on provider."""
        from windyfly.agent.models import _call_openai

        fake_choice = MagicMock()
        fake_choice.message.content = "hello"
        fake_choice.message.tool_calls = None
        fake_resp = MagicMock(
            choices=[fake_choice],
            usage=MagicMock(prompt_tokens=10, completion_tokens=5),
        )

        with patch("openai.OpenAI") as OAI:
            OAI.return_value.chat.completions.create.return_value = fake_resp
            result = _call_openai(
                messages=[{"role": "user", "content": "hi"}],
                model="gpt-4o", temperature=0.7, max_tokens=2000,
                tools=None, api_key="sk-test",
            )
        assert result["citations"] == []
        assert result["server_tools_used"] == 0


# ─── 8. agent_respond integration ────────────────────────────────


def _make_config():
    return {
        "agent": {"default_model": "claude-sonnet-4-6",
                  "max_context_tokens": 8000, "max_response_tokens": 2000,
                  "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20,
                   "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "humor_level": 7,
                        "formality": 4, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 6, "autonomy": 3,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


@pytest.fixture
def stack(monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue(); wq.start()
    yield _make_config(), db, wq
    try: wq.stop()
    except Exception: pass
    db.close()


class TestLoopIntegration:

    def test_native_tool_injected_when_supported_model(self, stack, monkeypatch):
        """When agent_respond runs on Claude with native enabled,
        ``call_llm`` must be called with the native tool in tools."""
        config, db, wq = stack
        captured: dict = {}

        def fake_call(messages, *, model, temperature, max_tokens, tools, config, **kwargs):
            captured["tools"] = tools
            return {
                "content": "Researched answer", "input_tokens": 50,
                "output_tokens": 30, "tool_calls": None,
                "model": model, "citations": [], "server_tools_used": 0,
            }

        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=fake_call):
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "research X", "session-A")

        # Native tool present in the tools list.
        types = [t.get("type") for t in (captured["tools"] or [])]
        assert "web_search_20250305" in types

    def test_killswitch_blocks_injection(self, stack, monkeypatch):
        config, db, wq = stack
        monkeypatch.setenv("WINDY_NATIVE_WEB_SEARCH", "0")
        captured: dict = {}

        def fake_call(messages, *, model, temperature, max_tokens, tools, config, **kwargs):
            captured["tools"] = tools
            return {
                "content": "Standard answer", "input_tokens": 10,
                "output_tokens": 5, "tool_calls": None,
                "model": model, "citations": [], "server_tools_used": 0,
            }

        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=fake_call):
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "hi", "session-B")

        types = [t.get("type") for t in (captured["tools"] or [])]
        assert "web_search_20250305" not in types

    def test_citations_appended_to_response(self, stack, monkeypatch):
        """When the model returns citations, the response gets a
        Sources: footer that's visible to the user."""
        config, db, wq = stack
        paid_with_citations = {
            "content": "Brian Hill works in Austin.",
            "input_tokens": 100, "output_tokens": 50,
            "tool_calls": None, "model": "claude-sonnet-4-6",
            "citations": [
                {"url": "https://nmls.org/brian", "title": "NMLS - Brian Hill"},
            ],
            "server_tools_used": 1,
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=paid_with_citations):
            from windyfly.agent.loop import agent_respond
            reply = agent_respond(config, db, wq, "research brian", "session-C")
        assert "Brian Hill works in Austin" in reply
        assert "Sources:" in reply
        assert "https://nmls.org/brian" in reply

    def test_unsupported_tool_error_retries_without_native(self, stack):
        """When Anthropic rejects the native tool with an unsupported-
        tool error, the loop must retry once WITHOUT it and succeed."""
        config, db, wq = stack
        retry_capture: dict = {"calls": []}

        def fake_call(messages, *, model, temperature, max_tokens, tools, config, **kwargs):
            retry_capture["calls"].append(tools)
            if len(retry_capture["calls"]) == 1:
                # First call has the native tool — reject with
                # the kind of error message we look for.
                raise Exception("tool web_search_20250305 is not supported")
            # Second call without it succeeds.
            return {
                "content": "OK plain answer", "input_tokens": 10,
                "output_tokens": 5, "tool_calls": None,
                "model": model, "citations": [], "server_tools_used": 0,
            }

        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=fake_call):
            from windyfly.agent.loop import agent_respond
            reply = agent_respond(config, db, wq, "hi", "session-D")

        # Two calls — first with native, second without.
        assert len(retry_capture["calls"]) == 2
        first_types = [t.get("type") for t in (retry_capture["calls"][0] or [])]
        second_types = [t.get("type") for t in (retry_capture["calls"][1] or [])]
        assert "web_search_20250305" in first_types
        assert "web_search_20250305" not in second_types
        assert "OK plain answer" in reply

    def test_counter_bumps_on_native_use(self, stack, monkeypatch, tmp_path):
        """Each server_tool_use block increments the daily counter."""
        config, db, wq = stack
        monkeypatch.setenv("WINDY_DAILY_SEARCH_COUNTER",
                           str(tmp_path / ".counter"))

        used_3 = {
            "content": "Researched 3x", "input_tokens": 200,
            "output_tokens": 100, "tool_calls": None,
            "model": "claude-sonnet-4-6", "citations": [],
            "server_tools_used": 3,
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=used_3):
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "research X", "session-E")

        from windyfly.tools.native_web_search import daily_search_count
        assert daily_search_count() == 3
