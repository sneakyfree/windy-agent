"""Thin client for the Windy Search service (master plan B.12).

Routes web_search + fetch_url through the centralized windy-search
service. Configuration:

    ETERNITAS_PASSPORT_TOKEN  the agent's bot-passport EPT (JWT) — the same
                              variable every other module reads; its
                              presence is what turns web access on
    WINDY_PASSPORT_EPT        legacy alias for the EPT, still honoured
    WINDY_SEARCH_BASE_URL     optional; defaults to https://api.windysearch.com

Until 2026-09-23 this client required WINDY_PASSPORT_EPT + an explicit
base URL, while hatch/refresh write the EPT to ETERNITAS_PASSPORT_TOKEN —
so on real installs web access was silently off (the tool's hard gate
fired every time). It also called the legacy POST /web/search; search now
uses the canonical POST /v1/search.

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
import time
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
# A Browserbase render spins up a real cloud browser (session create + CDP
# navigate + hydrate), so it needs a longer budget than a plain fetch.
_RENDER_TIMEOUT = 45.0


DEFAULT_BASE_URL = "https://api.windysearch.com"


def _ept() -> str:
    """The agent's EPT. ETERNITAS_PASSPORT_TOKEN is canonical (hatch and
    `windy ept refresh` write it); WINDY_PASSPORT_EPT is kept as an alias
    so operators who configured the old name keep working."""
    return (
        os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
        or os.environ.get("WINDY_PASSPORT_EPT", "").strip()
    )


def is_routed_through_search() -> bool:
    """Web access is on whenever the agent holds a passport token."""
    return bool(_ept())


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
    return (os.environ.get("WINDY_SEARCH_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def _auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {_ept()}"}


# ── Budget exhausted: stop calling until it resets ───────────────────
#
# Windy Search returns 429 for BOTH limits; headers tell them apart:
#   - per-minute rate limit: X-RateLimit-* headers → fine to retry in ~60s
#   - monthly budget:        X-Cost-Cap-USD (+ Retry-After: 86400) →
#                            do NOT retry; it resets on the 1st
# After a budget 429 the client refuses locally until the reset, so a
# looping tool call can't burn requests (or look broken) all month.
_budget_exhausted_until: float = 0.0


def _next_month_start(now: float) -> float:
    d = datetime.fromtimestamp(now, timezone.utc)
    y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
    return datetime(y, m, 1, tzinfo=timezone.utc).timestamp()


def _mark_budget_exhausted(e: httpx.HTTPStatusError) -> None:
    global _budget_exhausted_until
    now = time.time()
    until = _next_month_start(now)
    retry_after = e.response.headers.get("Retry-After", "")
    if retry_after.isdigit():
        until = max(until, now + int(retry_after)) if int(retry_after) > 0 else until
    _budget_exhausted_until = until


def _budget_blocked() -> bool:
    return time.time() < _budget_exhausted_until


def _blocked_fields() -> dict[str, Any]:
    return {
        "budget_exhausted": True,
        "error": "monthly search budget reached",
        "retry_after_utc": datetime.fromtimestamp(
            _budget_exhausted_until, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notice_to_user": _EXHAUSTED_NOTICE,
    }


def _error_message(e: httpx.HTTPStatusError) -> str:
    """Plain, actionable wording per status. Never includes the token."""
    code = e.response.status_code
    if code == 401:
        return ("search credential rejected (passport revoked or token "
                "expired; run `windy ept refresh`)")
    if code == 429 and _is_budget_exhausted(e):
        return "monthly search budget reached"
    if code == 429:
        return "rate limited by windy-search (HTTP 429); retry in about a minute"
    if code == 503:
        return "windy-search temporarily unavailable (HTTP 503)"
    return f"HTTP {code}"


def search_via_windy_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Run a web search through windy-search's canonical POST /v1/search.
    Returns the same dict shape as the direct web_search always did:

        {"query": ..., "results": [{title, snippet, url}, ...], "provider": "windy-search:..."}
    """
    if _budget_blocked():
        return {"query": query, "results": [], "provider": "windy-search-error",
                **_blocked_fields()}
    try:
        resp = httpx.post(
            f"{_base_url()}/v1/search",
            headers={**_auth_header(), "Content-Type": "application/json"},
            json={"query": query, "max_results": max(1, min(int(limit), 50))},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
    except httpx.HTTPStatusError as e:
        logger.warning(
            "windy-search /v1/search returned %d: %s",
            e.response.status_code, e.response.text[:200],
        )
        result: dict[str, Any] = {"query": query, "results": [],
                                  "provider": "windy-search-error",
                                  "error": _error_message(e)}
        if _is_budget_exhausted(e):
            _mark_budget_exhausted(e)
            result.update(_budget_exhausted_fields(e))
        return result
    except httpx.HTTPError as e:
        logger.warning("windy-search /v1/search network error: %s", type(e).__name__)
        return {"query": query, "results": [], "provider": "windy-search-error",
                "error": f"{type(e).__name__}: windy-search unreachable"}
    except ValueError:
        return {"query": query, "results": [], "provider": "windy-search-error",
                "error": "windy-search returned a non-JSON response"}

    results = [
        {"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("url", "")}
        for r in (payload.get("results") or [])
        if isinstance(r, dict) and r.get("url")
    ]
    bridges = (payload.get("stats") or {}).get("bridges_used") or []
    return {
        "query": query,
        "results": results,
        "provider": "windy-search:" + (",".join(str(b) for b in bridges) or "v1"),
        "search_id": payload.get("id"),
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
    if _budget_blocked():
        return {"url": url, "content": "", **_blocked_fields()}
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
        fetch_result: dict[str, Any] = {"url": url, "content": "",
                                        "error": (f"HTTP {e.response.status_code}"
                                                  if e.response.status_code not in (401, 429, 503)
                                                  else _error_message(e))}
        if _is_budget_exhausted(e):
            _mark_budget_exhausted(e)
            fetch_result.update(_budget_exhausted_fields(e))
        return fetch_result
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
