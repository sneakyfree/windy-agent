"""Tests for the web search tool.

Tests web_search function and tool registration.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import (
    fetch_url, register_web_search_tool, web_search,
)


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
        assert registry.tool_count == 2  # web_search + fetch_url
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "web_search" in names
        assert "fetch_url" in names

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


class TestFetchUrlUserAgent:
    """Regression: Wikipedia (and many CDN-fronted sites) 403 the
    default httpx UA. fetch_url must send a real browser-like UA."""

    @patch("windyfly.tools.web_search.httpx.get")
    def test_fetch_url_sends_browser_user_agent(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "<html><body>hello</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        fetch_url("https://en.wikipedia.org/wiki/Software_testing")

        assert mock_get.called, "httpx.get must be called"
        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        ua = headers.get("User-Agent", "")
        # A real browser UA, not httpx's default
        assert "Mozilla/5.0" in ua, (
            f"fetch_url must send a browser-like User-Agent (got {ua!r})"
        )
        assert "python-httpx" not in ua.lower(), (
            f"fetch_url must NOT advertise as httpx (got {ua!r})"
        )
