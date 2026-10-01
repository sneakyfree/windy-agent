"""Eternitas parity for message senders (dark: WINDY_PARITY_BANDS=1, off by default).

Grant's rule (10-01): a valid Eternitas credential gets the same access a human has.
For an AGENT sender (Matrix id ``@agent_<passport>:<server>``) the runtime asks
Eternitas's public Trust API about that passport:

- active, not a TEST passport  -> the USER band (read-only / safe tools, never the
  owner's data or actions in the owner's name);
- revoked or suspended         -> refused outright with a short honest line;
- unknown, a TEST passport, or Eternitas unreachable -> no change (the stranger
  band), i.e. it fails CLOSED.

An agent account is never bound as the owner by Trust-On-First-Use while this is on.
Verified HUMAN senders need Windy Chat to say who they are (pending); until then they
keep today's behaviour.
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
        return None  # not cached: try again next message; fails closed meanwhile
    _CACHE[passport] = (time.time(), data if isinstance(data, dict) else None)
    return _CACHE[passport][1]


def verdict(sender: str | None) -> str | None:
    """'user' | 'refused' | None (no change)."""
    passport = passport_of(sender)
    if not passport:
        return None
    t = _trust(passport)
    if not t:
        return None
    status = str(t.get("status") or "").lower()
    if status in ("revoked", "suspended"):
        return "refused"
    if status == "active" and not t.get("test_identity") and not passport.startswith("ET26-TEST"):
        return "user"
    return None


def _reset_for_tests() -> None:
    _CACHE.clear()
