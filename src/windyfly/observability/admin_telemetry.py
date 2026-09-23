"""Fire-and-forget telemetry to Windy Admin (ADR-WA-001).

The fly's LLM turns are the last un-ledgered burn point in the
ecosystem's cost ledger — Windy 0-class flies talk to Anthropic
directly on the house subscription token, invisible to the dashboard
until now. One `llm.call` envelope per successful LLM call (every path:
turn rounds, retries, the voice bridge, helper calls), the same records
the local cost ledger gets. It used to be one per turn from the loop
only, which missed helper calls entirely.

Rows are buffered and sent in batches (at most every ~10 s, 100 per
request) from a daemon thread, so a turn never waits on telemetry and a
busy agent stays far under the ingest's 30 requests/min per passport. Inert unless WINDY_ADMIN_INGEST_URL +
WINDY_ADMIN_INGEST_TOKEN are set, or the agent has its own passport
client token (see ``_ingest_target``). A customer install with neither
sends nothing. WINDY_TELEMETRY=0 turns it all off; a 401/403 from the
ingest trips a 24h breaker.

Privacy hard line (ADR-WA-001 §4): counts, costs, durations, models
only — never message content. The ingest 422s content-like metadata.
"""

from __future__ import annotations

import atexit
import base64
import json
import logging
import os
import threading
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

from windyfly.memory.write_queue import WriteQueue

logger = logging.getLogger(__name__)

_passport_cache: str | None = None

DEFAULT_INGEST_URL = "https://admin.windyword.ai"


def _claims_sub(token: str) -> str | None:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        sub = json.loads(base64.urlsafe_b64decode(payload)).get("sub")
        return str(sub) if sub else None
    except Exception:  # noqa: BLE001 — no passport just means no emit
        return None


def _own_passport() -> str | None:
    """The fly's passport: WINDY_AGENT_PASSPORT, ETERNITAS_PASSPORT, else the
    EPT's `sub` claim (unverified decode: this is self-identification for a
    telemetry row, not auth)."""
    global _passport_cache
    if _passport_cache:
        return _passport_cache
    for key in ("WINDY_AGENT_PASSPORT", "ETERNITAS_PASSPORT"):
        explicit = os.environ.get(key, "").strip()
        if explicit:
            _passport_cache = explicit
            return explicit
    for key in ("ETERNITAS_PASSPORT_TOKEN", "WINDY_PASSPORT_EPT"):
        sub = _claims_sub(os.environ.get(key, ""))
        if sub:
            _passport_cache = sub
            return sub
    return None


own_passport = _own_passport


def _ingest_target(*, require_consent: bool = True) -> tuple[str, str] | None:
    """(base URL, bearer) or None. Sources, in order:

    1. WINDY_ADMIN_INGEST_URL + WINDY_ADMIN_INGEST_TOKEN: a fleet box's
       named emitter token from the lockbox. Our own infrastructure, so it
       is exempt from the disclosure gate.
    2. WINDY_TELEMETRY_CLIENT_TOKEN: a public-client token (empty by
       default; nothing is embedded in the package).
    3. The agent's own passport token (EPT): the default for a customer
       install (windy-admin #294 verifies it and pins actor_id to its
       passport). WINDY_TELEMETRY_EPT_AUTH=0 turns this path off.

    (2) and (3) are customer paths: they open only after the user has been
    told what is sent (``disclosure.consented``). With nothing usable, a
    customer install sends nothing. Under pytest only (1) counts, so a
    developer's credentials never reach the real ingest from a test run.
    """
    url = os.environ.get("WINDY_ADMIN_INGEST_URL", "").strip()
    token = os.environ.get("WINDY_ADMIN_INGEST_TOKEN", "").strip()
    if url and token:
        return url, token
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return None
    base = url or DEFAULT_INGEST_URL
    candidate: tuple[str, str] | None = None
    client = os.environ.get("WINDY_TELEMETRY_CLIENT_TOKEN", "").strip()
    if client:
        candidate = (base, client)
    elif os.environ.get("WINDY_TELEMETRY_EPT_AUTH", "").strip() != "0":
        ept = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
        if ept and _claims_sub(ept):
            candidate = (base, ept)
    if candidate is None:
        return None
    if require_consent:
        from windyfly.observability import disclosure

        if not disclosure.consented():
            return None
    return candidate


