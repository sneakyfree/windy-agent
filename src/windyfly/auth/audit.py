"""Audit log for every wk_ bot-key use.

Append-only JSONL at data/audit/bot_key_usage.jsonl. One record per
outbound ecosystem call made with a bot key.

Schema:
    {
        "timestamp": "2026-04-16T13:22:05.123+00:00",
        "key_id": "wbk_01HXXX...",
        "scope_used": "cloud:upload",
        "target_url": "https://cloud.windy.test/api/v1/archive/agent",
        "response_status": 201,
        "latency_ms": 184
    }

The log is local to the agent. The account-server keeps its own
system-of-record — this file exists so an operator can inspect what
the agent did without a round-trip.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
AUDIT_LOG_PATH = PROJECT_ROOT / "data" / "audit" / "bot_key_usage.jsonl"

_lock = threading.Lock()


def _resolve_path() -> Path:
    """Resolve the audit log path, honouring WINDYFLY_AUDIT_LOG if set."""
    override = os.environ.get("WINDYFLY_AUDIT_LOG", "")
    return Path(override) if override else AUDIT_LOG_PATH


def log_bot_key_use(
    *,
    key_id: str,
    scope_used: str,
    target_url: str,
    response_status: int | None,
    latency_ms: float,
) -> None:
    """Append one record to the audit log. Never raises."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "key_id": key_id or "",
        "scope_used": scope_used or "",
        "target_url": target_url or "",
        "response_status": response_status,
        "latency_ms": round(float(latency_ms), 2),
    }
    path = _resolve_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
    except OSError as exc:
        logger.debug("Audit log write failed: %s", exc)


@contextmanager
def audit_bot_key_call(
    *,
    key_id: str,
    scope_used: str,
    target_url: str,
) -> Iterator[dict]:
    """Context manager that records a wk_-authenticated HTTP call.

    Usage:
        with audit_bot_key_call(key_id=..., scope_used=..., target_url=...) as ctx:
            resp = await client.post(...)
            ctx["response_status"] = resp.status_code

    On exit the record is written even if the body raised — status is
    left None for network failures, which is exactly the signal an
    operator wants to see.
    """
    ctx: dict = {"response_status": None}
    start = time.perf_counter()
    try:
        yield ctx
    finally:
        latency = (time.perf_counter() - start) * 1000
        log_bot_key_use(
            key_id=key_id,
            scope_used=scope_used,
            target_url=target_url,
            response_status=ctx.get("response_status"),
            latency_ms=latency,
        )


async def audited_post(
    client: httpx.AsyncClient,
    url: str,
    *,
    key_id: str,
    scope_used: str,
    **kwargs,
) -> httpx.Response:
    """Small helper: httpx POST with automatic audit logging."""
    with audit_bot_key_call(key_id=key_id, scope_used=scope_used, target_url=url) as ctx:
        resp = await client.post(url, **kwargs)
        ctx["response_status"] = resp.status_code
        return resp


async def audited_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    key_id: str,
    scope_used: str,
    **kwargs,
) -> httpx.Response:
    """Small helper: httpx GET with automatic audit logging."""
    with audit_bot_key_call(key_id=key_id, scope_used=scope_used, target_url=url) as ctx:
        resp = await client.get(url, **kwargs)
        ctx["response_status"] = resp.status_code
        return resp
