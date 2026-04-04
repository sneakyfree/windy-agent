"""Integration tests — verify tools register, schemas are valid, and tools execute."""

import json
from unittest.mock import patch

from windyfly.memory.database import Database
from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import register_web_search_tool
from windyfly.tools.reminders import register_reminder_tools
from windyfly.tools.todos import register_todo_tools
from windyfly.tools.weather import register_weather_tool
from windyfly.tools.news import register_news_tool
from windyfly.tools.calendar import register_calendar_tools
from windyfly.tools.utilities import register_utility_tools


def _full_registry():
    """Create a registry with all tools registered."""
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


class TestToolCount:
    def test_minimum_tool_count(self):
        """Agent should have at least 17 LLM-callable tools."""
        registry, db = _full_registry()
        assert registry.tool_count >= 17, f"Only {registry.tool_count} tools registered"
        db.close()

    def test_all_expected_tools_registered(self):
        """Every expected tool name should be in the registry."""
        registry, db = _full_registry()
        expected = {
            "web_search", "fetch_url",
            "set_reminder", "list_reminders", "cancel_reminder",
            "add_todo", "list_todos", "complete_todo",
            "get_weather", "get_news",
            "get_today_events", "get_upcoming_events", "create_event",
            "set_timer", "convert_units", "flip_coin", "roll_dice", "calculate",
        }
        registered = {s["function"]["name"] for s in registry.get_schemas()}
        missing = expected - registered
        assert not missing, f"Missing tools: {missing}"
        db.close()