def target_kind() -> str:
    """Which send path is in use: fleet, client, passport, or none."""
    target = _ingest_target()
    if target is None:
        return "none"
    if target[1] == os.environ.get("WINDY_ADMIN_INGEST_TOKEN", "").strip():
        return "fleet"
    if target[1] == os.environ.get("WINDY_TELEMETRY_CLIENT_TOKEN", "").strip():
        return "client"
    return "passport"


def breaker_until() -> float | None:
    """When the 24h breaker lifts (epoch seconds), or None if closed."""
    if not breaker_open():
        return None
    try:
        return _breaker_path().stat().st_mtime + BREAKER_HOLD_S
    except OSError:
        return time.time() + BREAKER_HOLD_S


# ── circuit breaker: the ingest refused this install's credentials ────

BREAKER_HOLD_S = 24 * 3600
_BREAKER_FILE = "telemetry_refused"
_breaker_tripped = False


def _breaker_path() -> Any:
    from windyfly.platform import windy_state_dir

    return windy_state_dir() / _BREAKER_FILE


def breaker_open() -> bool:
    """True while sends are suppressed: tripped in this process, or a
    marker younger than 24h from an earlier one."""
    if _breaker_tripped:
        return True
    try:
        p = _breaker_path()
        if p.exists():
            if time.time() - p.stat().st_mtime < BREAKER_HOLD_S:
                return True
            p.unlink(missing_ok=True)  # expired: try again
    except OSError:
        pass
    return False


def _trip_breaker(status: int) -> None:
    global _breaker_tripped
    if _breaker_tripped:
        return
    _breaker_tripped = True
    logger.warning(
        "telemetry ingest refused this install's credentials (HTTP %s); "
        "not sending for 24h", status,
    )
    try:
        p = _breaker_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(f"{int(time.time())} {status}\n")
        os.chmod(p, 0o600)
    except OSError:
        pass


def configured() -> bool:
    """A send path exists (consented, for customer paths) and telemetry
    isn't turned off (breaker aside)."""
    from windyfly.observability import disclosure

    if disclosure.opted_out():
        return False
    return _ingest_target() is not None


def enabled() -> bool:
    """Rows are built only when configured; while the breaker is open they
    are built and counted as dropped (see send_event), never sent."""
    return configured()


_configured = enabled


def _stamp(event: dict) -> dict:
    from windyfly.observability import synthetic

    if synthetic.active():
        event.setdefault("metadata", {})["synthetic"] = True
    return event


# ── batching ──────────────────────────────────────────────────────────
#
# The ingest allows 30 requests/min per passport and at most 100 events
# per request (413 above that). Rows are buffered and flushed at most every
# ~10 s, 100 per request, from one daemon thread, plus a best-effort flush
# at exit. The buffer is bounded: past MAX_BUFFER the oldest rows go and
# are counted as dropped.

MAX_BATCH = 100
MAX_BUFFER = 1000
FLUSH_INTERVAL_S = 10.0
_EXIT_FLUSH_TIMEOUT_S = 2.0

_buf: deque[dict] = deque()
_buf_lock = threading.Lock()
_flush_lock = threading.Lock()
_retry_at = 0.0
_flusher_started = False


def pending() -> list[dict]:
    """The rows waiting to be sent (a copy; for status and tests)."""
    with _buf_lock:
        return list(_buf)


def _reset_for_tests() -> None:
    global _retry_at, _breaker_tripped, _flusher_started
    with _buf_lock:
        _buf.clear()
    _retry_at = 0.0
    _breaker_tripped = False
    _flusher_started = False


def _bounded_extend_left(rows: list[dict]) -> None:
    """Put rows back at the front (a retry), dropping the OLDEST past the cap."""
    from windyfly.observability import agent_health

    with _buf_lock:
        _buf.extendleft(reversed(rows))
        overflow = len(_buf) - MAX_BUFFER
        for _ in range(max(0, overflow)):
            _buf.popleft()
    if overflow > 0:
        agent_health.note_dropped(overflow)


def _drop_all(extra: int = 0) -> None:
    from windyfly.observability import agent_health

    with _buf_lock:
        n = len(_buf)
        _buf.clear()
    if n + extra:
        agent_health.note_dropped(n + extra)


