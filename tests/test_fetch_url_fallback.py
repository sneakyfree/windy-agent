"""fetch_url failover regression suite.

Surfaced 2026-05-10 by a Telegram screenshot. The bot ran 5
successful web_search calls (200 OK), then 2 fetch_url calls that
returned HTTP 502 from windy-search /web/fetch with detail
'upstream HTTP 403' (target sites anti-bot-blocking windy-search's
fetcher). The bot reported "network is down" and gave up despite
direct httpx with a browser User-Agent likely succeeding (different
IP and UA from windy-search).

Pin the contract:

  - When windy-search /web/fetch fails with 502/503/504/timeout/
    connect error, fetch_url must fall back to direct httpx.
  - When windy-search returns successfully, no fallback (avoid
    double-fetching every URL).
  - When windy-search passes through a 4xx (target site itself
    refused), no fallback (direct would just get the same answer).
  - Result must carry ``provider="direct-fallback"`` so logs and
    debugging surfaces show the rescue happened.
  - When BOTH paths fail, the result still carries an error field
    rather than silently empty.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from windyfly.tools.web_search import (
    _is_windy_search_failure,
    fetch_url,
)


# ─── Failure-classifier unit tests ────────────────────────────────


class TestIsWindySearchFailure:

    def test_502_treated_as_failure(self):
        assert _is_windy_search_failure("HTTP 502") is True

    def test_503_504_treated_as_failure(self):
        assert _is_windy_search_failure("HTTP 503") is True
        assert _is_windy_search_failure("HTTP 504") is True

    def test_timeout_treated_as_failure(self):
        assert _is_windy_search_failure("ConnectTimeout: connection timed out") is True
        assert _is_windy_search_failure("ReadTimeout") is True
        assert _is_windy_search_failure("PoolTimeout") is True

    def test_connect_error_treated_as_failure(self):
        assert _is_windy_search_failure("ConnectError: connection refused") is True

    def test_403_NOT_failure(self):
        """4xx is windy-search successfully passing through a target-
        side refusal — direct httpx would get the same answer."""
        assert _is_windy_search_failure("HTTP 403") is False
        assert _is_windy_search_failure("HTTP 404") is False
        assert _is_windy_search_failure("HTTP 401") is False

    def test_5xx_passthrough_NOT_failure(self):
        """501 / 505 etc. aren't in the list — those are 'rare server
        bug' cases, not 'windy-search itself broken'. Direct fallback
        unlikely to help, no fallback."""
        # 502/503/504 are what we care about (the production case).
        # Other 5xx should pass through without fallback to keep the
        # blast radius narrow.
        assert _is_windy_search_failure("HTTP 500") is False
        assert _is_windy_search_failure("HTTP 501") is False

    def test_empty_or_none_returns_false(self):
        assert _is_windy_search_failure(None) is False
        assert _is_windy_search_failure("") is False


# ─── End-to-end fetch_url failover ────────────────────────────────


@pytest.fixture
def routed_env(monkeypatch):
    monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://api.windysearch.com")
    monkeypatch.setenv("WINDY_PASSPORT_EPT", "ey...test...")
    yield


class TestFetchUrlFallback:

    def test_windy_search_502_triggers_direct_fallback(self, routed_env):
        """The headline scenario: windy-search returns 502, direct
        httpx succeeds → caller gets content from the fallback."""
        with patch(
            "windyfly.tools.web_search.fetch_via_windy_search",
            return_value={"url": "u", "content": "", "error": "HTTP 502"},
        ), patch("windyfly.tools.web_search.httpx.get") as direct:
            mock_resp = MagicMock()
            mock_resp.text = (
                "<html><body><p>Brian Hill is a loan officer.</p>"
                "</body></html>"
            )
            mock_resp.raise_for_status = MagicMock()
            direct.return_value = mock_resp

            out = fetch_url("https://example.com/brian")

        assert out["content"], "fallback returned empty"
        assert "Brian Hill is a loan officer" in out["content"]
        assert out["provider"] == "direct-fallback"
        assert out["windy_search_error"] == "HTTP 502"

    def test_windy_search_503_504_also_trigger_fallback(self, routed_env):
        for status in ("HTTP 503", "HTTP 504"):
            with patch(
                "windyfly.tools.web_search.fetch_via_windy_search",
                return_value={"url": "u", "content": "", "error": status},
            ), patch("windyfly.tools.web_search.httpx.get") as direct:
                mock_resp = MagicMock()
                mock_resp.text = "<html><body>ok</body></html>"
                mock_resp.raise_for_status = MagicMock()
                direct.return_value = mock_resp

                out = fetch_url("https://example.com/x")
            assert out["provider"] == "direct-fallback", \
                f"{status} should trigger fallback"

    def test_windy_search_timeout_triggers_fallback(self, routed_env):
        with patch(
            "windyfly.tools.web_search.fetch_via_windy_search",
            return_value={"url": "u", "content": "",
                          "error": "ConnectTimeout: timed out"},
        ), patch("windyfly.tools.web_search.httpx.get") as direct:
            mock_resp = MagicMock()
            mock_resp.text = "<html><body>via direct</body></html>"
            mock_resp.raise_for_status = MagicMock()
            direct.return_value = mock_resp

            out = fetch_url("https://example.com/x")
        assert "via direct" in out["content"]
        assert out["provider"] == "direct-fallback"

    def test_windy_search_403_does_NOT_trigger_fallback(self, routed_env):
        """Pass-through 4xx means the target itself refused. Direct
        would get the same answer — no point falling back."""
        with patch(
            "windyfly.tools.web_search.fetch_via_windy_search",
            return_value={"url": "u", "content": "", "error": "HTTP 403"},
        ), patch("windyfly.tools.web_search.httpx.get") as direct:
            out = fetch_url("https://example.com/x")
        assert direct.called is False, \
            "403 (target refusal) must NOT trigger direct fallback"
        # The 4xx pass-through result is what reaches the caller.
        assert out.get("error") == "HTTP 403"

    def test_windy_search_success_no_fallback(self, routed_env):
        """When windy-search returns content, we DO NOT also call
        direct (that'd double the work and waste time)."""
        with patch(
            "windyfly.tools.web_search.fetch_via_windy_search",
            return_value={"url": "u", "content": "real content from ws",
                          "total_length": 22, "provider": "windy-search"},
        ), patch("windyfly.tools.web_search.httpx.get") as direct:
            out = fetch_url("https://example.com/x")
        assert direct.called is False
        assert out["content"] == "real content from ws"
        assert out["provider"] == "windy-search"

    def test_both_paths_fail_returns_error_not_silent(self, routed_env):
        """Worst case: windy-search 502 AND direct httpx also fails.
        We should return SOMETHING with an error, never silent
        empty success."""
        with patch(
            "windyfly.tools.web_search.fetch_via_windy_search",
            return_value={"url": "u", "content": "", "error": "HTTP 502"},
        ), patch(
            "windyfly.tools.web_search.httpx.get",
            side_effect=Exception("direct also dead"),
        ):
            out = fetch_url("https://example.com/x")
        assert out["content"] == ""
        # Provider annotation surfaces the both-failed state.
        assert out["provider"] == "direct-fallback-failed"
        # And carries both error sources for debugging.
        assert "direct also dead" in (out.get("error") or "")
        assert out["windy_search_error"] == "HTTP 502"

    def test_unrouted_path_raises_hard_gate_error(self, monkeypatch):
        """Hard gate (Search V1, 2026-05-17): unrouted fetch_url must
        raise loudly rather than silently degrade to direct httpx. The
        old "pre-B.12 parity" behavior was duplicate infrastructure
        that bypassed Search V1's cost-cap + per-EII rate-limit + audit
        machinery."""
        monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
        monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)

        with pytest.raises(RuntimeError, match="Search V1 hard gate"):
            fetch_url("https://example.com/")
