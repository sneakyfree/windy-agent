"""Runtime claim invariant — Phase A.5 of ADR-051.

Calls Mind's runtime claim API (`api.windymind.ai/v1/runtime/...`) so
that two Windy Fly runtimes for the same agent don't both try to host
it simultaneously. First runtime to claim wins; the other detects the
409 and exits cleanly. The holder heartbeats every 30s to keep the TTL
fresh; on graceful shutdown it releases the slot.

Spec: kit-army-config/docs/phase-a2-runtime-claim-api-spec-2026-05-20.md
ADR-051 §"Phase A — A.5 (Single-runtime claim invariant wire-up in
runtime)" — acceptance criterion: "two Words on two Macs with same
user: only one active; other logs 'idle, another runtime holds claim'".

Architecture notes
------------------
* Uses **sync** httpx for the initial claim + the heartbeat loop. The
  brain's main loop runs its own asyncio in channels/{matrix,telegram,
  sms} contexts — sync code at startup + a separate daemon thread for
  heartbeats keeps this module orthogonal to the async loop chosen by
  each channel.
* Reads `ETERNITAS_PASSPORT` + a bearer (`ETERNITAS_PASSPORT_TOKEN`
  preferred, `WINDY_JWT` fallback) from the environment
  (existing conventions per hatching.py + ecosystem_health.py). When
  either is missing the claim is skipped — agents that haven't yet
  paired with the ecosystem keep running (fail-open).
* Fails OPEN on network / 5xx errors during the initial claim. The
  single-runtime invariant only matters when there's a peer competing
  for the slot; an unreachable Mind shouldn't block a lone agent from
  starting. A subsequent peer running with Mind reachable WILL get the
  fresh claim and supersede the failing runtime (which will see
  heartbeat 404s and exit).
* Fails CLOSED on a 409 Conflict — that's the one signal Mind sends
  when another runtime genuinely holds the slot. The local process
  logs the conflict + exits cleanly so its parent doesn't keep
  respawning a runtime that won't ever serve messages.
"""
from __future__ import annotations

import atexit
import enum
import logging
import os
import threading
import uuid
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Spec invariants — keep aligned with kit-army-config/docs/phase-a2-...
_TTL_SECONDS = 90
_HEARTBEAT_INTERVAL_S = 30
_CLAIM_TIMEOUT_S = 5.0
_RELEASE_TIMEOUT_S = 2.0  # release is best-effort; don't hold shutdown


class ClaimOutcome(str, enum.Enum):
    """Result of `acquire_runtime_slot`.

    The brain entry point dispatches on this:
      GRANTED   — proceed, start heartbeat
      CONFLICT  — another runtime holds the slot; exit cleanly
      DEGRADED  — Mind unreachable / 5xx; proceed but no claim discipline
      SKIPPED   — missing passport or JWT; proceed without contacting Mind
    """

    GRANTED = "granted"
    CONFLICT = "conflict"
    DEGRADED = "degraded"
    SKIPPED = "skipped"


@dataclass
class _ClaimState:
    """In-process state of the active claim."""

    passport: str
    runtime_id: str
    jwt: str
    base_url: str
    holder_summary: str = ""  # filled on CONFLICT for the log message
    heartbeat_thread: threading.Thread | None = None
    stop_event: threading.Event | None = None


# Module-level state. The brain process is one runtime; one claim;
# one heartbeat thread. Cleaned up on process exit via atexit.
_state: _ClaimState | None = None


def _mind_base_url() -> str:
    """Resolve the Mind base URL. Defaults to the canonical production
    host; an env-var override exists for tests + dev environments."""
    return os.environ.get("MIND_BASE_URL", "https://api.windymind.ai").rstrip("/")


def _read_creds() -> tuple[str, str] | None:
    """Return (passport, bearer) if both are set; None otherwise.

    Bearer preference (2026-07-05, one-soul drill finding): the agent's
    own ETERNITAS_PASSPORT_TOKEN first — the keyless grandma path has
    no WINDY_JWT, and Mind's claim endpoint verifies EPTs directly.
    Requiring WINDY_JWT meant the exact agents one-soul was built for
    never claimed their slot, so the midwife never yielded to them.
    WINDY_JWT stays as the fallback for JWT-authed setups.
    """
    passport = os.environ.get("ETERNITAS_PASSPORT", "").strip()
    bearer = (
        os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
        or os.environ.get("WINDY_JWT", "").strip()
    )
    # Keyless grandma path (2026-07-05 drill finding): the hatch writes the
    # EPT into ETERNITAS_PASSPORT_TOKEN but only writes ETERNITAS_PASSPORT
    # (the bare id) when a REAL Eternitas provisioning succeeds. The exact
    # keyless agents one-soul is for often have the token but not the id, so
    # the claim was silently skipped and the midwife never yielded → double
    # replies. The EPT's `sub` claim IS the passport id, so recover it from
    # the token when the env var is absent.
    if not passport and bearer:
        passport = _passport_from_ept(bearer)
    if not passport or not bearer:
        return None
    return passport, bearer