def _retry_after_s(resp: Any) -> float:
    try:
        return max(1.0, float(resp.headers.get("Retry-After", "")))
    except (TypeError, ValueError):
        return 60.0


def _refresh_own_ept() -> None:
    try:
        from windyfly.eternitas.ept_refresh import refresh_ept

        refresh_ept(force=True)
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry: EPT refresh failed: %s", e)


def _send_batch(batch: list[dict], timeout: float, *, refreshed: bool = False) -> str:
    """POST one batch. Returns "ok", "wait" (429: batch re-queued),
    "stop" (breaker tripped / nothing to send with) or "fail" (dropped)."""
    import httpx

    from windyfly.observability import agent_health

    global _retry_at
    target = _ingest_target()
    if target is None:
        return "stop"
    url, token = target
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/v1/events",
            json={"events": batch},
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
        )
    except Exception as e:  # noqa: BLE001 — telemetry never raises
        logger.debug("admin telemetry post failed: %s", e)
        agent_health.note_dropped(len(batch))
        return "fail"

    status = resp.status_code
    if status == 202:
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            return "ok"
        quarantined = int(body.get("quarantined") or 0) if isinstance(body, dict) else 0
        if quarantined:
            logger.warning(
                "telemetry: %d row(s) QUARANTINED by the ingest: %s",
                quarantined, body.get("rejections"),
            )
            agent_health.note_ingest_result(quarantined)
        return "ok"
    if status == 429:
        wait = _retry_after_s(resp)
        _retry_at = time.time() + wait
        logger.info("telemetry: ingest rate-limited; retrying in %.0fs", wait)
        _bounded_extend_left(batch)
        return "wait"
    if status == 413:
        # Can't happen while MAX_BATCH matches the ingest; a bug if it does.
        logger.warning("telemetry: ingest said 413 for %d rows; splitting", len(batch))
        if len(batch) == 1:
            agent_health.note_dropped(1)
            return "fail"
        mid = len(batch) // 2
        first = _send_batch(batch[:mid], timeout, refreshed=refreshed)
        if first != "ok":
            if first == "wait":
                _bounded_extend_left(batch[mid:])
            else:
                agent_health.note_dropped(len(batch) - mid)
            return first
        return _send_batch(batch[mid:], timeout, refreshed=refreshed)
    if status == 401:
        reason = _refusal_reason(resp)
        is_ept = token == os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
        renewable = "expired" in reason or "kid" in reason or "re-issued" in reason
        if is_ept and renewable and not refreshed:
            _refresh_own_ept()
            new = _ingest_target()
            if new is not None and new[1] != token:
                return _send_batch(batch, timeout, refreshed=True)
        _trip_breaker(status)
        _drop_all(len(batch))
        return "stop"
    if status == 403:
        _trip_breaker(status)
        _drop_all(len(batch))
        return "stop"
    logger.debug("admin telemetry ingest returned %s", status)
    agent_health.note_dropped(len(batch))
    return "fail"


def _refusal_reason(resp: Any) -> str:
    try:
        body = resp.json()
        detail = body.get("detail") or body.get("error") or body if isinstance(body, dict) else body
        return str(detail).lower()
    except Exception:  # noqa: BLE001
        return (getattr(resp, "text", "") or "").lower()


def flush(timeout: float = 3.0) -> None:
    """Send everything buffered, 100 rows per request, until the buffer is
    empty or the ingest says wait/stop. Never raises."""
    if not _flush_lock.acquire(blocking=False):
        return  # another flush is running
    try:
        while True:
            if breaker_open():
                _drop_all()
                return
            if time.time() < _retry_at:
                return
            with _buf_lock:
                batch = [_buf.popleft() for _ in range(min(MAX_BATCH, len(_buf)))]
            if not batch:
                return
            if _send_batch(batch, timeout) not in ("ok", "fail"):
                return
    except Exception as e:  # noqa: BLE001
        logger.debug("telemetry flush failed: %s", e)
    finally:
        _flush_lock.release()


def _ensure_flusher() -> None:
    """One daemon flusher + an exit flush per process (not under pytest,
    where tests call flush() themselves)."""
    global _flusher_started
    if _flusher_started or os.environ.get("PYTEST_CURRENT_TEST"):
        return
    _flusher_started = True

    def _loop() -> None:
        while True:
            time.sleep(FLUSH_INTERVAL_S)
            flush()

    threading.Thread(target=_loop, name="windy-telemetry", daemon=True).start()
    atexit.register(flush, _EXIT_FLUSH_TIMEOUT_S)


