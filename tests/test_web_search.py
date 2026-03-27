"""Tests for the web search tool.

Tests web_search function and tool registration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import register_web_search_tool, web_search


class TestWebSearch:
    @patch("windyfly.tools.web_search.httpx.get")
    def test_returns_results_from_abstract(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "Python is a programming language.",
            "Heading": "Python",
            "AbstractURL": "https://python.org",
            "RelatedTopics": [],
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = web_search("python")
        assert result["query"] == "python"
        assert len(result["results"]) >= 1
        assert result["results"][0]["title"] == "Python"
        assert "programming" in result["results"][0]["snippet"]

    @patch("windyfly.tools.web_search.httpx.get")
    def test_returns_related_topics(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "",
            "Heading": "",
            "AbstractURL": "",
            "RelatedTopics": [
                {"Text": "Topic one is about X", "FirstURL": "http://example.com/1"},
                {"Text": "Topic two is about Y", "FirstURL": "http://example.com/2"},
            ],
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = web_search("test", limit=2)
        assert len(result["results"]) == 2
        assert "Topic one" in result["results"][0]["snippet"]

    @patch("windyfly.tools.web_search.httpx.get")
    def test_handles_http_error(self, mock_get):
        import httpx
        mock_get.side_effect = httpx.HTTPError("Connection failed")

        result = web_search("test")
        assert result["results"] == []
        assert "error" in result

    def test_register_adds_to_registry(self):
        registry = ToolRegistry()
        register_web_search_tool(registry)
        assert registry.tool_count == 1
        schemas = registry.get_schemas()
        assert schemas[0]["function"]["name"] == "web_search"
        assert "query" in schemas[0]["function"]["parameters"]["properties"]

    @patch("windyfly.tools.web_search.httpx.get")
    def test_respects_limit(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "Abstract": "Answer",
            "Heading": "H",
            "AbstractURL": "http://example.com",
            "RelatedTopics": [
                {"Text": f"Topic {i}", "FirstURL": f"http://example.com/{i}"}
                for i in range(10)
            ],
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = web_search("test", limit=3)
        assert len(result["results"]) <= 3
