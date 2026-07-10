"""Tests for the web search tool module.

Post hard-gate (Search V1, 2026-05-17):
- web_search / fetch_url routing tests live in test_windy_search_routing.py
- Here we test the surviving non-routing pieces:
  - Tool registration (register_web_search_tool)
  - _direct_fetch_url: chunking, UA, error handling
    (still exists as fetch_url's 5xx rescue path)
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import (
    _direct_fetch_url,
    register_web_search_tool,
)


class TestRegistration:
    def test_register_adds_to_registry(self):
        registry = ToolRegistry()
        register_web_search_tool(registry)
        assert registry.tool_count == 2  # web_search + fetch_url
        names = {s["function"]["name"] for s in registry.get_schemas()}
        assert "web_search" in names
        assert "fetch_url" in names


class TestDirectFetchUserAgent:
    """Regression: Wikipedia (and many CDN-fronted sites) 403 the
    default httpx UA. The direct rescue path must send a real
    browser-like UA so it actually has a chance of succeeding where
    windy-search's fetcher failed."""

    @patch("windyfly.tools.web_search._host_ips", return_value=["93.184.216.34"])
    @patch("windyfly.tools.web_search.httpx.get")
    def test_direct_fetch_sends_browser_user_agent(self, mock_get, mock_ips):
        mock_response = MagicMock()
        mock_response.text = "<html><body>hello</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        _direct_fetch_url("https://en.wikipedia.org/wiki/Software_testing")

        assert mock_get.called, "httpx.get must be called"
        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        ua = headers.get("User-Agent", "")
        assert "Mozilla/5.0" in ua, (
            f"_direct_fetch_url must send a browser-like User-Agent (got {ua!r})"
        )
        assert "python-httpx" not in ua.lower(), (
            f"_direct_fetch_url must NOT advertise as httpx (got {ua!r})"
        )


class TestDirectFetchChunking:
    """Round-3 finding: 5KB cap meant the bot couldn't read past the
    opening of any real article. Default raised to 20000 + offset
    parameter for chunked reading on long pages."""

    @patch("windyfly.tools.web_search._host_ips", return_value=["93.184.216.34"])
    @patch("windyfly.tools.web_search.httpx.get")
    def test_default_max_chars_is_20000(self, mock_get, mock_ips):
        long_text = "x" * 50_000
        mock_response = MagicMock()
        mock_response.text = f"<html><body>{long_text}</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        out = _direct_fetch_url("https://example.com/long")
        assert out["returned_chars"] == 20_000
        assert out["total_length"] == 50_000
        assert out["truncated"] is True
        assert out["next_offset"] == 20_000

    @patch("windyfly.tools.web_search._host_ips", return_value=["93.184.216.34"])
    @patch("windyfly.tools.web_search.httpx.get")
    def test_offset_returns_later_slice(self, mock_get, mock_ips):
        long_text = "ABCDEFGHIJ" * 5_000  # 50_000 chars
        mock_response = MagicMock()
        mock_response.text = f"<html><body>{long_text}</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        out = _direct_fetch_url("https://example.com/long", max_chars=100, offset=200)
        assert out["offset"] == 200
        assert out["returned_chars"] == 100
        # Slice should start at char 200 of the cleaned body
        assert out["content"][:10] == "ABCDEFGHIJ"
        assert out["truncated"] is True
        assert out["next_offset"] == 300

    @patch("windyfly.tools.web_search._host_ips", return_value=["93.184.216.34"])
    @patch("windyfly.tools.web_search.httpx.get")
    def test_offset_past_end_returns_empty(self, mock_get, mock_ips):
        mock_response = MagicMock()
        mock_response.text = "<html><body>short</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        out = _direct_fetch_url("https://example.com/", max_chars=100, offset=999)
        assert out["content"] == ""
        assert out["truncated"] is False
        assert out["next_offset"] is None


class TestDirectFetchSsrfGuard:
    """[I3] fetch_url's direct-httpx fallback must not be usable to reach cloud
    metadata / internal services (SSRF) — including via a redirect."""

    @patch("windyfly.tools.web_search.httpx.get")
    def test_metadata_ip_blocked(self, mock_get):
        out = _direct_fetch_url("http://169.254.169.254/latest/meta-data/")
        assert out.get("content") == ""
        assert "blocked" in out.get("error", "")
        assert not mock_get.called  # request never made

    @patch("windyfly.tools.web_search.httpx.get")
    def test_localhost_blocked(self, mock_get):
        out = _direct_fetch_url("http://127.0.0.1:8080/admin")
        assert "blocked" in out.get("error", "")
        assert not mock_get.called

    @patch("windyfly.tools.web_search._host_ips", return_value=["10.0.0.5"])
    @patch("windyfly.tools.web_search.httpx.get")
    def test_hostname_resolving_to_private_blocked(self, mock_get, mock_ips):
        out = _direct_fetch_url("https://evil.example.com/")
        assert "blocked" in out.get("error", "")
        assert not mock_get.called

    @patch("windyfly.tools.web_search._host_ips", return_value=["93.184.216.34"])
    @patch("windyfly.tools.web_search.httpx.get")
    def test_redirect_to_internal_blocked(self, mock_get, mock_ips):
        redirect = MagicMock()
        redirect.status_code = 302
        redirect.headers = {"location": "http://169.254.169.254/"}
        mock_get.return_value = redirect
        out = _direct_fetch_url("https://public.example.com/")
        assert "blocked" in out.get("error", "")
        assert mock_get.call_count == 1  # only the first (public) hop fired

    @patch("windyfly.tools.web_search.httpx.get")
    def test_short_page_not_truncated(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "<html><body>short</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        out = _direct_fetch_url("https://example.com/")
        assert out["truncated"] is False
        assert out["next_offset"] is None
        assert out["total_length"] == out["returned_chars"]

    @patch("windyfly.tools.web_search.httpx.get")
    def test_negative_offset_clamped_to_zero(self, mock_get):
        mock_response = MagicMock()
        mock_response.text = "<html><body>hello world</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        out = _direct_fetch_url("https://example.com/", offset=-50)
        assert out["offset"] == 0
        assert "hello" in out["content"]


class TestDirectFetchErrorHandling:
    @patch("windyfly.tools.web_search.httpx.get")
    def test_returns_error_field_on_exception(self, mock_get):
        mock_get.side_effect = Exception("connection refused")
        out = _direct_fetch_url("https://nonexistent.example")
        assert "error" in out
        assert out["content"] == ""
