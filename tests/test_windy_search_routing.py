"""Search V1 hard gate (2026-05-17) — windy-agent must route ALL
web_search/fetch_url through windy-search (api.windysearch.com).

Pre-hard-gate behavior (Brave-direct → DuckDuckGo fallback) was deleted
because it was duplicate infrastructure: windy-search has its own
Brave→Google provider failover internally, and the consumer-side
fallback bypassed Search V1's cost-cap, per-EII rate-limit, and
integrity-event audit machinery.

If WINDY_SEARCH_BASE_URL or WINDY_PASSPORT_EPT is missing,
web_search/fetch_url raise RuntimeError — fail loud at first call.

The fetch_url 5xx rescue path (direct httpx when windy-search itself
returns 5xx / timeout / connect error) is INTENTIONALLY KEPT — it's a
circuit breaker that keeps the agent functional when our own service
hiccups, not a competing search provider.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.web_search import fetch_url, web_search
from windyfly.tools.windy_search_client import is_routed_through_search


class TestRoutingDecision:
    def test_neither_env_set_means_no_routing(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        assert is_routed_through_search() is False

    def test_only_base_url_set_means_no_routing(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        assert is_routed_through_search() is False

    def test_only_ept_set_means_no_routing(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        assert is_routed_through_search() is False

    def test_both_set_routes_through(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        assert is_routed_through_search() is True


class TestSearchHardGate:
    def test_web_search_raises_when_not_configured(self, monkeypatch):
        """Hard gate: missing env → RuntimeError with actionable message."""
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        # Setting a Brave key MUST NOT enable a fallback — hard gate.
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fake-brave-key")

        with pytest.raises(RuntimeError, match="Search V1 hard gate"):
            web_search("anything")

    def test_web_search_raises_when_only_ept_set(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        with pytest.raises(RuntimeError, match="Search V1 hard gate"):
            web_search("anything")

    def test_web_search_raises_when_only_base_url_set(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        with pytest.raises(RuntimeError, match="Search V1 hard gate"):
            web_search("anything")

    def test_web_search_routes_through_when_configured(self, monkeypatch):
        """Both env vars set → calls windy-search, returns its result verbatim."""
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        with patch("windyfly.tools.web_search.search_via_windy_search") as ws:
            ws.return_value = {
                "query": "x",
                "results": [{"title": "T", "snippet": "S", "url": "U"}],
                "provider": "windy-search:brave",
            }
            result = web_search("x", limit=3)
            ws.assert_called_once_with("x", 3)
            assert result["provider"] == "windy-search:brave"


class TestFetchHardGate:
    def test_fetch_url_raises_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        with pytest.raises(RuntimeError, match="Search V1 hard gate"):
            fetch_url("https://example.com/")

    def test_fetch_url_routes_through_when_configured(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        with patch("windyfly.tools.web_search.fetch_via_windy_search") as ws, \
             patch("windyfly.tools.web_search._direct_fetch_url") as direct:
            ws.return_value = {"url": "U", "content": "hi", "total_length": 2}
            fetch_url("https://example.com/")
            assert ws.called
            assert not direct.called

    def test_fetch_url_rescues_when_windy_search_5xx(self, monkeypatch):
        """5xx from windy-search → direct httpx rescue (circuit breaker)."""
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        with patch("windyfly.tools.web_search.fetch_via_windy_search") as ws, \
             patch("windyfly.tools.web_search._direct_fetch_url") as direct:
            ws.return_value = {"url": "U", "content": "", "error": "HTTP 502"}
            direct.return_value = {"url": "U", "content": "rescued", "total_length": 7}
            result = fetch_url("https://example.com/")
            assert direct.called
            assert result["provider"] == "direct-fallback"
            assert result["windy_search_error"] == "HTTP 502"

    def test_fetch_url_no_rescue_on_pass_through_4xx(self, monkeypatch):
        """4xx from windy-search means the TARGET refused — direct would
        get the same answer. No rescue."""
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        with patch("windyfly.tools.web_search.fetch_via_windy_search") as ws, \
             patch("windyfly.tools.web_search._direct_fetch_url") as direct:
            ws.return_value = {"url": "U", "content": "", "error": "HTTP 403"}
            result = fetch_url("https://example.com/")
            assert not direct.called
            assert result["error"] == "HTTP 403"


class TestWindySearchClient:
    """Tests for the thin client itself (windy_search_client module).
    Kept verbatim from the pre-hard-gate test suite — these test the
    transport layer, not the routing decision."""

    def test_search_via_windy_search_happy_path(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        from windyfly.tools.windy_search_client import search_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "query": "test",
                "backend": "ddg",
                "results": [{"url": "U", "title": "T", "snippet": "S"}],
                "cache_hit": False,
            }
            mock_resp.raise_for_status = MagicMock()
            post.return_value = mock_resp

            result = search_via_windy_search("test", limit=5)
            assert result["query"] == "test"
            assert result["provider"] == "windy-search:ddg"
            assert len(result["results"]) == 1
            kwargs = post.call_args.kwargs
            assert kwargs["headers"]["Authorization"] == "Bearer ey...test..."

    def test_search_via_windy_search_returns_error_on_4xx(self, monkeypatch):
        import httpx as _httpx

        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        from windyfly.tools.windy_search_client import search_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.text = '{"detail":"rate limit"}'
            err = _httpx.HTTPStatusError("rate limit", request=MagicMock(), response=mock_resp)
            mock_resp.raise_for_status = MagicMock(side_effect=err)
            post.return_value = mock_resp

            result = search_via_windy_search("test")
            assert result["provider"] == "windy-search-error"
            assert "429" in result["error"]
            assert result["results"] == []

    def test_fetch_via_windy_search_includes_pagination(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        from windyfly.tools.windy_search_client import fetch_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "url": "U", "final_url": "U", "status_code": 200,
                "content_type": "text/plain",
                "content": "ABC",
                "total_chars": 100,
                "offset": 50,
                "max_chars": 3,
                "truncated": True,
                "cache_hit": True,
            }
            mock_resp.raise_for_status = MagicMock()
            post.return_value = mock_resp

            result = fetch_via_windy_search("https://x.test/", max_chars=3, offset=50)
            assert result["content"] == "ABC"
            assert result["total_length"] == 100
            assert result["truncated"] is True
            assert result["next_offset"] == 53
            assert result["cache_hit"] is True
            assert result["provider"] == "windy-search"
