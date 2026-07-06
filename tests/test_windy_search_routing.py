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

        with pytest.raises(RuntimeError, match="WEB_SEARCH_UNAVAILABLE"):
            web_search("anything")

    def test_web_search_raises_when_only_ept_set(self, monkeypatch):
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        with pytest.raises(RuntimeError, match="WEB_SEARCH_UNAVAILABLE"):
            web_search("anything")

    def test_web_search_raises_when_only_base_url_set(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
        with pytest.raises(RuntimeError, match="WEB_SEARCH_UNAVAILABLE"):
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
        with pytest.raises(RuntimeError, match="WEB_SEARCH_UNAVAILABLE"):
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

    def test_fetch_via_windy_search_sends_render_and_returns_rendered_via(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
        from windyfly.tools.windy_search_client import fetch_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "content": "hydrated", "total_chars": 8, "truncated": False,
                "rendered_via": "browserbase",
            }
            mock_resp.raise_for_status = MagicMock()
            post.return_value = mock_resp

            # default render should be "auto" and land in the request body
            result = fetch_via_windy_search("https://spa.test/")
            body = post.call_args.kwargs["json"]
            assert body["render"] == "auto"
            assert result["rendered_via"] == "browserbase"

            # explicit render="on" is forwarded
            fetch_via_windy_search("https://spa.test/", render="on")
            assert post.call_args.kwargs["json"]["render"] == "on"


class TestBudgetNotices:
    """Budget-notice wiring (2026-07-06): windy-search's per-passport
    budget signals reach the agent's tool results so the fly can tell
    its user in its own voice. Server stays the meter (edge-triggered
    80% warning + 429 cap); the fly is only the messenger."""

    def _env(self, monkeypatch):
        monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
        monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")

    def _budget_429(self):
        """A windy-search budget-exhausted 429 (X-Cost-* headers present)."""
        import httpx
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {"X-Cost-Cap-USD": "5.00", "Retry-After": "86400"}
        resp.text = "Monthly budget exhausted"
        err = httpx.HTTPStatusError("429", request=MagicMock(), response=resp)
        mock = MagicMock()
        mock.raise_for_status.side_effect = err
        return mock

    def _rate_limit_429(self):
        """A windy-search rate-limit 429 (X-RateLimit-*, no X-Cost-*)."""
        import httpx
        resp = MagicMock()
        resp.status_code = 429
        resp.headers = {"X-RateLimit-Limit": "50", "Retry-After": "60"}
        resp.text = "Rate limit exceeded"
        err = httpx.HTTPStatusError("429", request=MagicMock(), response=resp)
        mock = MagicMock()
        mock.raise_for_status.side_effect = err
        return mock

    def test_search_threads_warning_notice(self, monkeypatch):
        self._env(monkeypatch)
        from windyfly.tools.windy_search_client import search_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "query": "q", "results": [], "backend": "brave",
                "budget_warning": True, "budget_used_usd": 4.0005,
                "budget_cap_usd": 5.0,
            }
            mock_resp.raise_for_status = MagicMock()
            post.return_value = mock_resp

            result = search_via_windy_search("q")
            assert result["budget_warning"] is True
            assert "80%" in result["notice_to_user"]
            assert result["budget_cap_usd"] == 5.0

    def test_search_no_warning_means_no_notice(self, monkeypatch):
        self._env(monkeypatch)
        from windyfly.tools.windy_search_client import search_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "query": "q", "results": [], "backend": "brave",
                "budget_warning": False, "budget_used_usd": 0.01,
                "budget_cap_usd": 5.0,
            }
            mock_resp.raise_for_status = MagicMock()
            post.return_value = mock_resp

            result = search_via_windy_search("q")
            assert "notice_to_user" not in result
            assert "budget_warning" not in result

    def test_fetch_threads_warning_notice(self, monkeypatch):
        self._env(monkeypatch)
        from windyfly.tools.windy_search_client import fetch_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            mock_resp = MagicMock()
            mock_resp.json.return_value = {
                "content": "body", "total_chars": 4, "truncated": False,
                "budget_warning": True, "budget_used_usd": 4.05,
                "budget_cap_usd": 5.0,
            }
            mock_resp.raise_for_status = MagicMock()
            post.return_value = mock_resp

            result = fetch_via_windy_search("https://x.test/")
            assert result["budget_warning"] is True
            assert "80%" in result["notice_to_user"]

    def test_search_budget_429_returns_friendly_exhausted(self, monkeypatch):
        self._env(monkeypatch)
        from windyfly.tools.windy_search_client import search_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            post.return_value = self._budget_429()
            result = search_via_windy_search("q")
            assert result["budget_exhausted"] is True
            assert "do not retry" in result["notice_to_user"]
            assert "built-in web search" in result["notice_to_user"]
            assert result["error"] == "HTTP 429"

    def test_rate_limit_429_is_not_budget_exhausted(self, monkeypatch):
        """The per-minute rate-limit 429 must NOT read as budget-exhausted —
        it clears in 60s and carries no X-Cost-* headers."""
        self._env(monkeypatch)
        from windyfly.tools.windy_search_client import search_via_windy_search

        with patch("windyfly.tools.windy_search_client.httpx.post") as post:
            post.return_value = self._rate_limit_429()
            result = search_via_windy_search("q")
            assert "budget_exhausted" not in result
            assert "notice_to_user" not in result
            assert result["error"] == "HTTP 429"

    def test_fetch_url_budget_429_skips_direct_rescue(self, monkeypatch):
        """Budget-exhausted is intentional policy, not a windy-search
        failure — the direct-httpx circuit breaker must NOT bypass it."""
        self._env(monkeypatch)
        from windyfly.tools.web_search import fetch_url

        with patch("windyfly.tools.windy_search_client.httpx.post") as post, \
             patch("windyfly.tools.web_search.httpx.get") as direct_get:
            post.return_value = self._budget_429()
            result = fetch_url("https://x.test/")
            direct_get.assert_not_called()
            assert result["budget_exhausted"] is True
            assert "notice_to_user" in result
