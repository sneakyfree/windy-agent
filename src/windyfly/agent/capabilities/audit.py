"""Audit hook installer — wires the CapabilityRegistry to the
agent_actions ledger.

The hooks correlate pre and post invoke calls via contextvars (one
context per asyncio task), so concurrent capability invocations don't
collide on action ids. Capabilities with ``audit_required=False``
(the default for Tier 0 — pure compute) skip the ledger entirely so
``dice.roll`` doesn't fill the table.

Args are JSON-serialized and passed through ``observability.redact``
before storage so any secret accidentally embedded in a tool call
gets masked the same way it would in logs.

The hook adds two writes per invocation (start + end), both at HIGH
priority so they bypass the medium-batch queue. End writes happen in
the registry's ``finally`` block so a handler raise still records the
failure.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from windyfly.agent.capabilities.descriptor import Band, Capability
from windyfly.agent.capabilities.registry import CapabilityRegistry
from windyfly.memory.agent_actions import (
    record_action_end,
    record_action_start,
)
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

logger = logging.getLogger(__name__)


# Inline redaction for the audit args path. Wave 1 #51's
# observability.redact lives on a different branch lineage; once both
# land in master we can consolidate into one shared redactor. The
# patterns here intentionally mirror that file so behavior matches.
_TELEGRAM_TOKEN_RE = re.compile(
    r"(bot\d{6,}:[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{20,}"
)
_API_KEY_RE = re.compile(r"\b(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]{20,}")
_WK_KEY_RE = re.compile(r"\b(wk[_-][A-Za-z0-9_-]{4})[A-Za-z0-9_-]{16,}")
_ZAI_KEY_RE = re.compile(r"\b([0-9a-f]{8})[0-9a-f]{24}\.[A-Za-z0-9]{16,}")
_BEARER_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9_-]{4})[A-Za-z0-9._-]{16,}", re.IGNORECASE,
)


def _redact(text: str) -> str:
    text = _TELEGRAM_TOKEN_RE.sub(r"\1***REDACTED***", text)
    text = _API_KEY_RE.sub(r"\1***REDACTED***", text)
    text = _WK_KEY_RE.sub(r"\1***REDACTED***", text)
    text = _ZAI_KEY_RE.sub(r"\1***REDACTED***", text)
    text = _BEARER_RE.sub(r"\1***REDACTED***", text)
    return text

# Per-task correlation between pre and post hooks. Contextvars are
# preserved across awaits within the same asyncio task and isolated
# across concurrent tasks — exactly what we need.
_current_action_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "capability_action_id", default=None,
)
_current_action_started: contextvars.ContextVar[float] = contextvars.ContextVar(
    "capability_action_started", default=0.0,
)

# Wave 14b session-id propagation. ``agent_respond`` calls
# :func:`set_current_session_id` at entry; the audit hooks read it via
# the default :func:`get_current_session_id` provider so every
# ``agent_actions`` row carries the originating session_id.
#
# Without this, every ledger row had ``session_id IS NULL`` and there
# was no way to ask "which user request invoked this capability?" —
# the harness on 2026-04-26 surfaced the gap when correlating tool
# usage to test cases became impossible.
_current_session_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "capability_session_id", default=None,
)


def set_current_session_id(session_id: str | None) -> None:
    """Stamp the current task's session_id so audit rows carry it.

    Safe to call from anywhere — channel adapters and the agent loop
    both call it at the top of their per-message entrypoints.
    """
    _current_session_id.set(session_id)


def get_current_session_id() -> str | None:
    """Default ``session_id_provider`` used by :func:`install_audit_hooks`."""
    return _current_session_id.get()


def install_audit_hooks(
    registry: CapabilityRegistry,
    db: Database,
    write_queue: WriteQueue,
    *,
    session_id_provider: Callable[[], str | None] | None = None,
) -> None:
    """Register pre/post hooks on ``registry`` that write to agent_actions.

    Idempotent — calling more than once would register duplicate hooks,
    so the function checks for prior installs via a sentinel attribute
    and skips on re-call.

    Args:
        registry: the capability registry to instrument.
        db: SQLite database for the ledger.
        write_queue: priority write queue (HIGH priority for both
            start and end so they don't sit behind episode writes).
        session_id_provider: optional callable returning the current
            session id for inclusion in the ledger row. Defaults to
            :func:`get_current_session_id` which reads the contextvar
            populated by :func:`set_current_session_id` (called from
            ``agent_respond`` entry). Pass an explicit callable to
            override (e.g., tests, or non-agent_respond callers).
    """
    if session_id_provider is None:
        session_id_provider = get_current_session_id
    if getattr(registry, "_audit_installed", False):
        logger.debug("Audit hooks already installed on this registry")
        return

    def pre(cap: Capability, args: dict[str, Any], band: Band) -> None:
        if not cap.audit_required:
            return
        action_id = uuid.uuid4().hex
        _current_action_id.set(action_id)
        _current_action_started.set(time.time())
        try:
            args_json = _redact(json.dumps(args, default=_json_default))
        except (TypeError, ValueError) as e:
            logger.debug("audit args serialization fallback for %s: %s", cap.id, e)
            args_json = _redact(repr(args))
        session_id = session_id_provider() if session_id_provider else None
        record_action_start(
            db, write_queue,
            action_id=action_id,
            capability_id=cap.id,
            tier=int(cap.tier),
            band=band.name,
            sandbox_tier=cap.sandbox_tier or "none",
            args_json=args_json,
            started_at=_now_iso(),
            session_id=session_id,
        )

    def post(
        cap: Capability,
        args: dict[str, Any],
        band: Band,
        result: Any,
        error: Exception | None,
    ) -> None:
        if not cap.audit_required:
            return
        action_id = _current_action_id.get()
        if not action_id:
            # Pre didn't fire (cap.audit_required was False) or we lost
            # the contextvar somehow. Skip rather than write a garbage row.
            return
        started = _current_action_started.get()
        duration_ms = int((time.time() - started) * 1000) if started else 0
        record_action_end(
            db, write_queue,
            action_id=action_id,
            success=error is None,
            duration_ms=duration_ms,
            error_class=type(error).__name__ if error else None,
            error_message=_redact(str(error)) if error else None,
            ended_at=_now_iso(),
        )
        # Clear so a subsequent unrelated invocation (e.g., during
        # shutdown) can't accidentally update this row.
        _current_action_id.set(None)
        _current_action_started.set(0.0)

    registry.add_pre_invoke_hook(pre)
    registry.add_post_invoke_hook(post)
    registry._audit_installed = True  # sentinel for idempotency
    logger.info("Capability audit hooks installed")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_default(obj: Any) -> str:
    """Fallback serializer for dataclasses, paths, anything custom."""
    return repr(obj)