class TestToolSchemas:
    def test_schemas_are_valid_openai_format(self):
        """All schemas should follow OpenAI function-calling format."""
        registry, db = _full_registry()
        for schema in registry.get_schemas():
            assert schema["type"] == "function"
            assert "function" in schema
            fn = schema["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            assert fn["parameters"]["type"] == "object"
            assert "properties" in fn["parameters"]
        db.close()

    def test_all_descriptions_are_substantive(self):
        """Tool descriptions should be at least 20 chars (helpful for the LLM)."""
        registry, db = _full_registry()
        for schema in registry.get_schemas():
            desc = schema["function"]["description"]
            assert len(desc) >= 20, f"{schema['function']['name']} has short description: {desc!r}"
        db.close()

    def test_required_fields_present(self):
        """Tools with 'required' should list valid property names."""
        registry, db = _full_registry()
        for schema in registry.get_schemas():
            fn = schema["function"]
            required = fn["parameters"].get("required", [])
            props = fn["parameters"]["properties"]
            for r in required:
                assert r in props, f"{fn['name']}: required param '{r}' not in properties"
        db.close()


class TestToolExecution:
    def test_flip_coin_executes(self):
        registry, db = _full_registry()
        result = registry.execute("flip_coin", {})
        data = json.loads(result)
        assert data["result"] in ("Heads", "Tails")
        db.close()

    def test_roll_dice_executes(self):
        registry, db = _full_registry()
        result = registry.execute("roll_dice", {"sides": 6, "count": 2})
        data = json.loads(result)
        assert len(data["rolls"]) == 2
        assert all(1 <= r <= 6 for r in data["rolls"])
        db.close()

    def test_calculate_executes(self):
        registry, db = _full_registry()
        result = registry.execute("calculate", {"expression": "2 + 3 * 4"})
        data = json.loads(result)
        assert data["result"] == 14
        db.close()

    def test_convert_units_executes(self):
        registry, db = _full_registry()
        result = registry.execute("convert_units", {"value": 10, "from_unit": "km", "to_unit": "miles"})
        data = json.loads(result)
        assert 6.2 < data["result"] < 6.3
        db.close()

    def test_set_timer_executes(self):
        registry, db = _full_registry()
        result = registry.execute("set_timer", {"duration": "5 minutes"})
        data = json.loads(result)
        assert data["success"] is True
        assert data["seconds"] == 300
        db.close()

    def test_add_todo_executes(self):
        registry, db = _full_registry()
        result = registry.execute("add_todo", {"title": "Buy milk"})
        data = json.loads(result)
        assert data["success"] is True
        db.close()

    def test_list_todos_executes(self):
        registry, db = _full_registry()
        registry.execute("add_todo", {"title": "Test item"})
        result = registry.execute("list_todos", {})
        data = json.loads(result)
        assert len(data["todos"]) >= 1
        db.close()

    def test_set_reminder_executes(self):
        registry, db = _full_registry()
        result = registry.execute("set_reminder", {"message": "test", "time": "in 1 hour"})
        data = json.loads(result)
        assert data["success"] is True
        db.close()

    def test_list_reminders_executes(self):
        registry, db = _full_registry()
        result = registry.execute("list_reminders", {})
        data = json.loads(result)
        assert "reminders" in data
        db.close()

    @patch("windyfly.tools.weather.httpx.get")
    def test_get_weather_executes(self, mock_get):
        from unittest.mock import MagicMock
        geo_resp = MagicMock()
        geo_resp.status_code = 200
        geo_resp.json.return_value = {"results": [{"name": "Test", "latitude": 0, "longitude": 0, "country": "US"}]}
        weather_resp = MagicMock()
        weather_resp.status_code = 200
        weather_resp.json.return_value = {
            "current_weather": {"temperature": 70, "weathercode": 0, "windspeed": 5},
            "daily": {"temperature_2m_max": [75], "temperature_2m_min": [55]},
        }
        mock_get.side_effect = [geo_resp, weather_resp]

        registry, db = _full_registry()
        result = registry.execute("get_weather", {"location": "Test City"})
        data = json.loads(result)
        assert "temperature_f" in data
        db.close()

    def test_unknown_tool_raises(self):
        registry, db = _full_registry()
        try:
            registry.execute("nonexistent_tool", {})
            assert False, "Should have raised"
        except KeyError:
            pass
        db.close()

    def test_malformed_json_handled(self):
        registry, db = _full_registry()
        result = registry.execute("flip_coin", "not json {{{")
        data = json.loads(result)
        assert "error" in data
        db.close()


class TestUtilities:
    def test_temperature_conversion(self):
        from windyfly.tools.utilities import convert_units
        result = convert_units(100, "c", "f")
        assert result["result"] == 212.0

    def test_temperature_f_to_c(self):
        from windyfly.tools.utilities import convert_units
        result = convert_units(32, "f", "c")
        assert result["result"] == 0.0

    def test_distance_conversion(self):
        from windyfly.tools.utilities import convert_units
        result = convert_units(1, "miles", "km")
        assert 1.60 < result["result"] < 1.61

    def test_unit_aliases(self):
        from windyfly.tools.utilities import convert_units
        result = convert_units(1, "kilometer", "mile")
        assert result["result"] > 0

    def test_unknown_conversion(self):
        from windyfly.tools.utilities import convert_units
        result = convert_units(1, "parsec", "lightyear")
        assert "error" in result

    def test_timer_seconds(self):
        from windyfly.tools.utilities import set_timer
        result = set_timer("30 seconds")
        assert result["seconds"] == 30

    def test_timer_hours(self):
        from windyfly.tools.utilities import set_timer
        result = set_timer("2 hours")
        assert result["seconds"] == 7200

    def test_timer_bad_input(self):
        from windyfly.tools.utilities import set_timer
        result = set_timer("someday")
        assert result["success"] is False

    def test_calculate_sqrt(self):
        from windyfly.tools.utilities import calculate
        result = calculate("sqrt(144)")
        assert result["result"] == 12.0

    def test_calculate_bad_expression(self):
        from windyfly.tools.utilities import calculate
        result = calculate("import os")
        assert "error" in result

    def test_random_number(self):
        from windyfly.tools.utilities import random_number
        result = random_number(1, 10)
        assert 1 <= result["result"] <= 10
