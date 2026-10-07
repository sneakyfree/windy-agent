"""Refuse a revoked or suspended AGENT sender (dark: WINDY_PARITY_BANDS=1, off by default).

For an AGENT sender (Matrix id ``@agent_<passport>:<server>``) the runtime asks Eternitas's public
Trust API about that passport: revoked or suspended -> refused outright with one honest line.
Everything else (active, unknown, a TEST passport, Eternitas unreachable) is unchanged: this module
grants no band and never blocks because Eternitas blinked.
"""

from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_AGENT_RE = re.compile(r"^@agent_([a-z0-9-]+):", re.IGNORECASE)
_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}
_TTL_S = 300.0
REFUSED_LINE = "I can't talk with that agent: its Eternitas passport is revoked or suspended."


def enabled() -> bool:
    return os.environ.get("WINDY_PARITY_BANDS", "") == "1"


def passport_of(sender: str | None) -> str | None:
    m = _AGENT_RE.match(sender or "")
    return m.group(1).upper() if m else None


def _trust(passport: str) -> dict[str, Any] | None:
    hit = _CACHE.get(passport)
    if hit and time.time() - hit[0] < _TTL_S:
        return hit[1]
    base = (os.environ.get("ETERNITAS_URL") or "https://api.eternitas.ai").rstrip("/")
    data: dict[str, Any] | None
    try:
        resp = httpx.get(f"{base}/api/v1/trust/{passport}", timeout=5.0)
        data = resp.json() if resp.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("parity: trust lookup failed for %s: %s", passport, e)
        return None  # not cached: try again next message
    _CACHE[passport] = (time.time(), data if isinstance(data, dict) else None)
    return _CACHE[passport][1]


def verdict(sender: str | None) -> str | None:
    """'refused' for a revoked or suspended agent sender, else None (no change)."""
    passport = passport_of(sender)
    if not passport:
        return None
    t = _trust(passport)
    if t and str(t.get("status") or "").lower() in ("revoked", "suspended"):
        return "refused"
    return None


def _reset_for_tests() -> None:
    _CACHE.clear()
