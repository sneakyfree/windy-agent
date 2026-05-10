"""Web search tool — Windy Search (preferred when configured) +
Brave Search + DuckDuckGo fallbacks.

Resolution order:
  1. If WINDY_SEARCH_BASE_URL and WINDY_PASSPORT_EPT are both set, route
     through the centralized Windy Search service (master plan B.12).
     This is opt-in — nothing changes for agents that don't set both.
  2. Else if BRAVE_SEARCH_API_KEY is set, call Brave directly (free tier
     2000/month, high quality).
  3. Else fall back to DuckDuckGo instant-answer (no key, low quality).

Also includes fetch_url for reading specific web pages, with the same
opt-in routing.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry
from windyfly.tools.windy_search_client import (
    fetch_via_windy_search,
    is_routed_through_search,
    search_via_windy_search,
)

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# Wikipedia (and many CDN-fronted sites) 403 the default httpx UA. A
# real browser-like UA gets us past the bot filter for plain reads.
_BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the web.

    Routing (master plan B.12):
      - WINDY_SEARCH_BASE_URL + WINDY_PASSPORT_EPT set → centralized service
      - BRAVE_SEARCH_API_KEY set                       → direct Brave
      - else                                            → DuckDuckGo
    """
    if is_routed_through_search():
        return search_via_windy_search(query, limit)
    if os.environ.get("BRAVE_SEARCH_API_KEY"):
        return _brave_search(query, limit)
    return _ddg_search(query, limit)


def _brave_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search using Brave Search API."""
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": limit},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("web", {}).get("results", [])[:limit]:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("description", ""),
                "url": item.get("url", ""),
            })

        return {"query": query, "results": results, "provider": "brave"}
    except Exception as e:
        logger.warning("Brave search failed, falling back to DuckDuckGo: %s", e)
        return _ddg_search(query, limit)


def _ddg_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search using DuckDuckGo instant answer API (no API key)."""
    try:
        response = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        results: list[dict[str, str]] = []

        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["Abstract"],
                "url": data.get("AbstractURL", ""),
            })

        for topic in data.get("RelatedTopics", [])[:limit]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", ""),
                })

        return {"query": query, "results": results[:limit], "provider": "duckduckgo"}
    except httpx.HTTPError as e:
        logger.error("Web search failed: %s", e)
        return {"query": query, "results": [], "error": str(e)}


# Errors that indicate the windy-search service itself is broken
# (not a pass-through of an upstream-target failure). When fetch_via
# _windy_search returns one of these, the direct-httpx fallback is
# worth trying — the target site might respond differently to a
# direct request from this iMac than to windy-search's fetcher.
#
# Surfaced 2026-05-10: windy-search /web/fetch returned HTTP 502 with
# detail "upstream HTTP 403" — windy-search couldn't get past the
# target's anti-bot block. Direct httpx with _BROWSER_HEADERS often
# bypasses such blocks because the request comes from a different
# IP and a real-looking user agent.
_WINDY_SEARCH_FAILURE_INDICATORS = (
    "HTTP 502",
    "HTTP 503",
    "HTTP 504",
    "ConnectError",
    "ConnectTimeout",
    "ReadTimeout",
    "WriteTimeout",
    "PoolTimeout",
    "RemoteProtocolError",
)


def _is_windy_search_failure(error: str | None) -> bool:
    """True iff the error string suggests windy-search itself failed
    (vs. windy-search successfully passing through a 4xx from the
    target URL). 4xx from windy-search means the target site itself
    refused — direct httpx would just get the same answer, no point
    falling back."""
    if not error:
        return False
    return any(ind in error for ind in _WINDY_SEARCH_FAILURE_INDICATORS)


def _direct_fetch_url(
    url: str,
    max_chars: int = 20000,
    offset: int = 0,
) -> dict[str, Any]:
    """Direct httpx fetch with browser-shaped headers. Used both as
    the no-routing default AND as the windy-search failover."""
    try:
        resp = httpx.get(
            url, timeout=15.0, follow_redirects=True,
            headers=_BROWSER_HEADERS,
        )
        resp.raise_for_status()
        html = resp.text

        # Strip HTML tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        total_length = len(text)
        offset = max(0, offset)
        end = offset + max_chars
        slice_ = text[offset:end] if offset < total_length else ""
        truncated = end < total_length

        return {
            "url": url,
            "content": slice_,
            "offset": offset,
            "returned_chars": len(slice_),
            "total_length": total_length,
            "truncated": truncated,
            "next_offset": end if truncated else None,
            # Kept for backwards-compatible callers / older episodes.
            "length": total_length,
            "provider": "direct",
        }
    except Exception as e:
        return {"url": url, "content": "", "error": str(e),
                "provider": "direct"}