def _passport_from_ept(token: str) -> str:
    """Best-effort passport id from an EPT's `sub` claim (no verification).

    We only need the id string to name the runtime slot; Mind verifies the
    token's signature server-side. Returns "" on any malformed input.
    """
    try:
        import base64
        import json

        parts = token.split(".")
        if len(parts) != 3:
            return ""
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode()))
        sub = claims.get("sub", "")
        return sub.strip() if isinstance(sub, str) else ""
    except Exception:
        return ""


def _hostname() -> str:
    """Free-form host identifier surfaced to UI when claims conflict
    ("running on macbook-pro-grant.local"). os.uname() works on Unix +
    macOS; fall back to socket.gethostname() for Windows."""
    try:
        return os.uname().nodename
    except AttributeError:
        import socket

        return socket.gethostname()


def _windyfly_version() -> str:
    """Version string sent with the claim. Cheap import; isolated so
    tests can stub it."""
    try:
        from windyfly import __version__

        return f"windyfly/{__version__}"
    except Exception:
        return "windyfly/unknown"


def acquire_runtime_slot(
    *,
    source: str = "cli",
    transport: httpx.BaseTransport | None = None,
) -> ClaimOutcome:
    """Try to claim the runtime slot for this process's agent.

    Reads ETERNITAS_PASSPORT + WINDY_JWT from the environment, generates
    a UUID runtime_id, and POSTs to /v1/runtime/claim. Returns one of
    the ClaimOutcome values; on GRANTED, the module retains the claim
    state so `start_heartbeat()` and the atexit release can find it.

    `transport` is only set in tests (httpx.MockTransport); production
    callers leave it None.
    """
    global _state
    creds = _read_creds()
    if creds is None:
        logger.warning(
            "runtime_claim.skipped: ETERNITAS_PASSPORT or WINDY_JWT not set; "
            "single-runtime discipline disabled for this process"
        )
        return ClaimOutcome.SKIPPED

    passport, jwt = creds
    runtime_id = str(uuid.uuid4())
    base = _mind_base_url()

    payload = {
        "passport": passport,
        "runtime_id": runtime_id,
        "source": source,
        "host": _hostname(),
        "version": _windyfly_version(),
    }

    try:
        with httpx.Client(
            base_url=base,
            timeout=_CLAIM_TIMEOUT_S,
            transport=transport,
            headers={"Authorization": f"Bearer {jwt}"},
        ) as client:
            resp = client.post("/v1/runtime/claim", json=payload)
    except httpx.RequestError as e:
        logger.warning(
            "runtime_claim.network_error: %s (proceeding without claim discipline)",
            e,
        )
        return ClaimOutcome.DEGRADED

    if resp.status_code == 200:
        body = _safe_json(resp)
        ttl = body.get("ttl_seconds", _TTL_SECONDS)
        _state = _ClaimState(
            passport=passport,
            runtime_id=runtime_id,
            jwt=jwt,
            base_url=base,
        )
        logger.info(
            "runtime_claim.granted: passport=%s runtime_id=%s ttl=%ss",
            passport,
            runtime_id[:8],
            ttl,
        )
        return ClaimOutcome.GRANTED

    if resp.status_code in (202, 409):
        # 202 = warm-pool yield in progress; treated as CONFLICT here
        # since this is the CLI runtime, not a pool worker — Phase B
        # warm pool can poll-retry, but the CLI should just exit and
        # let the user understand what's running where.
        body = _safe_json(resp)
        # FastAPI wraps the conflict response inside `detail`.
        detail = body.get("detail", body)
        holder = detail.get("holder", {}) if isinstance(detail, dict) else {}
        holder_summary = (
            f"source={holder.get('source','?')} host={holder.get('host','?')} "
            f"claimed_at={holder.get('claimed_at','?')}"
        )
        _state = _ClaimState(
            passport=passport,
            runtime_id=runtime_id,
            jwt=jwt,
            base_url=base,
            holder_summary=holder_summary,
        )
        logger.warning(
            "runtime_claim.conflict: another runtime holds the slot for %s "
            "(%s) — exiting idle",
            passport,
            holder_summary,
        )
        return ClaimOutcome.CONFLICT

    # 401/403 = JWT issue / ownership denied; degrade rather than exit
    # so a misconfigured agent at least logs cleanly.
    # 5xx = Mind outage; same.
    logger.warning(
        "runtime_claim.unexpected_status: HTTP %d (proceeding without claim discipline) — body=%s",
        resp.status_code,
        resp.text[:200],
    )
    return ClaimOutcome.DEGRADED


