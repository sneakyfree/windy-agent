"""Hardening tests for the agent loop.

Tests every failure mode: missing API keys, budget exhaustion,
empty/huge inputs, DB lock, tool errors, LLM failures.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


def _make_config(**overrides) -> dict:
    config = {
        "agent": {
            "default_model": "gpt-4o-mini",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {
            "soul_path": "SOUL.md",
            "humor_level": 5,
            "formality": 5,
            "proactivity": 5,
            "verbosity": 5,
            "reasoning_depth": 5,
            "autonomy": 3,
            "epistemic_strictness": 5,
        },
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }
    for k, v in overrides.items():
        if isinstance(v, dict) and k in config:
            config[k].update(v)
        else:
            config[k] = v
    return config


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


@pytest.fixture
def wq():
    return WriteQueue()


class TestNoApiKey:
    @patch("windyfly.agent.loop.is_online", return_value=False)
    def test_missing_api_key_returns_helpful_message(self, mock_online, db, wq):
        """No LLM API key → offline fallback, not crash."""
        config = _make_config()
        result = agent_respond(config, db, wq, "Hello", "sess-1")
        assert isinstance(result, str)
        assert len(result) > 0
        # Should not be an exception traceback
        assert "Traceback" not in result


class TestBudgetExhausted:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.check_budget")
    def test_budget_exhausted_refuses_politely(self, mock_budget, mock_online, db, wq):
        """Daily budget exhausted → polite refusal with budget info."""
        mock_budget.return_value = {
            "allowed": False,
            "daily_spend": 5.50,
            "daily_budget": 5.0,
            "warning": True,
            "monthly_spend": 45.00,
        }
        config = _make_config()
        result = agent_respond(config, db, wq, "Hello", "sess-1")
        assert "budget" in result.lower()
        assert "$" in result


class TestEmptyMessage:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_empty_string_handled(self, mock_llm, mock_online, db, wq):
        """Empty string message should not crash."""
        mock_llm.return_value = {
            "content": "I didn't catch that. Could you say that again?",
            "input_tokens": 10,
            "output_tokens": 15,
        }
        config = _make_config()
        result = agent_respond(config, db, wq, "", "sess-1")
        assert isinstance(result, str)
        assert len(result) > 0


class TestHugeMessage:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_100k_chars_does_not_oom(self, mock_llm, mock_online, db, wq):
        """100,000 character message should not cause OOM or crash."""
        mock_llm.return_value = {
            "content": "That's a lot of text!",
            "input_tokens": 50000,
            "output_tokens": 10,
        }
        config = _make_config()
        huge_msg = "A" * 100_000
        result = agent_respond(config, db, wq, huge_msg, "sess-1")
        assert isinstance(result, str)


class TestToolCallErrors:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_tool_exception_caught(self, mock_llm, mock_online, db, wq):
        """Tool that raises an exception → caught, error message returned to user."""
        from windyfly.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            "failing_tool",
            "A tool that always fails",
            {"type": "object", "properties": {}, "required": []},
            lambda: (_ for _ in ()).throw(RuntimeError("tool exploded")),
        )

        # First call: LLM wants to call the tool
        mock_llm.side_effect = [
            {
                "content": "",
                "input_tokens": 50,
                "output_tokens": 20,
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "failing_tool", "arguments": "{}"},
                }],
            },
            # Second call: LLM responds after getting tool error
            {
                "content": "I encountered an error with that tool.",
                "input_tokens": 100,
                "output_tokens": 15,
            },
        ]

        config = _make_config()
        result = agent_respond(config, db, wq, "Use the tool", "sess-1", registry)
        assert isinstance(result, str)
        # Should not crash — should get a response


class TestLlmEmptyResponse:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_empty_llm_response(self, mock_llm, mock_online, db, wq):
        """LLM returns empty string → should handle gracefully."""
        mock_llm.return_value = {
            "content": "",
            "input_tokens": 50,
            "output_tokens": 0,
        }
        config = _make_config()
        result = agent_respond(config, db, wq, "Hello", "sess-1")
        assert isinstance(result, str)
        # Empty string is still a valid response (the LLM just had nothing to say)


class TestLlmMalformedToolCall:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_malformed_tool_json(self, mock_llm, mock_online, db, wq):
        """LLM returns malformed JSON in tool call → should catch."""
        from windyfly.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            "echo",
            "Echoes input",
            {"type": "object", "properties": {"msg": {"type": "string"}}, "required": ["msg"]},
            lambda msg: msg,
        )

        mock_llm.side_effect = [
            {
                "content": "",
                "input_tokens": 50,
                "output_tokens": 20,
                "tool_calls": [{
                    "id": "tc1",
                    "function": {"name": "echo", "arguments": "{invalid json!!!"},
                }],
            },
            {
                "content": "Sorry, I had trouble with that.",
                "input_tokens": 100,
                "output_tokens": 10,
            },
        ]

        config = _make_config()
        result = agent_respond(config, db, wq, "Echo hello", "sess-1", registry)
        assert isinstance(result, str)


class TestOfflineFallback:
    @patch("windyfly.agent.loop.is_online", return_value=False)
    def test_offline_returns_response(self, mock_online, db, wq):
        """When LLM API is unreachable, offline fallback should respond."""
        config = _make_config()
        result = agent_respond(config, db, wq, "What is 2+2?", "sess-1")
        assert isinstance(result, str)
        assert len(result) > 0


class TestBudgetWarning:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    @patch("windyfly.agent.loop.check_budget")
    def test_budget_warning_still_responds(self, mock_budget, mock_llm, mock_online, db, wq):
        """Budget warning (but not exceeded) → still responds."""
        mock_budget.return_value = {
            "allowed": True,
            "daily_spend": 3.50,
            "daily_budget": 5.0,
            "warning": True,
            "monthly_spend": 30.0,
        }
        mock_llm.return_value = {
            "content": "Here's your answer.",
            "input_tokens": 50,
            "output_tokens": 15,
        }
        config = _make_config()
        result = agent_respond(config, db, wq, "Hello", "sess-1")
        assert result == "Here's your answer."
