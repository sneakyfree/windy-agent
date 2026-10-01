"""Sync bridge to the vendored Windy Mind helper (MD14). OFF unless WINDY_MIND_HELPER=1.

The helper (``windyfly._vendor.mind_client``) is async and owns ONE transient
retry, the signed route table, the floors and the switched-off list. Turns run
on worker threads (agent/executor.py), so this module keeps ONE long-lived
helper on its own event-loop thread and submits calls to it: the route-table
cache survives between calls and no loop is created per call.

Floors: the standby comes from the route table (dark until Mind enables it).
No local floor is passed yet, so a Mind outage raises MindUnavailableError and
the caller falls through to the existing direct chain / Ollama lifeboat, which
already marks its replies.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from windyfly._vendor.mind_client import (  # noqa: F401  (re-exported for callers)
    MindClient,
    MindRefusedError,
    MindResult,
    MindUnavailableError,
)

RETRY_AFTER_CAP_S = 10.0
READ_TIMEOUT_S = 60.0  # covers the 8192-token truncation retry

_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None
_client: MindClient | None = None
_base_url = ""
_token = ""


def enabled() -> bool:
    return os.environ.get("WINDY_MIND_HELPER", "") == "1"


def _ensure(base_url: str, transport: Any = None) -> tuple[asyncio.AbstractEventLoop, MindClient]:
    global _loop, _client, _base_url
    with _lock:
        if _loop is None:
            _loop = asyncio.new_event_loop()
            threading.Thread(target=_loop.run_forever, name="mind-helper", daemon=True).start()
        if _client is None or _base_url != base_url:
            _base_url = base_url
            _client = MindClient(
                base_url, token=lambda: _token,
                read_timeout=READ_TIMEOUT_S, transport=transport,
            )
        return _loop, _client


def chat(base_url: str, ept: str, body: dict[str, Any], *, transport: Any = None) -> MindResult:
    """One helper call from a worker thread. Raises MindRefusedError (a 4xx/500 wall)
    or MindUnavailableError (Mind down/slow and no floor worked)."""
    global _token
    _token = ept
    loop, client = _ensure(base_url, transport)
    return asyncio.run_coroutine_threadsafe(client.chat(body), loop).result(
        timeout=READ_TIMEOUT_S * 2 + 10
    )


def _reset_for_tests() -> None:
    global _client, _base_url, _token
    with _lock:
        _client = None
        _base_url = ""
        _token = ""


class JsonShim:
    """Just enough of an httpx.Response for _translate_mind_response."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def json(self) -> dict[str, Any]:
        return self._data
