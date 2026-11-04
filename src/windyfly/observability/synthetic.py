"""Synthetic traffic marking (ecosystem convention, Telemetry UPDATE 4).

When the agent itself is test-born (its passport is ET26-TEST-*), or when a
process WITHOUT a real passport sets ``WINDY_SYNTHETIC=1`` (our own probes: the
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


TEST_PASSPORT_PREFIX = "ET26-TEST-"


def _passport() -> str:
    """This process's passport: ETERNITAS_PASSPORT, else the ``sub`` of its passport token (read, never verified)."""
    passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    if passport:
        return passport
    token = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
    if token.count(".") == 2:
        import base64
        import json

        seg = token.split(".")[1]
        try:
            sub = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))).get("sub")
        except (ValueError, AttributeError):
            return ""
        return sub.strip() if isinstance(sub, str) else ""
    return ""


def active() -> bool:
    """Synthetic when this agent is TEST-BORN (its own passport number is an ET26-TEST-* one): a test agent's traffic
    is never a person's, even when its operator forgets the env (10-22: the lab agent's Mind calls went out unmarked,
    ~10 a day). A REAL passport is never synthetic, whatever WINDY_SYNTHETIC says: a real agent must never claim to
    be a test (Hub + Boss 11-04: Zero's passport went out marked on ~165 Mind calls a week). WINDY_SYNTHETIC=1 marks
    only a process with no passport at all (a probe)."""
    passport = _passport()
    if passport:
        return passport.startswith(TEST_PASSPORT_PREFIX)
    return os.environ.get("WINDY_SYNTHETIC", "").strip() == "1"


def install() -> bool:
    """Wrap httpx send. Idempotent; returns True once the wrapper is on. It is put on for EVERY process: it adds the
    header for a synthetic one, and for a real passport it also STRIPS a header some code set by hand (Hub on #710:
    a real agent's request to a Windy host never carries it)."""
    global _installed
    if _installed:
        return True
    if not active() and not _passport():
        return False      # no passport and no probe switch: nothing to add and nothing to strip
    import httpx

    orig_sync = httpx.Client.send
    orig_async = httpx.AsyncClient.send

    def _mark(request: httpx.Request) -> None:
        # active() again per request: a passport loaded after the wrapper went on (the .env, a pairing) is honoured
        if not is_windy_host(request.url.host):
            return
        if active():
            if HEADER not in request.headers:
                request.headers[HEADER] = "1"
        elif _passport():
            request.headers.pop(HEADER, None)    # a real passport: never marked, not even by hand

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
