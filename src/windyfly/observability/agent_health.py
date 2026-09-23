"""Field visibility for a running fly: boot, health, failed turns, demotions.

Four event families, all declared at Windy Admin (windy-admin #290):

* ``service.boot``: once per process.
* ``service.health``: every 15 minutes, from one in-process daemon timer.
  Counts cover the interval since the last row. A count the process can't
  know is left out, never sent as a fake 0.
* ``agent.run_failed``: one row per turn where the human got no real answer
  (every provider failed, the lifeboat answered, the turn crashed).
* ``agent.model_demoted``: once per transition when the agent quietly falls
  back to a weaker or local model. A steady demoted state doesn't repeat;
  recovery then a fresh demotion emits again.

The ingest QUARANTINES a whole row when a required key is missing or an
enum value is outside the declared set, so the sets are vendored here and
rows are validated before they leave: an invalid row is dropped and counted
in ``telemetry_dropped``, never sent.

Every row goes through ``admin_telemetry`` (auth, opt-out, synthetic stamp,
the 202 body check). Privacy: ids, codes, counts and durations only.
"""

from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── declared sets (windy-admin 36b3970) ──────────────────────────────

RUN_FAILED_CODES = frozenset({
    "provider_http", "auth", "rate_limited", "quota_exceeded", "timeout",
    "network", "no_provider", "tool_error", "context_overflow", "lifeboat",
    "internal",
})
RUN_FAILED_STAGES = frozenset({"llm", "tool", "channel_send", "memory"})
DEMOTION_REASONS = frozenset({
    "credential_missing", "credential_rejected", "provider_unreachable",
    "quota_exceeded", "rate_limited",
})
INSTALL_KINDS = frozenset({"pip", "checkout"})

HEALTH_INTERVAL_S = 15 * 60

# Metadata key names the ingest treats as content (and rejects).
_CONTENT_WORDS = ("text", "body", "message", "email", "prompt")


# ── interval counters ────────────────────────────────────────────────

_lock = threading.Lock()


class _Interval:
    def __init__(self) -> None:
        self.started = time.monotonic()
        self.turns = 0
        self.turn_errors = 0
        self.tool_calls = 0
        self.tool_errors = 0
        self.retries_429 = 0
        self.lifeboat_turns = 0
        self.turn_ms: list[int] = []
        self.quarantined = 0
        self.dropped = 0


_iv = _Interval()
_demoted: tuple[str, str] | None = None
_started = False
_write_queue: Any = None


def _reset_for_tests() -> None:
    global _iv, _demoted, _started, _write_queue
    from windyfly.observability import admin_telemetry

    admin_telemetry._reset_for_tests()
    with _lock:
        _iv = _Interval()
        _demoted = None
        _started = False
        _write_queue = None


def note_tool_call(ok: bool) -> None:
    with _lock:
        _iv.tool_calls += 1
        if not ok:
            _iv.tool_errors += 1


def note_llm_record(record: dict[str, Any]) -> None:
    """Every per-call record from ``models.call_llm`` passes through here."""
    if record.get("status") == "failed" and record.get("error_code") == "rate_limited":
        with _lock:
            _iv.retries_429 += 1


def note_ingest_result(quarantined: int) -> None:
    if quarantined:
        with _lock:
            _iv.quarantined += int(quarantined)


def note_dropped(n: int = 1) -> None:
    from windyfly.observability import admin_telemetry as at

    if not at.configured():
        return  # no send path: nothing was going to be sent, nothing to count
    with _lock:
        _iv.dropped += n


def _p95(values: list[int]) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, int(round(0.95 * len(ordered))) - 1)
    return ordered[idx]


# ── row building + validation ────────────────────────────────────────

def _valid_keys(metadata: dict[str, Any]) -> bool:
    return not any(w in k.lower() for k in metadata for w in _CONTENT_WORDS)


def _emit(event_type: str, metadata: dict[str, Any], write_queue: Any = None,
          **top: Any) -> dict | None:
    """Build, validate and queue one row. Returns the row (for tests) or None."""
    from windyfly.observability import admin_telemetry as at

    if not at.enabled():
        return None
    passport = at.own_passport()
    if not passport:
        return None
    if not _valid_keys(metadata):
        logger.warning("telemetry: %s dropped (content-like metadata key)", event_type)
        note_dropped()
        return None
    event: dict[str, Any] = {
        "ts": datetime.now(UTC).isoformat(),
        "platform": "windy-agent",
        "service": "fly",
        "event_type": event_type,
        "actor_type": "agent",
        "actor_id": passport,
        "metadata": {k: v for k, v in metadata.items() if v is not None},
    }
    for k, v in top.items():
        if v is not None:
            event[k] = v
    wq = write_queue if write_queue is not None else _write_queue
    at.send_event(wq, event)
    return event


def emit_run_failed(code: str, *, write_queue: Any = None, stage: str | None = None,
                    http_status: int | None = None, attempts: int | None = None,
                    channel: str | None = None, model: str | None = None,
                    provider: str | None = None, duration_ms: int | None = None) -> dict | None:
    if code not in RUN_FAILED_CODES or (stage is not None and stage not in RUN_FAILED_STAGES):
        logger.warning("telemetry: agent.run_failed dropped (code=%r stage=%r not declared)",
                       code, stage)
        note_dropped()
        return None
    return _emit("agent.run_failed", {
        "code": code, "stage": stage,
        "http_status": int(http_status) if isinstance(http_status, int) else None,
        "attempts": attempts, "channel": (channel or None) and str(channel)[:16],
    }, write_queue, model=model, provider=provider, duration_ms=duration_ms)


