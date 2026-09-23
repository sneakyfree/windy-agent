"""Synthetic traffic marking (ecosystem convention, Telemetry UPDATE 4).

When ``WINDY_SYNTHETIC=1`` (set only by our own probes: fire drills, the
journey probe, clean-machine tests), every outbound HTTP request from this
process carries ``X-Windy-Synthetic: 1`` so each Windy service marks its own
rows synthetic, and every telemetry row we emit carries
``metadata.synthetic: true``. Real traffic never sets it; absent means real.

The header is added by wrapping ``httpx``'s client ``send`` once per process,
so it covers the shared clients and one-off ``httpx.post`` calls alike, but
ONLY for requests to Windy hosts (``WINDY_DOMAINS`` and their subdomains).
Third parties (Anthropic, OpenAI, Hugging Face, ...) never see it. It never
overwrites a header a caller set.
"""

from __future__ import annotations

import os
from typing import Any

HEADER = "X-Windy-Synthetic"

# The Windy services that honour the header. A request host must be one of
# these or a subdomain of one; nothing else ever gets it.
WINDY_DOMAINS: tuple[str, ...] = (
    "windyword.ai", "windymind.ai", "windychat.ai", "windysearch.com",
    "eternitas.ai", "windyfly.ai", "windymail.ai", "windycloud.com",
)


def is_windy_host(host: str) -> bool:
    h = (host or "").lower().rstrip(".")
    return any(h == d or h.endswith("." + d) for d in WINDY_DOMAINS)

_installed = False


def active() -> bool:
    return os.environ.get("WINDY_SYNTHETIC", "").strip() == "1"


def install() -> bool:
    """Wrap httpx send when synthetic mode is on. Idempotent; returns
    whether the wrapper is (now) installed."""
    global _installed
    if _installed:
        return True
    if not active():
        return False
    import httpx

    orig_sync = httpx.Client.send
    orig_async = httpx.AsyncClient.send

    def _mark(request: httpx.Request) -> None:
        if HEADER not in request.headers and is_windy_host(request.url.host):
            request.headers[HEADER] = "1"

    def send(self: httpx.Client, request: httpx.Request, *a: Any, **kw: Any) -> httpx.Response:
        _mark(request)
        return orig_sync(self, request, *a, **kw)

    async def asend(self: httpx.AsyncClient, request: httpx.Request, *a: Any,
                    **kw: Any) -> httpx.Response:
        _mark(request)
        return await orig_async(self, request, *a, **kw)

    httpx.Client.send = send  # type: ignore[method-assign]
    httpx.AsyncClient.send = asend  # type: ignore[method-assign]
    _installed = True
    return True