def fetch_url(
    url: str,
    max_chars: int = 20000,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch a URL and return its text content (HTML stripped).

    Routing + failover:
      1. If WINDY_SEARCH_BASE_URL + WINDY_PASSPORT_EPT are set, route
         through the centralized service (cross-tenant cache, SSRF
         protection, integrity-event audit).
      2. If windy-search itself fails (502 / 503 / 504 / timeout /
         connect error — not a pass-through 4xx), fall back to direct
         httpx with browser-shaped headers. The target site often
         responds differently to a direct request than to windy-
         search's fetcher (different IP, different UA), so the
         fallback rescues a meaningful fraction of fetches.
      3. Otherwise direct httpx is used from the start.

    Surfaced 2026-05-10: windy-search /web/fetch was returning 502s
    on 2 fetches in a turn (upstream sites 403'ing windy-search's
    fetcher). The bot reported "network is down" and gave up despite
    direct httpx being viable. Failover added.

    Stress harness v4 round-3 finding: the prior 5000-char cap meant
    the bot couldn't read past the opening of any real article (most
    Wikipedia bodies are 30-100KB of text). Default raised to 20000
    chars and an ``offset`` parameter added so the bot can read past
    the cap by repeating the call with offset += max_chars. The
    response always includes ``total_length`` so the LLM can decide
    whether another fetch is worth it.

    Useful for "Read this article for me: [URL]" and "give me the
    last paragraph of [URL]".
    """
    if is_routed_through_search():
        result = fetch_via_windy_search(url, max_chars=max_chars, offset=offset)
        # If windy-search itself broke (not a pass-through of a
        # target-side 4xx), try direct httpx as a rescue. Don't
        # fall back on pass-through 4xx — direct would get the
        # same answer.
        if _is_windy_search_failure(result.get("error")):
            logger.info(
                "windy-search /web/fetch failed (%s); falling back "
                "to direct httpx for %s",
                result.get("error"), url,
            )
            direct = _direct_fetch_url(url, max_chars=max_chars, offset=offset)
            if direct.get("content"):
                # Annotate so debugging / logs show the rescue.
                direct["provider"] = "direct-fallback"
                direct["windy_search_error"] = result.get("error")
                return direct
            # Direct also failed — return it (it carries its own
            # error string) so the caller sees the real problem.
            direct["provider"] = "direct-fallback-failed"
            direct["windy_search_error"] = result.get("error")
            return direct
        return result
    return _direct_fetch_url(url, max_chars=max_chars, offset=offset)


def register_web_search_tool(registry: ToolRegistry) -> None:
    """Register web search and fetch_url tools."""
    registry.register(
        name="web_search",
        description=(
            "Search the web for information. Use this when the user asks about "
            "current events, facts you're unsure about, or anything you don't "
            "have in memory. Returns titles, snippets, and URLs."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query"},
                "limit": {"type": "integer", "description": "Max results (default: 5)"},
            },
            "required": ["query"],
        },
        fn=web_search,
    )

    registry.register(
        name="fetch_url",
        description=(
            "Fetch and read a specific web page (HTML stripped, plain text). "
            "Use when the user shares a URL and asks you to read, summarize, "
            "or extract info from it. Default returns up to 20000 chars; pass "
            "max_chars to change the slice size (LLM-context cost). For long "
            "pages (Wikipedia, blog posts), the response includes total_length "
            "and next_offset — call again with offset=next_offset to read the "
            "next chunk. Returns {content, offset, returned_chars, "
            "total_length, truncated, next_offset}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
                "max_chars": {
                    "type": "integer",
                    "description": (
                        "Max characters to return per call (default 20000). "
                        "Raise for big articles, lower to save context."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Char offset to start the slice at (default 0). For "
                        "pagination through long pages: call with the prior "
                        "response's next_offset."
                    ),
                },
            },
            "required": ["url"],
        },
        fn=fetch_url,
    )
