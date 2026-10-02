"""windy_plans: Windy's current plans and prices, read live from the Hub.

The ONLY source an agent may quote prices from (Hub, 10-02: an agent recited another
company's plans as Windy's). GET https://account.windyword.ai/api/v1/plans is public;
the answer is cached for an hour. If it can't be read, the tool says so and gives the
plans page, never a remembered number.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

DEFAULT_PLANS_URL = "https://account.windyword.ai/api/v1/plans"
FALLBACK_PAGE = "https://app.windyword.ai/upgrade"
_TTL_S = 3600.0
_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _money(cents: Any) -> str | None:
    if not isinstance(cents, int):
        return None
    return f"${cents // 100}" if cents % 100 == 0 else f"${cents / 100:.2f}"


def _gb(n: Any) -> str | None:
    if not isinstance(n, int) or n <= 0:
        return None
    gb = n / 1024 ** 3
    return f"{gb / 1024:g} TB" if gb >= 1024 else (f"{gb:g} GB" if gb >= 1 else f"{n / 1024 ** 2:g} MB")


def _line(p: dict[str, Any]) -> str:
    name = str(p.get("display_name") or p.get("id") or "?")
    month, year = _money(p.get("monthly_cents")), _money(p.get("annual_cents"))
    if p.get("monthly_cents") == 0:
        price = "free"
    elif month and year:
        price = f"{month}/month or {year}/year"
    elif month:
        price = f"{month}/month"
    else:
        price = "custom pricing (ask Windy)"
    extras = []
    if isinstance(p.get("cloud_session_minutes"), int):
        extras.append(f"{p['cloud_session_minutes']}-minute cloud sessions")
    if isinstance(p.get("cloud_devices"), int):
        extras.append(f"{p['cloud_devices']} device{'s' if p['cloud_devices'] != 1 else ''}")
    if _gb(p.get("storage_bytes")):
        extras.append(f"{_gb(p.get('storage_bytes'))} storage")
    return f"{name}: {price}" + (f" ({', '.join(extras)})" if extras else "")


def windy_plans() -> dict[str, Any]:
    """Current Windy plans, as plain lines the agent can quote verbatim."""
    now = time.time()
    data = _cache["data"] if now - _cache["at"] < _TTL_S else None
    if data is None:
        url = os.environ.get("WINDY_PLANS_URL", DEFAULT_PLANS_URL)
        try:
            resp = httpx.get(url, timeout=10.0)
            data = resp.json() if resp.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("plans"), list) or not data["plans"]:
            logger.info("windy_plans: plans unavailable from %s", url)
            return {"status": "unavailable", "see_plans_url": FALLBACK_PAGE,
                    "error": ("I couldn't load Windy's current plans just now, so I won't guess "
                              f"prices. They're on {FALLBACK_PAGE}.")}
        _cache.update(at=now, data=data)
    page = str(data.get("see_plans_url") or FALLBACK_PAGE)
    return {"status": "ok", "currency": str(data.get("currency") or "usd").upper(),
            "plans": [_line(p) for p in data["plans"] if isinstance(p, dict)],
            "see_plans_url": page,
            "note": "Quote these lines exactly; for anything not listed, point to see_plans_url."}


def register_windy_plans_tool(registry: ToolRegistry) -> None:
    registry.register(
        name="windy_plans",
        description=(
            "Windy's CURRENT plans and prices, read live from Windy. Call this whenever "
            "someone asks what Windy costs, which plans exist or what a plan includes. "
            "Quote only what it returns; never state a Windy price from memory."
        ),
        parameters={"type": "object", "properties": {}, "required": []},
        fn=windy_plans,
    )
