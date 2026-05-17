"""End-to-end test: verify LLM tool calling works through the agent loop.

Proves that:
1. Tools register correctly with valid schemas
2. Individual tools execute and return results
3. The agent loop handles tool calls from the LLM correctly
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import register_web_search_tool
from windyfly.tools.reminders import register_reminder_tools
from windyfly.tools.todos import register_todo_tools
from windyfly.tools.weather import register_weather_tool
from windyfly.tools.news import register_news_tool
from windyfly.tools.calendar import register_calendar_tools
from windyfly.tools.utilities import register_utility_tools


def _make_registry():
    """Create a full tool registry with all tools."""
    registry = ToolRegistry()
    db = Database(":memory:")
    register_web_search_tool(registry)
    register_reminder_tools(registry, db)
    register_todo_tools(registry, db)
    register_weather_tool(registry)
    register_news_tool(registry)
    register_calendar_tools(registry)
    register_utility_tools(registry)
    return registry, db


def _make_config():
    return {
        "agent": {"default_model": "gpt-4o-mini", "max_context_tokens": 8000, "max_response_tokens": 2000, "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


# ═══════════════════════════════════════════════════════════════
# 1. Tool Registration Verification
# ═══════════════════════════════════════════════════════════════


class TestToolRegistration:
    def test_tool_count_minimum(self):
        """Agent should have at least 15 LLM-callable tools."""
        registry, db = _make_registry()
        assert registry.tool_count >= 15, f"Only {registry.tool_count} tools"
        db.close()

    def test_critical_tools_registered(self):
        """All critical tools must be present."""
        registry, db = _make_registry()
        names = {s["function"]["name"] for s in registry.get_schemas()}

        critical = ["get_weather", "set_reminder", "add_todo", "web_search",
                     "get_news", "fetch_url", "calculate", "convert_units",
                     "flip_coin", "roll_dice", "set_timer",
                     "list_reminders", "cancel_reminder", "list_todos", "complete_todo"]

        missing = set(critical) - names
        assert not missing, f"Missing critical tools: {missing}"
        db.close()

    def test_schemas_valid_for_llm(self):
        """All schemas should be valid OpenAI function-calling format."""
        registry, db = _make_registry()
        for schema in registry.get_schemas():
            assert schema["type"] == "function"
            fn = schema["function"]
            assert isinstance(fn["name"], str) and len(fn["name"]) > 0
            assert isinstance(fn["description"], str) and len(fn["description"]) > 10
            assert fn["parameters"]["type"] == "object"
        db.close()


# ═══════════════════════════════════════════════════════════════
# 2. Individual Tool Execution
# ═══════════════════════════════════════════════════════════════


class TestToolExecution:
    def test_weather_via_registry(self):
        """get_weather should execute through registry and return structured data."""
        registry, db = _make_registry()
        with patch("windyfly.tools.weather.httpx.get") as mock_get:
            geo = MagicMock()
            geo.status_code = 200
            geo.json.return_value = {"results": [{"name": "Boston", "admin1": "MA", "latitude": 42.3, "longitude": -71.0, "country": "US"}]}
            wx = MagicMock()
            wx.status_code = 200
            wx.json.return_value = {
                "current_weather": {"temperature": 68.0, "weathercode": 1, "windspeed": 8.0},
                "daily": {"temperature_2m_max": [72.0], "temperature_2m_min": [55.0]},
            }
            mock_get.side_effect = [geo, wx]

            result = registry.execute("get_weather", {"location": "Boston"})
            assert "68" in result
            assert "Boston" in result
        db.close()

    def test_reminder_via_registry(self):
        """set_reminder should create entry and return confirmation."""
        registry, db = _make_registry()
        result = registry.execute("set_reminder", {"message": "Call dentist", "time": "in 30 minutes"})
        assert "remind" in result.lower() or "call dentist" in result.lower()
        db.close()

    def test_todo_via_registry(self):
        """add_todo should create entry."""
        registry, db = _make_registry()
        result = registry.execute("add_todo", {"title": "Buy groceries"})
        assert "added" in result.lower() or "buy groceries" in result.lower()
        db.close()

    def test_web_search_via_registry(self, monkeypatch):
        """web_search should return results when env is configured.
        Post Search V1 hard gate (2026-05-17): routes through
        api.windysearch.com — mock the windy-search client, not httpx."""
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        registry, db = _make_registry()
        with patch("windyfly.tools.web_search.search_via_windy_search") as ws:
            ws.return_value = {
                "query": "python",
                "results": [{"title": "Python", "snippet": "Python is a programming language", "url": "https://python.org"}],
                "provider": "windy-search:brave",
            }
            result = registry.execute("web_search", {"query": "python"})
            assert "python" in result.lower()
        db.close()

    def test_calculate_via_registry(self):
        """calculate should evaluate math."""
        registry, db = _make_registry()
        result = registry.execute("calculate", {"expression": "15 * 0.15"})
        assert "2.25" in result
        db.close()

    def test_fetch_url_via_registry(self, monkeypatch):
        """fetch_url should return cleaned text when env is configured.
        Post Search V1 hard gate (2026-05-17): routes through
        api.windysearch.com — mock the windy-search client, not httpx."""
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        registry, db = _make_registry()
        with patch("windyfly.tools.web_search.fetch_via_windy_search") as ws:
            ws.return_value = {
                "url": "https://example.com",
                "content": "Hello world",
                "total_length": 11,
                "provider": "windy-search",
            }
            result = registry.execute("fetch_url", {"url": "https://example.com"})
            assert "Hello world" in result
        db.close()


# ═══════════════════════════════════════════════════════════════
# 3. Agent Loop with Tool Calls (Mocked LLM)
# ═══════════════════════════════════════════════════════════════


class TestAgentLoopToolCalling:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_agent_executes_weather_tool(self, mock_llm, mock_online):
        """When LLM returns a tool call, agent executes it and responds."""
        import windyfly.agent.context_header as _ch
        import windyfly.agent.loop as _loop
        _ch._tracker = None
        _loop._interaction_count = 0
        _loop._session_tokens_used = 0

        from windyfly.agent.loop import agent_respond

        # First LLM call returns a tool call
        # Second LLM call (after tool result) returns final answer
        mock_llm.side_effect = [
            {
                "content": "",
                "model": "gpt-4o-mini",
                "input_tokens": 50,
                "output_tokens": 10,
                "tool_calls": [{
                    "id": "call_weather_1",
                    "function": {
                        "name": "flip_coin",
                        "arguments": {},
                    },
                }],
            },
            {
                "content": "I flipped a coin and got Heads!",
                "model": "gpt-4o-mini",
                "input_tokens": 80,
                "output_tokens": 15,
                "tool_calls": None,
            },
        ]

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        registry, _ = _make_registry()

        response = agent_respond(_make_config(), db, wq, "Flip a coin", "test-session", registry)

        # Verify: LLM was called twice (initial + after tool result)
        assert mock_llm.call_count == 2
        # Verify: response contains the final answer
        assert "Heads" in response

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_agent_handles_unknown_tool_gracefully(self, mock_llm, mock_online):
        """If LLM calls a non-existent tool, agent should handle gracefully."""
        import windyfly.agent.context_header as _ch
        import windyfly.agent.loop as _loop
        _ch._tracker = None
        _loop._interaction_count = 0

        from windyfly.agent.loop import agent_respond

        mock_llm.side_effect = [
            {
                "content": "",
                "model": "gpt-4o-mini",
                "input_tokens": 50,
                "output_tokens": 10,
                "tool_calls": [{
                    "id": "call_fake",
                    "function": {"name": "nonexistent_tool", "arguments": {}},
                }],
            },
            {
                "content": "Sorry, I encountered an issue.",
                "model": "gpt-4o-mini",
                "input_tokens": 80,
                "output_tokens": 15,
                "tool_calls": None,
            },
        ]

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        registry, _ = _make_registry()

        # Should not crash — error is caught and fed back to LLM
        response = agent_respond(_make_config(), db, wq, "Do something impossible", "test-session", registry)
        assert isinstance(response, str)

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_agent_stops_after_max_tool_rounds(self, mock_llm, mock_online):
        """Agent should stop re-looping after max tool rounds."""
        import windyfly.agent.context_header as _ch
        import windyfly.agent.loop as _loop
        _ch._tracker = None
        _loop._interaction_count = 0

        from windyfly.agent.loop import agent_respond

        # LLM always returns a tool call (infinite loop scenario)
        tool_response = {
            "content": "",
            "model": "gpt-4o-mini",
            "input_tokens": 50,
            "output_tokens": 10,
            "tool_calls": [{
                "id": "call_loop",
                "function": {"name": "flip_coin", "arguments": {}},
            }],
        }
        final_response = {
            "content": "Done!",
            "model": "gpt-4o-mini",
            "input_tokens": 50,
            "output_tokens": 10,
            "tool_calls": None,
        }
        # 3 tool rounds + final = 4 calls, but we provide enough for max rounds
        mock_llm.side_effect = [tool_response, tool_response, tool_response, final_response]

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        registry, _ = _make_registry()

        response = agent_respond(_make_config(), db, wq, "Keep flipping", "test-session", registry)
        # Should have stopped after 3 rounds (default)
        assert mock_llm.call_count <= 4
        assert isinstance(response, str)

        wq.stop()
        db.close()
