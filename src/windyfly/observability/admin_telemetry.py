"""Fire-and-forget telemetry to Windy Admin (ADR-WA-001).

The fly's LLM turns are the last un-ledgered burn point in the
ecosystem's cost ledger — Windy 0-class flies talk to Anthropic
directly on the house subscription token, invisible to the dashboard
until now. One `llm.call` envelope per completed turn (summed tokens
across retries/follow-ups, the same totals the local cost log gets).

Delivery rides the existing WriteQueue at LOW priority so a turn never
waits on telemetry; the POST itself has a 3s timeout and swallows
every error. Inert unless WINDY_ADMIN_INGEST_URL +
WINDY_ADMIN_INGEST_TOKEN are set in the agent's env.

Privacy hard line (ADR-WA-001 §4): counts, costs, durations, models
only — never message content. The ingest 422s content-like metadata.
"""

from __future__ import annotations

import base64
import json
import logging
import os
from datetime import UTC, datetime

from windyfly.memory.write_queue import Priority, WriteQueue

logger = logging.getLogger(__name__)

_passport_cache: str | None = None


def _own_passport() -> str | None:
    """The fly's passport, from WINDY_AGENT_PASSPORT or the EPT's `sub`
    claim (unverified decode — this is self-identification for a
    telemetry row, not auth; the ingest attributes the row to the
    service token regardless)."""
    global _passport_cache
    if _passport_cache:
        return _passport_cache
    explicit = os.environ.get("WINDY_AGENT_PASSPORT")
    if explicit:
        _passport_cache = explicit
        return explicit
    ept = os.environ.get("WINDY_PASSPORT_EPT", "")
    try:
        payload = ept.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub")
        if sub:
            _passport_cache = sub
            return sub
    except Exception:  # noqa: BLE001 — no passport just means no emit
        pass
    return None


def _configured() -> bool:
    return bool(
        os.environ.get("WINDY_ADMIN_INGEST_URL")
        and os.environ.get("WINDY_ADMIN_INGEST_TOKEN")
    )


def _post_event(event: dict) -> None:
    """Runs on the write queue — blocking httpx is fine there."""
    import httpx

    try:
        resp = httpx.post(
            f"{os.environ['WINDY_ADMIN_INGEST_URL'].rstrip('/')}/v1/events",
            json={"events": [event]},
            headers={
                "Authorization": f"Bearer {os.environ['WINDY_ADMIN_INGEST_TOKEN']}"
            },
            timeout=3.0,
        )
        if resp.status_code != 202:
            logger.debug("admin telemetry ingest returned %s", resp.status_code)
    except Exception as e:  # noqa: BLE001 — telemetry never raises
        logger.debug("admin telemetry post failed: %s", e)


def build_llm_call_event(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
    session_id: str | None,
    had_tool_calls: bool,
    duration_ms: int | None = None,
) -> dict | None:
    """The envelope, or None when unconfigured / passport unknown."""
    if not _configured():
        return None
    passport = _own_passport()
    if not passport:
        return None
    provider = "anthropic" if model.startswith("claude") else None
    return {
        "ts": datetime.now(UTC).isoformat(),
        "platform": "windy-agent",
        "service": "fly",
        "event_type": "llm.call",
        "actor_type": "agent",
        "actor_id": passport,
        "model": model,
        "provider": provider,
        "tokens_in": int(input_tokens),
        "tokens_out": int(output_tokens),
        "cost_microcents": max(0, int(round(cost_usd * 1_000_000))),
        "duration_ms": duration_ms,
        "session_id": session_id,
        "metadata": {"had_tool_calls": bool(had_tool_calls)},
    }


def emit_llm_call(write_queue: WriteQueue, **kwargs) -> None:
    """Queue one llm.call envelope; a no-op unless configured."""
    event = build_llm_call_event(**kwargs)
    if event is None:
        return
    try:
        write_queue.enqueue(Priority.LOW, _post_event, event)
    except Exception as e:  # noqa: BLE001
        logger.debug("admin telemetry enqueue failed: %s", e)
