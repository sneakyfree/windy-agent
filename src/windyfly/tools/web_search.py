"""Web search tool for the agent.

Uses DuckDuckGo instant answer API (no API key required).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0


def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the web using DuckDuckGo instant answer API.

    Args:
        query: Search query.
        limit: Max results to return.

    Returns:
        Dict with search results.
    """
    try:
        response = httpx.get(
            "https://api.duckduckgo.com/",
            params={"q": query, "format": "json", "no_html": 1},
            timeout=_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()

        results: list[dict[str, str]] = []

        # Abstract (instant answer)
        if data.get("Abstract"):
            results.append({
                "title": data.get("Heading", ""),
                "snippet": data["Abstract"],
                "url": data.get("AbstractURL", ""),
            })

        # Related topics
        for topic in data.get("RelatedTopics", [])[:limit]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append({
                    "title": topic.get("Text", "")[:100],
                    "snippet": topic.get("Text", ""),
                    "url": topic.get("FirstURL", ""),
                })

        return {"query": query, "results": results[:limit]}
    except httpx.HTTPError as e:
        logger.error("Web search failed: %s", e)
        return {"query": query, "results": [], "error": str(e)}


def register_web_search_tool(registry: ToolRegistry) -> None:
    """Register the web search tool with the registry.

    Args:
        registry: ToolRegistry instance.
    """
    registry.register(
        name="web_search",
        description=(
            "Search the web for information. Use this when the user asks about "
            "current events, facts you're unsure about, or anything you don't "
            "have in memory."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default: 5)",
                },
            },
            "required": ["query"],
        },
        fn=web_search,
    )
