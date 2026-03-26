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
        mock_response.json.return_value = {"recordings": []}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_recordings()
        assert "recordings" in result

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
