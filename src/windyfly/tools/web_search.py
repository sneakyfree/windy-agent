"""Web search tool — hard-gated through Windy Search (api.windysearch.com).

**Hard gate (Search V1 — 2026-05-17):** all agent web search and fetch
MUST route through windy-search. The service has its own Brave→Google
provider failover internally; a second consumer-side fallback (the
old Brave-direct → DuckDuckGo chain) was duplicate infrastructure
that bypassed Search V1's cost-cap, per-EII-tier rate-limit, and
integrity-event audit machinery.

Requires both env vars at first call:
  WINDY_SEARCH_BASE_URL  e.g. https://api.windysearch.com
  WINDY_PASSPORT_EPT     the agent's bot-passport EPT (JWT)

If either is missing, web_search()/fetch_url() raise RuntimeError with
an actionable message. Fail loud at first call rather than silently
degrade — that's the whole point of the hard gate.

`fetch_url` keeps a smart rescue path: when windy-search itself returns
5xx (its own fetcher gets anti-bot blocked, etc.), fall back to direct
httpx with browser-shaped headers. This is NOT a competing search
provider — it's a circuit breaker that keeps the agent functional when
our own service has a hiccup. Pass-through 4xx (target site refused)
does NOT trigger the rescue; direct httpx would get the same answer.
"""

from __future__ import annotations

import logging
import re
import ipaddress
import socket
from typing import Any
from urllib.parse import urljoin, urlparse

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

_HARD_GATE_ERROR = (
    "WEB_SEARCH_UNAVAILABLE: I'm not connected to the web right now "
    "— web_search and fetch_url require windy-search to be enabled "
    "(env vars WINDY_SEARCH_BASE_URL + WINDY_PASSPORT_EPT). Tell "
    "the user I can't browse the web in this configuration; "
    "offer to answer from what I know, or suggest the operator "
    "enable windy-search if web access matters for this task. Do "
    "NOT retry — the gate is intentional, not a transient error."
)


def web_search(query: str, limit: int = 5) -> dict[str, Any]:
    """Search the web through windy-search (hard-gated)."""
    if not is_routed_through_search():
        raise RuntimeError(_HARD_GATE_ERROR)
    return search_via_windy_search(query, limit)


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


_REDIRECT_CODES = frozenset({301, 302, 303, 307, 308})
_MAX_REDIRECTS = 5


def _host_ips(host: str) -> list[str]:
    """Resolve a hostname to its IP strings. Isolated so tests can patch it."""
    return [info[4][0] for info in socket.getaddrinfo(host, None)]


def _ip_blocked(ip_str: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    return bool(
        addr.is_private or addr.is_loopback or addr.is_link_local
        or addr.is_reserved or addr.is_multicast or addr.is_unspecified
    )


def _assert_public_url(url: str) -> None:
    """[I3] SSRF guard. Reject a non-http(s) scheme, or a host that resolves to a
    private / loopback / link-local (169.254.169.254 cloud metadata) / reserved
    address. Called before the initial fetch AND on every redirect hop, so an
    attacker can't point (or redirect) fetch_url at internal services."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked URL scheme: {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise ValueError("blocked URL: missing host")
    try:
        ipaddress.ip_address(host)
        ips = [host]  # host is already an IP literal — check it directly
    except ValueError:
        ips = _host_ips(host)  # hostname — resolve (patchable in tests)
    if not ips or any(_ip_blocked(ip) for ip in ips):
        raise ValueError(f"blocked non-public host: {host}")


def _direct_fetch_url(
    url: str,
    max_chars: int = 20000,
    offset: int = 0,
) -> dict[str, Any]:
    """Direct httpx fetch with browser-shaped headers. Used both as
    the no-routing default AND as the windy-search failover."""
    try:
        # [I3] Validate the target and EVERY redirect hop against the SSRF guard,
        # following redirects manually so a 3xx to an internal IP is blocked
        # before the request is made.
        current = url
        resp = None
        for _ in range(_MAX_REDIRECTS + 1):
            _assert_public_url(current)
            resp = httpx.get(
                current, timeout=15.0, follow_redirects=False,
                headers=_BROWSER_HEADERS,
            )
            location = (
                resp.headers.get("location")
                if resp.status_code in _REDIRECT_CODES
                else None
            )
            if not location:
                break
            current = urljoin(current, location)
        else:
            raise ValueError("too many redirects")
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
    render: str = "auto",
) -> dict[str, Any]:
    """Fetch a URL through windy-search (hard-gated) with a 5xx rescue.

    Hard gate (Search V1, 2026-05-17): WINDY_SEARCH_BASE_URL +
    WINDY_PASSPORT_EPT must be set or RuntimeError is raised.

    ``render`` (default "auto"): windy-search renders JS-heavy / bot-walled
    pages in a Browserbase cloud browser when the plain fetch returns an
    empty shell. "auto" is transparent (most pages stay cheap plain);
    "on" forces a render; "off" is plain-only.

    Rescue path (kept): if windy-search itself returns 5xx / timeout /
    connect error (its fetcher got anti-bot blocked, target IP refuses
    its UA, etc.), fall back to direct httpx with browser-shaped
    headers. This is NOT a competing search provider — it's a circuit
    breaker. Pass-through 4xx from windy-search means the target site
    refused; direct httpx would get the same answer, so no rescue.

    Pagination: response includes total_length + next_offset; call
    again with offset=next_offset to read past the slice cap.
    """
    if not is_routed_through_search():
        raise RuntimeError(_HARD_GATE_ERROR)
    result = fetch_via_windy_search(url, max_chars=max_chars, offset=offset, render=render)
    if _is_windy_search_failure(result.get("error")):
        logger.info(
            "windy-search /web/fetch failed (%s); falling back "
            "to direct httpx for %s",
            result.get("error"), url,
        )
        direct = _direct_fetch_url(url, max_chars=max_chars, offset=offset)
        if direct.get("content"):
            direct["provider"] = "direct-fallback"
            direct["windy_search_error"] = result.get("error")
            return direct
        direct["provider"] = "direct-fallback-failed"
        direct["windy_search_error"] = result.get("error")
        return direct
    return result


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
                "render": {
                    "type": "string",
                    "enum": ["off", "auto", "on"],
                    "description": (
                        "How to handle JavaScript-heavy pages. 'auto' (default) "
                        "renders in a real cloud browser only if the plain fetch "
                        "comes back an empty shell — leave it on 'auto' for most "
                        "pages. Use 'on' to force a full browser render for a "
                        "known JS app / dashboard; 'off' for plain HTML only."
                    ),
                },
            },
            "required": ["url"],
        },
        fn=fetch_url,
    )
