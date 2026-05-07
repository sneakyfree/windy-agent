"""B.12 — opt-in routing of web_search/fetch_url through windy-search.

When WINDY_SEARCH_BASE_URL and WINDY_PASSPORT_EPT are both set, the
existing web_search() / fetch_url() helpers MUST route through the
centralized service. When either is missing, behavior is unchanged
from the pre-B.12 direct-Brave/DDG path.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestSearchRouting:
    def test_falls_back_to_brave_when_windy_search_not_configured(self, monkeypatch):
        """Default behavior (no opt-in) is unchanged from pre-B.12."""
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fake-key")

        with patch("windyfly.tools.web_search._brave_search") as brave, \
             patch("windyfly.tools.web_search.search_via_windy_search") as ws:
            brave.return_value = {"query": "x", "results": [], "provider": "brave"}
            web_search("x")
            assert brave.called
            assert not ws.called

    def test_falls_back_to_ddg_when_no_creds(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        monkeypatch.delenv("BRAVE_SEARCH_API_KEY", raising=False)

        with patch("windyfly.tools.web_search._ddg_search") as ddg, \
             patch("windyfly.tools.web_search.search_via_windy_search") as ws:
            ddg.return_value = {"query": "x", "results": [], "provider": "duckduckgo"}
            web_search("x")
            assert ddg.called
            assert not ws.called

    def test_routes_through_windy_search_when_configured(self, monkeypatch):
        """Both env vars set → calls windy-search, NOT Brave or DDG."""
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        # Setting BRAVE shouldn't change anything — windy-search wins.
        monkeypatch.setenv("BRAVE_SEARCH_API_KEY", "fake-brave")

        with patch("windyfly.tools.web_search.search_via_windy_search") as ws, \
             patch("windyfly.tools.web_search._brave_search") as brave, \
             patch("windyfly.tools.web_search._ddg_search") as ddg:
            ws.return_value = {"query": "x", "results": [{"title": "T", "snippet": "S", "url": "U"}],
                               "provider": "windy-search:brave"}
            result = web_search("x", limit=3)
            assert ws.called
            ws.assert_called_with("x", 3)
            assert not brave.called
            assert not ddg.called
            assert result["provider"] == "windy-search:brave"


class TestFetchRouting:
    def test_routes_through_windy_search_when_configured(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

        with patch("windyfly.tools.web_search.fetch_via_windy_search") as ws, \
             patch("windyfly.tools.web_search.httpx.get") as direct:
            ws.return_value = {"url": "U", "content": "hi", "total_length": 2}
            fetch_url("https://example.com/")
            assert ws.called
            assert not direct.called

    def test_falls_back_to_direct_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)

        with patch("windyfly.tools.web_search.fetch_via_windy_search") as ws, \
             patch("windyfly.tools.web_search.httpx.get") as direct:
            mock_resp = MagicMock()
            mock_resp.text = "<html><body>hi</body></html>"
            mock_resp.raise_for_status = MagicMock()
            direct.return_value = mock_resp
            fetch_url("https://example.com/")
            assert direct.called
            assert not ws.called


class TestWindySearchClient:
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
            # Verify Authorization header was set with Bearer + EPT
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
