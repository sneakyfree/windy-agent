"""Web search tool — Brave Search (primary) + DuckDuckGo (fallback).

Brave Search: free tier 2000 queries/month, high-quality results.
DuckDuckGo: no API key needed, instant answers only.
Also includes fetch_url for reading specific web pages.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the web. Uses Brave if API key available, else DuckDuckGo."""
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


def fetch_url(url: str, max_chars: int = 5000) -> dict[str, Any]:
    """Fetch a URL and return its text content (HTML stripped).

    Useful for "Read this article for me: [URL]".
    """
    try:
        resp = httpx.get(url, timeout=15.0, follow_redirects=True)
        resp.raise_for_status()
        html = resp.text

        # Strip HTML tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()

        return {
            "url": url,
            "content": text[:max_chars],
            "truncated": len(text) > max_chars,
            "length": len(text),
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
            "Fetch and read a specific web page. Use when the user shares a URL "
            "and asks you to read, summarize, or extract info from it."
        ),
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "The URL to fetch"},
            },
            "required": ["url"],
        },
        fn=fetch_url,
    )