def note_demotion(from_model: str, to_model: str, reason: str, *,
                  provider: str | None = None, http_status: int | None = None,
                  write_queue: Any = None) -> dict | None:
    """Record that the agent is answering on ``to_model`` instead of
    ``from_model``. Emits only on a transition."""
    global _demoted
    if not from_model or not to_model or from_model == to_model:
        return None
    with _lock:
        if _demoted == (from_model, to_model):
            return None
        _demoted = (from_model, to_model)
    if reason not in DEMOTION_REASONS:
        logger.warning("telemetry: agent.model_demoted dropped (reason=%r not declared)", reason)
        note_dropped()
        return None
    return _emit("agent.model_demoted", {
        "from_model": from_model, "to_model": to_model, "reason": reason,
        "http_status": int(http_status) if isinstance(http_status, int) else None,
    }, write_queue, provider=provider)


def note_recovered() -> None:
    """The agent answered on its primary route again."""
    global _demoted
    with _lock:
        _demoted = None


def is_demoted() -> bool:
    return _demoted is not None


def demotion_reason(error_code: str | None, detail: str = "") -> str:
    """Map a call failure (``models._error_code`` codes, or a chain skip) to a
    declared demotion reason."""
    d = (detail or "").lower()
    if "no-key" in d:
        return "credential_missing"
    if "credit balance" in d or "quota" in d or error_code == "quota_exceeded":
        return "quota_exceeded"
    if error_code == "auth":
        return "credential_rejected"
    if error_code == "rate_limited":
        return "rate_limited"
    return "provider_unreachable"


def failure_code(detail: str) -> str:
    """Classify a whole-chain failure message into a run_failed code."""
    d = (detail or "").lower()
    if "attempted=[]" in d and ("no-key" in d or "cooldown" in d):
        return "no_provider"
    if "credit balance" in d or "quota" in d:
        return "quota_exceeded"
    if "429" in d or "rate limit" in d or "rate_limit" in d:
        return "rate_limited"
    if "401" in d or "403" in d or "authentication" in d or "invalid x-api-key" in d:
        return "auth"
    if "context" in d and ("length" in d or "too long" in d or "window" in d):
        return "context_overflow"
    if "timeout" in d or "timed out" in d:
        return "timeout"
    if "connect" in d or "network" in d or "unreachable" in d:
        return "network"
    return "provider_http"


# ── per-turn tracking ────────────────────────────────────────────────

_turn_failure: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "windy_turn_failure", default=None,
)


def mark_turn_failed(code: str, *, stage: str | None = "llm", lifeboat: bool = False,
                     **fields: Any) -> None:
    """Called on a return path where the human gets no real answer. The
    enclosing ``turn()`` emits exactly one ``agent.run_failed`` for it."""
    _turn_failure.set({"code": code, "stage": stage, "lifeboat": lifeboat, **fields})


@contextlib.contextmanager
def turn(write_queue: Any = None) -> Iterator[None]:
    token = _turn_failure.set(None)
    started = time.monotonic()
    failure: dict[str, Any] | None = None
    try:
        yield
        failure = _turn_failure.get()
    except Exception:
        failure = {"code": "internal", "stage": None}
        raise
    finally:
        ms = int((time.monotonic() - started) * 1000)
        with _lock:
            _iv.turns += 1
            _iv.turn_ms.append(ms)
            if failure:
                _iv.turn_errors += 1
                if failure.get("lifeboat"):
                    _iv.lifeboat_turns += 1
        _turn_failure.reset(token)
        if failure:
            f = dict(failure)
            f.pop("lifeboat", None)
            code = f.pop("code")
            try:
                emit_run_failed(code, write_queue=write_queue, duration_ms=ms, **f)
            except Exception as e:  # noqa: BLE001 — telemetry never breaks a turn
                logger.debug("run_failed emit failed: %s", e)


# ── boot + health ────────────────────────────────────────────────────

def _install_kind() -> str:
    try:
        from windyfly.platform import get_project_root

        return "checkout" if (get_project_root() / ".git").exists() else "pip"
    except Exception:  # noqa: BLE001
        return "pip"


def health_row(write_queue: Any = None) -> dict | None:
    """Emit one service.health row for the interval so far, then reset."""
    global _iv
    with _lock:
        iv, _iv = _iv, _Interval()
        degraded = _demoted is not None
    return _emit("service.health", {
        "interval_s": int(time.monotonic() - iv.started),
        "turns": iv.turns,
        "turn_errors": iv.turn_errors,
        "tool_calls": iv.tool_calls,
        "tool_errors": iv.tool_errors,
        "retries_429": iv.retries_429,
        "p95_turn_ms": _p95(iv.turn_ms),
        "lifeboat_turns": iv.lifeboat_turns,
        "degraded": degraded,
        "telemetry_quarantined": iv.quarantined,
        "telemetry_dropped": iv.dropped,
    }, write_queue)


def start(write_queue: Any, *, channel: str | None = None) -> None:
    """Once per process: the boot row + the health timer. Idempotent."""
    global _started, _write_queue
    from windyfly.observability import admin_telemetry as at
    from windyfly.observability import synthetic

    synthetic.install()
    with _lock:
        if _started:
            return
        _started = True
        _write_queue = write_queue
    if not at.enabled():
        return
    from windyfly import __version__

    _emit("service.boot", {
        "version": __version__,
        "channel_count": 1 if channel else None,
        "install": _install_kind(),
    }, write_queue)
    interval = float(os.environ.get("WINDY_HEALTH_INTERVAL_S") or HEALTH_INTERVAL_S)

    def _loop() -> None:
        while True:
            time.sleep(interval)
            try:
                health_row()
            except Exception as e:  # noqa: BLE001
                logger.debug("health row failed: %s", e)

    threading.Thread(target=_loop, name="windy-health", daemon=True).start()
