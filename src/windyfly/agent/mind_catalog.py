"""The models Windy Mind lets THIS agent use, from GET /v1/catalog/me (Mind plan v2.1 S6.9, C6).

The ``/model`` menu used to be a hand-kept list that fell behind every release ("haiku" still meant
4.5 after Haiku 5.5 shipped). Mind's per-caller catalog is the one source: what this agent's token,
its owner's keys and terms allow, each with ``available_now``. Any failure returns None and the
caller falls back to the hand list, so a down Mind never breaks ``/model``.

Read with the agent's own token, cached for Mind's ``max-age`` (60 s). Never raises.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from windyfly.agent import mind_auth, mind_self

logger = logging.getLogger(__name__)

CACHE_S = 60.0
_TIMEOUT_S = 3.0

_lock = threading.Lock()
_cache: dict[str, Any] = {"models": None, "at": 0.0}


def _reset_for_tests() -> None:
    with _lock:
        _cache.update({"models": None, "at": 0.0})


def my_catalog() -> list[dict[str, Any]] | None:
    """Mind's catalog entries for this agent (id, display_name, available_now, ...), else None."""
    if not mind_self.enabled() or not mind_self._ept():
        return None
    now = time.time()
    with _lock:
        if _cache["at"] and now - _cache["at"] < CACHE_S:
            return _cache["models"]
    models: list[dict[str, Any]] | None = None
    try:
        resp = mind_auth.request("GET", f"{mind_self._base()}/v1/catalog/me", mind_self._ept(), None, _TIMEOUT_S, None)
        if resp.status_code == 200:
            data = (resp.json() or {}).get("data")
            if isinstance(data, list):
                models = [m for m in data if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"]]
    except Exception as exc:  # noqa: BLE001
        logger.debug("mind_catalog: read failed: %s", type(exc).__name__)
    with _lock:
        _cache.update({"models": models, "at": now})  # a failure is cached too: no timeout on every /model
    return models


_FAMILIES = ("opus", "sonnet", "haiku")


def resolve(word: str, catalog: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match what the owner typed against the catalog.

    An exact id or display name wins. A family word (opus, sonnet, haiku) means the strongest
    model of that family that is available now, so "haiku" follows the newest Haiku Mind offers.
    """
    w = (word or "").strip().lower()
    if not w:
        return None
    for m in catalog:
        if w == m["id"].lower() or w == str(m.get("display_name") or "").strip().lower():
            return m
    if w in _FAMILIES:
        fam = [m for m in catalog if w in m["id"].lower() and m.get("available_now", True)]
        if fam:
            return max(fam, key=lambda m: m.get("power_rating") or 0)
    return None
