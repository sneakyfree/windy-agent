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
# A Browserbase render spins up a real cloud browser (session create + CDP
# navigate + hydrate), so it needs a longer budget than a plain fetch.
_RENDER_TIMEOUT = 45.0


def is_routed_through_search() -> bool:
    """Both env vars must be set to opt in."""
    return bool(
        os.environ.get("WINDY_SEARCH_BASE_URL")
        and os.environ.get("WINDY_PASSPORT_EPT")
    )


# ── Budget-notice wiring (2026-07-06) ────────────────────────────────
#
# windy-search meters every paid capability against a per-passport
# monthly USD budget (B.9). The service emits two signals this client
# relays into tool results so the *agent* can tell its user in its own
# voice — the fly is the messenger, the server stays the meter:
#
#   1. `budget_warning: true` in a success body — edge-triggered
#      server-side on exactly the request that crosses 80% of the cap,
#      so relaying it can never nag.
#   2. HTTP 429 with an `X-Cost-Cap-USD` header — budget exhausted
#      (distinct from the per-minute rate-limit 429, which carries
#      X-RateLimit-* headers instead).

_WARNING_NOTICE = (
    "HEADS-UP FOR YOUR USER (relay once, gently, woven into your reply "
    "in your own words): this agent has used 80% of its included "
    "monthly web-access allowance. Web access still works — nothing is "
    "broken. The allowance resets on the 1st. If they want unlimited "
    "web access, they can power this agent with a model that has "
    "built-in web search (such as Claude or ChatGPT models), or raise "
    "the allowance in their Windy account."
)

_EXHAUSTED_NOTICE = (
    "BUDGET_EXHAUSTED — do not retry this tool. Tell your user, in a "
    "friendly way: this month's included web-access allowance is used "
    "up (it resets on the 1st). You can still help from what you "
    "already know. To keep searching the web this month, they can "
    "power you with a model that has built-in web search (such as "
    "Claude or ChatGPT models), or raise the allowance in their Windy "
    "account."
)


def _is_budget_exhausted(e: httpx.HTTPStatusError) -> bool:
    """True iff this 429 is the monthly-budget gate, not the rate limit."""
    return (
        e.response.status_code == 429
        and "X-Cost-Cap-USD" in e.response.headers
    )


def _budget_exhausted_fields(e: httpx.HTTPStatusError) -> dict[str, Any]:
    """Friendly, actionable fields for a budget-429 tool result."""
    return {
        "budget_exhausted": True,
        "budget_cap_usd": e.response.headers.get("X-Cost-Cap-USD"),
        "notice_to_user": _EXHAUSTED_NOTICE,
    }


def _budget_warning_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Thread the once-only 80% warning (plus context to phrase it)."""
    if not payload.get("budget_warning"):
        return {}
    return {
        "budget_warning": True,
        "budget_used_usd": payload.get("budget_used_usd"),
        "budget_cap_usd": payload.get("budget_cap_usd"),
        "notice_to_user": _WARNING_NOTICE,
    }


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
        result = {"query": query, "results": [], "provider": "windy-search-error",
                  "error": f"HTTP {e.response.status_code}"}
        if _is_budget_exhausted(e):
            result.update(_budget_exhausted_fields(e))
        return result
    except httpx.HTTPError as e:
        logger.warning("windy-search /web/search network error: %s", e)
        return {"query": query, "results": [], "provider": "windy-search-error",
                "error": str(e)}

    return {
        "query": payload.get("query", query),
        "results": payload.get("results", []),
        "provider": f"windy-search:{payload.get('backend', 'unknown')}",
        "cache_hit": payload.get("cache_hit", False),
        **_budget_warning_fields(payload),
    }


def fetch_via_windy_search(
    url: str,
    max_chars: int = 20000,
    offset: int = 0,
    render: str = "auto",
) -> dict[str, Any]:
    """Fetch a URL through windy-search. Returns the same dict shape as
    the existing direct fetch_url for caller compatibility:

        {url, content, offset, returned_chars, total_length, truncated,
         next_offset, length}

    ``render`` (windy-search B.6): "off" = plain HTTP; "auto" (default) =
    plain first, escalate to a Browserbase cloud browser only when the page
    is an unhydrated JS shell or a bot-wall; "on" = always render. "auto"
    gives the agent transparent JS rendering with no extra reasoning — most
    pages stay on the cheap plain path, so it barely costs anything.
    """
    try:
        resp = httpx.post(
            f"{_base_url()}/web/fetch",
            headers={**_auth_header(), "Content-Type": "application/json"},
            json={"url": url, "max_chars": max_chars, "offset": offset, "render": render},
            timeout=_RENDER_TIMEOUT if render != "off" else _TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "windy-search /web/fetch returned %d: %s",
            e.response.status_code, e.response.text[:200],
        )
        result: dict[str, Any] = {"url": url, "content": "",
                                  "error": f"HTTP {e.response.status_code}"}
        if _is_budget_exhausted(e):
            result.update(_budget_exhausted_fields(e))
        return result
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
        "rendered_via": payload.get("rendered_via"),  # None | "browserbase"
        "provider": "windy-search",
        "cache_hit": payload.get("cache_hit", False),
        **_budget_warning_fields(payload),
    }