def send_event(write_queue: WriteQueue | None, event: dict) -> None:
    """Buffer one prepared row; it goes out with the next batch. Never
    blocks a turn. (``write_queue`` is kept for callers' signatures.)"""
    from windyfly.observability import agent_health

    if breaker_open():
        agent_health.note_dropped()
        return
    with _buf_lock:
        _buf.append(_stamp(event))
        overflow = len(_buf) - MAX_BUFFER
        for _ in range(max(0, overflow)):
            _buf.popleft()
    if overflow > 0:
        agent_health.note_dropped(overflow)
    _ensure_flusher()


def build_llm_call_event(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float | None,
    session_id: str | None,
    had_tool_calls: bool,
    duration_ms: int | None = None,
    provider: str | None = None,
    billing: str | None = None,
    cache_write_tokens: int = 0,
    cache_read_tokens: int = 0,
) -> dict | None:
    """The envelope, or None when unconfigured / passport unknown."""
    if not _configured():
        return None
    passport = _own_passport()
    if not passport:
        return None
    if provider is None:
        provider = "anthropic" if model.startswith("claude") else None
    metadata: dict = {"had_tool_calls": bool(had_tool_calls)}
    if billing:
        # "max_subscription" = list-price equivalent on a flat plan (the
        # marginal cost is $0); "metered" = really billed; "local" = free.
        metadata["billing"] = billing
    if cache_write_tokens:
        metadata["cache_write_tokens"] = int(cache_write_tokens)
    if cache_read_tokens:
        metadata["cache_read_tokens"] = int(cache_read_tokens)
    event = {
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
        "cost_microcents": (
            None if cost_usd is None else max(0, int(round(cost_usd * 1_000_000)))
        ),
        "duration_ms": duration_ms,
        "session_id": session_id,
        "metadata": metadata,
    }
    if event["cost_microcents"] is None:
        del event["cost_microcents"]  # unknown is absent, never 0
    return event


def emit_llm_call(write_queue: WriteQueue, **kwargs) -> None:
    """Queue one llm.call envelope; a no-op unless configured."""
    event = build_llm_call_event(**kwargs)
    if event is None:
        return
    send_event(write_queue, event)


def emit_command_invoked(
    write_queue: WriteQueue | None,
    command_name: str,
    platform: str,
) -> None:
    """Queue one command.invoked envelope; a no-op unless configured.

    Powers the data-driven de-bloat of the 131-command surface
    (Sprint 2.2): after 30 days of real usage counts, handlers nobody
    invokes get deleted by measurement, not opinion.

    Privacy hard line: the command NAME only — never args, never
    message content.
    """
    if not _configured() or write_queue is None:
        return
    passport = _own_passport()
    if not passport:
        return
    event = {
        "ts": datetime.now(UTC).isoformat(),
        "platform": "windy-agent",
        "service": "fly",
        "event_type": "command.invoked",
        "actor_type": "agent",
        "actor_id": passport,
        "session_id": None,
        "metadata": {"command": command_name[:32], "channel": platform[:16]},
    }
    send_event(write_queue, event)


_PROVIDER_NAMES = {"windy-mind": "windymind"}


def emit_llm_record(write_queue: WriteQueue, record: dict) -> None:
    """One ``llm.call`` for one successful per-call record from
    ``models.call_llm`` (failed calls stay in the local ledger)."""
    if record.get("status") != "ok":
        return
    provider = record.get("provider")
    emit_llm_call(
        write_queue,
        model=record.get("model") or "unknown",
        input_tokens=int(record.get("input_tokens") or 0),
        output_tokens=int(record.get("output_tokens") or 0),
        cost_usd=record.get("cost_usd"),
        session_id=record.get("session_id"),
        had_tool_calls=bool(record.get("had_tool_calls")),
        duration_ms=record.get("duration_ms"),
        provider=_PROVIDER_NAMES.get(provider, provider) if provider else None,
        billing=record.get("billing"),
        cache_write_tokens=int(record.get("cache_write_tokens") or 0),
        cache_read_tokens=int(record.get("cache_read_tokens") or 0),
    )
