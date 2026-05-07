"""Thin client for the Windy Search service (master plan B.12).

Routes web_search + fetch_url through the centralized windy-search
service when both env vars are set:

    WINDY_SEARCH_BASE_URL  e.g. https://api.windysearch.com
    WINDY_PASSPORT_EPT     the agent's bot-passport EPT (JWT)

When either is unset, callers fall back to direct Brave/DDG/httpx (the
existing behavior in tools/web_search.py). This keeps B.12 opt-in:
no production agent's behavior changes until the operator flips the
env vars in their soul repo.

What you gain by routing through windy-search:
  - Cross-tenant query/page cache (no duplicate Brave spend)
  - Per-passport monthly USD cost cap
  - Per-EII rate limits (your tier scales with reputation)
  - SSRF-hardened fetch
  - Every request audited as an integrity event in eternitas

The windy-search wire contract is documented at the OpenAPI spec
served by the service: e.g. https://api.windysearch.com/docs.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0


def is_routed_through_search() -> bool:
    """Both env vars must be set to opt in."""
    return bool(
        os.environ.get("WINDY_SEARCH_BASE_URL")
        and os.environ.get("WINDY_PASSPORT_EPT")
    )


def _base_url() -> str:
    return os.environ["WINDY_SEARCH_BASE_URL"].rstrip("/")


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {os.environ['WINDY_PASSPORT_EPT']}"}


def search_via_windy_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Run a web search through windy-search. Returns the same dict
    shape as the existing direct web_search for caller compatibility:

        {"query": ..., "results": [{title, snippet, url}, ...], "provider": "windy-search"}
    """
    try:
        resp = httpx.post(
            f"{_base_url()}/web/search",
            headers={**_auth_header(), "Content-Type": "application/json"},
            json={"query": query, "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "windy-search /web/search returned %d: %s",
            e.response.status_code, e.response.text[:200],
        )
        return {"query": query, "results": [], "provider": "windy-search-error",
                "error": f"HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        logger.warning("windy-search /web/search network error: %s", e)
        return {"query": query, "results": [], "provider": "windy-search-error",
                "error": str(e)}

    return {
        "query": payload.get("query", query),
        "results": payload.get("results", []),
        "provider": f"windy-search:{payload.get('backend', 'unknown')}",
        "cache_hit": payload.get("cache_hit", False),
    }


def fetch_via_windy_search(
    url: str,
    max_chars: int = 20000,
    offset: int = 0,
) -> dict[str, Any]:
    """Fetch a URL through windy-search. Returns the same dict shape as
    the existing direct fetch_url for caller compatibility:

        {url, content, offset, returned_chars, total_length, truncated,
         next_offset, length}
    """
    try:
        resp = httpx.post(
            f"{_base_url()}/web/fetch",
            headers={**_auth_header(), "Content-Type": "application/json"},
            json={"url": url, "max_chars": max_chars, "offset": offset},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "windy-search /web/fetch returned %d: %s",
            e.response.status_code, e.response.text[:200],
        )
        return {"url": url, "content": "",
                "error": f"HTTP {e.response.status_code}"}
    except httpx.HTTPError as e:
        logger.warning("windy-search /web/fetch network error: %s", e)
        return {"url": url, "content": "", "error": str(e)}

    content = payload.get("content", "")
    total = payload.get("total_chars", len(content))
    truncated = payload.get("truncated", False)
    end = offset + max_chars
    return {
        "url": url,
        "content": content,
        "offset": offset,
        "returned_chars": len(content),
        "total_length": total,
        "truncated": truncated,
        "next_offset": end if truncated else None,
        "length": total,  # legacy alias for older callers
        "provider": "windy-search",
        "cache_hit": payload.get("cache_hit", False),
    }
