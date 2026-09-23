"""Windy Search client on the canonical POST /v1/search (2026-09-23).

Before this, the client read WINDY_PASSPORT_EPT + a required base URL, while
hatch and `windy ept refresh` write ETERNITAS_PASSPORT_TOKEN, so web access
was silently off on real installs. Windy Search's limits both arrive as 429:
headers tell a per-minute rate limit (X-RateLimit-*) from an exhausted
monthly budget (X-Cost-Cap-USD + Retry-After: 86400).
"""
from __future__ import annotations

import logging
import time

import httpx
import pytest

import windyfly.tools.windy_search_client as client

TOKEN = "eyJ.SECRET-EPT-VALUE.sig"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
    monkeypatch.delenv("WINDY_SEARCH_BASE_URL", raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", TOKEN)
    monkeypatch.setattr(client, "_budget_exhausted_until", 0.0)


def _respond(monkeypatch, status=200, body=None, headers=None):
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status, json=body if body is not None else {}, headers=headers or {})

    transport = httpx.MockTransport(handler)

    def fake_post(url, **kw):
        with httpx.Client(transport=transport) as c:
            return c.post(url, headers=kw.get("headers"), json=kw.get("json"))

    monkeypatch.setattr(client.httpx, "post", fake_post)
    return calls


def test_routes_on_canonical_token_with_default_base(monkeypatch):
    assert client.is_routed_through_search() is True
    calls = _respond(monkeypatch, body={"id": "srch_x", "results": [
        {"url": "https://a", "title": "A", "snippet": "a", "rank": 1},
        {"title": "no url is dropped"},
    ], "stats": {"bridges_used": ["brave"]}})
    out = client.search_via_windy_search("hello", limit=99)
    req = calls[0]
    assert str(req.url) == "https://api.windysearch.com/v1/search"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert req.read() == b'{"query":"hello","max_results":50}'
    assert out["results"] == [{"title": "A", "snippet": "a", "url": "https://a"}]
    assert out["provider"] == "windy-search:brave"
    assert out["search_id"] == "srch_x"


def test_legacy_alias_and_base_override(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    monkeypatch.setenv("WINDY_PASSPORT_EPT", "legacy-token")
    monkeypatch.setenv("WINDY_SEARCH_BASE_URL", "https://search.example/")
    calls = _respond(monkeypatch, body={"results": []})
    client.search_via_windy_search("q")
    assert str(calls[0].url) == "https://search.example/v1/search"
    assert calls[0].headers["Authorization"] == "Bearer legacy-token"


def test_no_token_means_not_routed(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN")
    assert client.is_routed_through_search() is False


@pytest.mark.parametrize("status,expect", [
    (401, "search credential rejected"),
    (503, "temporarily unavailable"),
])
def test_error_wording(monkeypatch, status, expect):
    _respond(monkeypatch, status=status, body={"detail": "x"})
    out = client.search_via_windy_search("q")
    assert out["results"] == [] and expect in out["error"]


def test_rate_limit_429_does_not_block_future_calls(monkeypatch):
    calls = _respond(monkeypatch, status=429, body={"detail": "slow down"},
                     headers={"X-RateLimit-Limit": "50", "X-RateLimit-Count": "51"})
    out = client.search_via_windy_search("q")
    assert "rate limited" in out["error"] and "budget_exhausted" not in out
    client.search_via_windy_search("q")
    assert len(calls) == 2  # still calling: a rate limit is transient


def test_budget_429_stops_calls_until_reset(monkeypatch):
    calls = _respond(monkeypatch, status=429,
                     body={"detail": "Monthly budget exhausted. Resets on the 1st."},
                     headers={"X-Cost-Cap-USD": "5.00", "Retry-After": "86400"})
    first = client.search_via_windy_search("q")
    assert first["error"] == "monthly search budget reached" and first["budget_exhausted"]
    assert client._budget_exhausted_until >= time.time() + 86000
    second = client.search_via_windy_search("q")
    third = client.fetch_via_windy_search("https://example.com")
    assert len(calls) == 1  # no more requests until the reset
    assert second["budget_exhausted"] and "retry_after_utc" in second
    assert third["budget_exhausted"] and third["content"] == ""


def test_network_error_never_raises(monkeypatch):
    def boom(url, **kw):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(client.httpx, "post", boom)
    out = client.search_via_windy_search("q")
    assert out["provider"] == "windy-search-error" and "unreachable" in out["error"]


def test_token_never_logged(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)
    _respond(monkeypatch, status=401, body={"detail": "bad token"})
    client.search_via_windy_search("q")
    def boom(url, **kw):
        raise httpx.ConnectError(f"refused while sending {TOKEN}")
    monkeypatch.setattr(client.httpx, "post", boom)
    client.search_via_windy_search("q")
    assert TOKEN not in caplog.text
