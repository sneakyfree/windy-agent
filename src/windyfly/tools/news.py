"""News tool — "What's the latest news?" / "Any tech news today?"

Parses free RSS feeds from major outlets. No API key needed.
Optionally uses NewsAPI.org if NEWS_API_KEY is set.
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0

# Free RSS feeds — no API key required
_RSS_FEEDS = {
    "general": [
        ("AP News", "https://rsshub.app/apnews/topics/apf-topnews"),
        ("BBC", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("Reuters", "https://www.reutersagency.com/feed/?taxonomy=best-sectors&post_type=best"),
    ],
    "tech": [
        ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
        ("TechCrunch", "https://techcrunch.com/feed/"),
        ("Hacker News", "https://hnrss.org/frontpage"),
    ],
    "science": [
        ("Nature", "https://www.nature.com/nature.rss"),
    ],
    "business": [
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
    ],
}


def get_news(topic: str | None = None, count: int = 5) -> dict[str, Any]:
    """Get latest news headlines.

    Args:
        topic: Optional topic filter (tech, science, business, or general).
        count: Number of headlines to return.
    """
    # Try NewsAPI if key is available
    api_key = os.environ.get("NEWS_API_KEY", "")
    if api_key:
        return _newsapi_search(topic, count, api_key)

    # Fall back to RSS
    return _rss_search(topic, count)


def _rss_search(topic: str | None, count: int) -> dict[str, Any]:
    """Fetch news from RSS feeds."""
    # Pick feeds based on topic
    category = "general"
    if topic:
        topic_lower = topic.lower()
        for cat in _RSS_FEEDS:
            if cat in topic_lower:
                category = cat
                break

    feeds = _RSS_FEEDS.get(category, _RSS_FEEDS["general"])
    articles: list[dict[str, str]] = []

    for source_name, feed_url in feeds:
        try:
            resp = httpx.get(feed_url, timeout=_TIMEOUT, follow_redirects=True)
            if resp.status_code != 200:
                continue
            items = _parse_rss(resp.text, source_name)
            articles.extend(items)
        except Exception as e:
            logger.debug("RSS fetch failed for %s: %s", source_name, e)

    # Deduplicate and limit
    seen_titles: set[str] = set()
    unique = []
    for a in articles:
        title = a["title"].strip().lower()
        if title not in seen_titles:
            seen_titles.add(title)
            unique.append(a)

    headlines = unique[:count]

    if not headlines:
        return {"headlines": [], "message": "Could not fetch news right now. Try again later."}

    lines = [f"📰 Top {len(headlines)} Headlines:\n"]
    for i, h in enumerate(headlines, 1):
        lines.append(f"{i}. {h['title']} — {h['source']}")

    return {"headlines": headlines, "message": "\n".join(lines)}


def _parse_rss(xml_text: str, source: str) -> list[dict[str, str]]:
    """Parse RSS XML into article dicts."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title = item.findtext("title", "").strip()
            link = item.findtext("link", "").strip()
            desc = item.findtext("description", "").strip()
            # Strip HTML from description
            desc = re.sub(r"<[^>]+>", "", desc)[:200]
            if title:
                items.append({
                    "title": title,
                    "url": link,
                    "description": desc,
                    "source": source,
                })
    except ET.ParseError:
        pass
    return items


def _newsapi_search(topic: str | None, count: int, api_key: str) -> dict[str, Any]:
    """Fetch news from NewsAPI.org."""
    try:
        params: dict[str, str | int] = {
            "apiKey": api_key,
            "pageSize": count,
            "language": "en",
        }
        if topic:
            url = "https://newsapi.org/v2/everything"
            params["q"] = topic
            params["sortBy"] = "publishedAt"
        else:
            url = "https://newsapi.org/v2/top-headlines"
            params["country"] = "us"

        resp = httpx.get(url, params=params, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        headlines = []
        for a in data.get("articles", [])[:count]:
            headlines.append({
                "title": a.get("title", ""),
                "url": a.get("url", ""),
                "description": a.get("description", ""),
                "source": a.get("source", {}).get("name", ""),
            })

        lines = [f"📰 Top {len(headlines)} Headlines:\n"]
        for i, h in enumerate(headlines, 1):
            lines.append(f"{i}. {h['title']} — {h['source']}")

        return {"headlines": headlines, "message": "\n".join(lines)}
    except Exception as e:
        logger.warning("NewsAPI failed, falling back to RSS: %s", e)
        return _rss_search(topic, count)


def register_news_tool(registry: ToolRegistry) -> None:
    """Register news tool with the LLM."""
    registry.register(
        name="get_news",
        description=(
            "Get the latest news headlines. Use when the user asks "
            "'What's the latest news?', 'Any tech news?', 'What's happening in the world?'. "
            "Can filter by topic: tech, science, business."
        ),
        parameters={
            "type": "object",
            "properties": {
                "topic": {
                    "type": "string",
                    "description": "Optional topic: 'tech', 'science', 'business', or a search term",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of headlines (default: 5)",
                },
            },
        },
        fn=get_news,
    )
