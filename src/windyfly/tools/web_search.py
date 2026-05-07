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


def fetch_url(
    url: str,
    max_chars: int = 20000,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch a URL and return its text content (HTML stripped).

    Routing (master plan B.12): if WINDY_SEARCH_BASE_URL +
    WINDY_PASSPORT_EPT are set, routes through the centralized service
    (cross-tenant cache, SSRF protection, integrity-event audit).
    Otherwise falls back to direct httpx.

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
        return fetch_via_windy_search(url, max_chars=max_chars, offset=offset)
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
        }
    except Exception as e:
        return {"url": url, "content": "", "error": str(e)}


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