def conflict_holder_summary() -> str:
    """Human-readable description of the runtime that won FCFS. Used by
    main() to print a friendly message before exiting on CONFLICT."""
    if _state is None:
        return ""
    return _state.holder_summary


def start_heartbeat() -> None:
    """Spawn the heartbeat daemon thread. Idempotent — second call is a
    no-op. Must be called AFTER `acquire_runtime_slot()` returned
    GRANTED. Returns immediately; the thread runs in the background.
    """
    if _state is None:
        logger.debug("runtime_claim.start_heartbeat: no active claim, skipping")
        return
    if _state.heartbeat_thread is not None and _state.heartbeat_thread.is_alive():
        return

    stop = threading.Event()
    _state.stop_event = stop
    t = threading.Thread(
        target=_heartbeat_loop,
        args=(_state, stop),
        name="windyfly-runtime-heartbeat",
        daemon=True,
    )
    _state.heartbeat_thread = t
    t.start()
    logger.debug("runtime_claim.heartbeat_started: every %ss", _HEARTBEAT_INTERVAL_S)


def _heartbeat_loop(state: _ClaimState, stop: threading.Event) -> None:
    """Background loop. Stops on `stop` event OR on a 404 from Mind
    (claim lost — e.g. TTL expired after a network partition)."""
    while not stop.wait(_HEARTBEAT_INTERVAL_S):
        try:
            with httpx.Client(
                base_url=state.base_url,
                timeout=_CLAIM_TIMEOUT_S,
                headers={"Authorization": f"Bearer {state.jwt}"},
            ) as client:
                resp = client.post(
                    "/v1/runtime/heartbeat",
                    json={
                        "passport": state.passport,
                        "runtime_id": state.runtime_id,
                    },
                )
        except httpx.RequestError as e:
            logger.warning("runtime_claim.heartbeat_network_error: %s", e)
            # Network blip — don't exit. Next iteration may succeed.
            continue

        if resp.status_code == 404:
            # Claim is gone. Either TTL-expired (we missed too many
            # heartbeats) or another runtime force-claimed via
            # admin-release in V2. Either way, this process is no
            # longer the holder; stop heartbeating + log.
            logger.warning(
                "runtime_claim.heartbeat_lost: 404 from Mind — claim is gone, "
                "this runtime is no longer the holder. Exiting heartbeat loop."
            )
            return

        if resp.status_code != 200:
            logger.warning(
                "runtime_claim.heartbeat_unexpected: HTTP %d body=%s",
                resp.status_code,
                resp.text[:150],
            )
            continue

        body = _safe_json(resp)
        if body.get("yield_requested"):
            # Spec §"Yield protocol detail": EXPLICIT runtimes (word,
            # cli) don't yield to warm-pool requesters in V1. The pool
            # only requests yield when an explicit runtime arrives —
            # not the other way around. So if a CLI runtime sees
            # yield_requested=true, something's wrong with the
            # source-priority logic upstream. Log noisily, don't act.
            logger.warning(
                "runtime_claim.yield_requested_unexpected: cli runtime received "
                "yield_requested=true; ignoring per spec source-priority"
            )


def release_slot(*, transport: httpx.BaseTransport | None = None) -> None:
    """Best-effort release of the runtime slot. Called from atexit.

    Network errors are swallowed — TTL will reap the claim within 90s
    even if this release fails to land. The release is a courtesy that
    speeds up failover when the runtime exits cleanly.
    """
    if _state is None:
        return
    if _state.stop_event is not None:
        _state.stop_event.set()

    try:
        with httpx.Client(
            base_url=_state.base_url,
            timeout=_RELEASE_TIMEOUT_S,
            transport=transport,
            headers={"Authorization": f"Bearer {_state.jwt}"},
        ) as client:
            client.post(
                "/v1/runtime/release",
                json={
                    "passport": _state.passport,
                    "runtime_id": _state.runtime_id,
                },
            )
    except (httpx.RequestError, httpx.HTTPError) as e:
        logger.debug("runtime_claim.release_swallowed_error: %s", e)


def register_atexit_release() -> None:
    """Register the release call so it fires when Python exits cleanly.
    Idempotent — atexit handles duplicate registration gracefully."""
    atexit.register(release_slot)


def _safe_json(resp: httpx.Response) -> dict:
    """Parse JSON without raising — returns {} on bad-json so callers
    don't crash on edge responses."""
    try:
        return resp.json()
    except ValueError:
        return {}


# Convenience accessor for tests
def _reset_state_for_tests() -> None:
    """Tear down module state so tests don't bleed into each other."""
    global _state
    if _state is not None and _state.stop_event is not None:
        _state.stop_event.set()
        if _state.heartbeat_thread is not None:
            _state.heartbeat_thread.join(timeout=1.0)
    _state = None
