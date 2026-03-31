"""Tests for the tool registry and Windy API tools.

Tests tool registration, schema generation, dispatch, and
Windy API tool implementations with mocked httpx.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.windy_api import (
    get_clone_status,
    get_recordings,
    get_translation_history,
    register_windy_tools,
    translate_text,
)


# === Tool Registry Tests ===


class TestToolRegistry:
    def test_register_and_count(self):
        registry = ToolRegistry()
        registry.register(
            "test_tool",
            "A test tool",
            {"type": "object", "properties": {}},
            lambda: "result",
        )
        assert registry.tool_count == 1

    def test_get_schemas(self):
        registry = ToolRegistry()
        registry.register(
            "greet",
            "Says hello",
            {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name to greet"},
                },
                "required": ["name"],
            },
            lambda name: f"Hello, {name}!",
        )
        schemas = registry.get_schemas()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "greet"

    def test_execute_with_dict(self):
        registry = ToolRegistry()
        registry.register(
            "add",
            "Add two numbers",
            {"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}},
            lambda a, b: str(a + b),
        )
        result = registry.execute("add", {"a": 2, "b": 3})
        assert result == "5"

    def test_execute_with_json_string(self):
        registry = ToolRegistry()
        registry.register(
            "echo",
            "Echo input",
            {"type": "object", "properties": {"msg": {"type": "string"}}},
            lambda msg: msg,
        )
        result = registry.execute("echo", '{"msg": "hello"}')
        assert result == "hello"

    def test_execute_unknown_tool(self):
        registry = ToolRegistry()
        try:
            registry.execute("nonexistent", {})
            assert False, "Should have raised"
        except KeyError:
            pass

    def test_execute_error_returns_error_json(self):
        def fail():
            raise ValueError("intentional error")

        registry = ToolRegistry()
        registry.register(
            "fail_tool",
            "Fails intentionally",
            {"type": "object", "properties": {}},
            fail,
        )
        result = registry.execute("fail_tool", {})
        assert "error" in result

    def test_execute_dict_result_serialized(self):
        registry = ToolRegistry()
        registry.register(
            "info",
            "Returns info",
            {"type": "object", "properties": {}},
            lambda: {"status": "ok", "count": 42},
        )
        result = registry.execute("info", {})
        assert '"status": "ok"' in result
        assert '"count": 42' in result


# === Windy API Tool Registration ===


class TestWindyToolRegistration:
    def test_registers_four_tools(self):
        registry = ToolRegistry()
        register_windy_tools(registry)
        assert registry.tool_count == 4

    def test_schema_format(self):
        registry = ToolRegistry()
        register_windy_tools(registry)
        schemas = registry.get_schemas()
        tool_names = {s["function"]["name"] for s in schemas}
        assert "get_translation_history" in tool_names
        assert "get_recordings" in tool_names
        assert "get_clone_status" in tool_names
        assert "translate_text" in tool_names

    def test_translate_schema_has_required_params(self):
        registry = ToolRegistry()
        register_windy_tools(registry)
        schemas = registry.get_schemas()
        translate_schema = next(
            s for s in schemas if s["function"]["name"] == "translate_text"
        )
        params = translate_schema["function"]["parameters"]
        assert "text" in params["required"]
        assert "source_lang" in params["required"]
        assert "target_lang" in params["required"]

    def test_recordings_schema_has_query_param(self):
        registry = ToolRegistry()
        register_windy_tools(registry)
        schemas = registry.get_schemas()
        rec_schema = next(
            s for s in schemas if s["function"]["name"] == "get_recordings"
        )
        params = rec_schema["function"]["parameters"]
        assert "query" in params["properties"]


# === Windy API Tool Implementations (mocked) ===


class TestWindyApiTools:
    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_translation_history(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "translations": [{"text": "Hello", "target": "es"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_translation_history(limit=5)
        assert "translations" in result
        mock_get.assert_called_once()

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_recordings(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"recordings": [{"id": "r1"}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_recordings()
        assert "recordings" in result

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_recordings_empty(self, mock_get):
        """Empty recordings returns friendly message about local storage."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"recordings": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_recordings()
        assert result["recordings"] == []
        assert "local" in result.get("message", "").lower()

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_recordings_with_query(self, mock_get):
        """Recordings search passes query parameter."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"recordings": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        get_recordings(query="meeting")
        call_kwargs = mock_get.call_args
        assert "q" in call_kwargs.kwargs.get("params", {}) or "q" in (call_kwargs[1].get("params", {}))

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_clone_status(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ready": False, "phoneme_coverage": 45.2, "hours": 1.5
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_clone_status()
        assert "ready" in result

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_clone_status_not_available(self, mock_get):
        """Clone status returns friendly message when service unavailable."""
        import httpx
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        result = get_clone_status()
        assert result["available"] is False
        assert "not available" in result.get("message", "")

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_get_clone_status_404(self, mock_get):
        """Clone status returns friendly message when endpoint doesn't exist."""
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 404
        response = httpx.Response(404, request=httpx.Request("GET", "http://test"))
        mock_get.side_effect = httpx.HTTPStatusError("Not found", request=response.request, response=response)

        result = get_clone_status()
        assert result["available"] is False

    @patch("windyfly.tools.windy_api.httpx.post")
    def test_translate_text(self, mock_post):
        mock_response = MagicMock()
        mock_response.json.return_value = {"translated_text": "Hola"}
        mock_response.raise_for_status = MagicMock()
        mock_post.return_value = mock_response

        result = translate_text("Hello", "en", "es")
        assert "translated_text" in result

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_handles_http_error(self, mock_get):
        import httpx
        mock_get.side_effect = httpx.ConnectError("Connection refused")

        result = get_translation_history()
        assert "error" in result
        assert "not available" in result["error"].lower()

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_register_adds_to_registry(self, mock_get):
        """Verify tools are callable through the registry."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"translations": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        registry = ToolRegistry()
        register_windy_tools(registry)
        result = registry.execute("get_translation_history", {"limit": 5})
        assert "translations" in result

    @patch("windyfly.tools.windy_api.httpx.get")
    def test_respects_limit(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"translations": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        get_translation_history(limit=3)
        call_kwargs = mock_get.call_args
        params = call_kwargs.kwargs.get("params", call_kwargs[1].get("params", {}))
        assert params["limit"] == 3
