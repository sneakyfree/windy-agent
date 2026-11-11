"""Fetch and cache the agent's trust snapshot from Eternitas.

Canonical contract: `eternitas/docs/trust-api.md` (eternitas repo).

    GET {ETERNITAS_URL}/api/v1/trust/{passport}    (public, no auth)
    Rate: 100 req/min/IP, server-side Redis cache 5 min
    Headers: X-Trust-Cache: hit|miss

    Response: {
        passport_number, status, integrity_score,
        dimensions: {honesty, reliability, compliance, safety, reputation},
        band: exceptional|good|fair|poor|critical,
        clearance_level: registered|verified|cleared|top_secret|eternal,
        tier_multiplier: float,
        allowed_actions: [...],
        denied_actions: [...],
        cache_ttl_seconds: 300,
        evaluated_at: iso8601,
    }

Critical band has empty allowed_actions by contract. status ∈
{suspended, revoked} zeroes the multiplier and denies everything.

The snapshot is cached in the agent's SQLite (trust_cache table).
Server-suggested cache_ttl_seconds is honoured (falls back to 5 min).

A home PULLS this; it has no inbound port for a push. Eternitas's rule for every verifier (BOARD
2026-11-11T05:15:50Z; eternitas.ai/developers, "Revocation, on every request") therefore applies to the home too:

1. cache at most 5 minutes, and if the verdict cannot be refreshed for 10 minutes the passport counts as NOT good:
   every gated action is refused until a fetch succeeds (never a stale yes, and never "unknown = yes");
2. a high-value act asks live, with no cache.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

DEFAULT_CACHE_TTL_SECONDS = 300
CACHE_TTL = timedelta(seconds=DEFAULT_CACHE_TTL_SECONDS)
# How long the last verdict that was really fetched may still be used when a refresh fails. After that the passport
# counts as not good until a fetch succeeds.
MAX_VERDICT_AGE_SECONDS = 600
# TrustDecision.reason for "no verdict I may still use". The owner reads it. Windy Legal's words, word for word
# (BOARD 2026-11-11T05:54:01Z): the action is REFUSED, never queued, so neither sentence says "holding".
NO_FRESH_VERDICT = ("I didn't do this, because I couldn't check with Eternitas that I'm still allowed to. "
                    "Ask me again in a few minutes.")
NO_LIVE_VERDICT = ("This needs a live check with Eternitas that I'm still allowed to do it, and Eternitas didn't "
                   "answer. I didn't do it; ask me again in a few minutes.")
_TIMEOUT = 5.0

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS trust_cache (
    passport TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    band TEXT NOT NULL,
    clearance_level TEXT NOT NULL,
    tier_multiplier REAL NOT NULL,
    integrity_score INTEGER NOT NULL,
    dimensions JSON NOT NULL,
    allowed_actions JSON NOT NULL,
    denied_actions JSON NOT NULL,
    evaluated_at DATETIME NOT NULL,
    cache_ttl_seconds INTEGER NOT NULL,
    cached_at DATETIME NOT NULL
);
"""

# Maps the agent's gate vocabulary to the Eternitas action vocabulary.
# Eternitas vocabulary (per trust-api.md):
#   read, send, execute, dm_bots, install_packages,
#   commit_push, broadcast, mention_strangers, bypass_rate_caps
_GATE_TO_ETERNITAS: dict[str, str] = {
    "send_email": "send",
    "post_chat_message": "send",
    "run_command": "execute",
    "install_package": "install_packages",
    "commit_push": "commit_push",
    "upload_file": "send",
}


def map_gate_action(action: str) -> str:
    """Translate an agent-side gate action into an Eternitas action.

    Unknown actions pass through unchanged so new gate names can ship
    without a round-trip through this table.
    """
    return _GATE_TO_ETERNITAS.get(action, action)


@dataclass
class TrustSnapshot:
    passport: str
    status: str = "active"
    band: str = "unknown"
    clearance_level: str = "registered"
    tier_multiplier: float = 0.0
    integrity_score: int = 0
    dimensions: dict[str, int] = field(default_factory=dict)
    allowed_actions: list[str] = field(default_factory=list)
    denied_actions: list[str] = field(default_factory=list)
    evaluated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    cache_ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS

    def allows(self, action: str) -> bool:
        """True if `action` (gate vocabulary) is allowed right now.

        Inactive status and the `critical` band both deny everything
        by contract — the server already empties `allowed_actions` in
        those cases, but we double-check here so a malformed response
        fails closed.
        """
        if self.status != "active":  # suspended, revoked, being_born, not_born, anything new: fail closed
            return False
        if self.band == "critical":
            return False
        mapped = map_gate_action(action)
        if not self.allowed_actions:
            return False
        return "*" in self.allowed_actions or mapped in self.allowed_actions


@dataclass
class TrustDecision:
    allowed: bool
    snapshot: TrustSnapshot
    reason: str = ""
    #: True when the answer is "nobody could tell just now" (no verdict fresh enough), not a verdict against the
    #: agent. The command registry lets the OWNER AT THE KEYBOARD through in that case only: an outage at Eternitas
    #: must not lock a person out of their own agent.
    unverified: bool = False


def _eternitas_url() -> str:
    """Resolve the Eternitas base URL (see eternitas.url.resolve_eternitas_url)."""
    from windyfly.eternitas.url import resolve_eternitas_url
    return resolve_eternitas_url()


def _use_mock() -> bool:
    """True when the caller wants the local mock instead of a live fetch."""
    val = os.environ.get("ETERNITAS_USE_MOCK", "").lower()
    return val in ("1", "true", "yes")


def _db():
    """Get the agent's shared Database instance (lazy import)."""
    from windyfly.memory.database import Database
    from windyfly.memory.db_location import resolve_db_path

    return Database(resolve_db_path())


def _ensure_table(db) -> None:
    db.conn.execute(_TABLE_DDL)
    db.conn.commit()


def _parse_dt(raw: str) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _capped_ttl(suggested: object) -> int:
    """The registry may suggest a SHORTER cache life than 5 minutes, never a longer one: the home keeps the published
    rule (cache 5 minutes at most) whatever a later answer suggests (Eternitas, BOARD 2026-11-11T05:46:22Z (b))."""
    try:
        value = int(suggested or DEFAULT_CACHE_TTL_SECONDS)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        value = DEFAULT_CACHE_TTL_SECONDS
    return max(1, min(value, DEFAULT_CACHE_TTL_SECONDS))


def _cache_read(passport: str, db=None, max_age_seconds: int | None = None) -> TrustSnapshot | None:
    """The cached verdict while it is fresh (its own TTL), or, with ``max_age_seconds``, while it is at most that
    old. The age counts from ``cached_at``: the time of the last SUCCESSFUL fetch."""
    db = db or _db()
    _ensure_table(db)
    row = db.conn.execute(
        "SELECT status, band, clearance_level, tier_multiplier, integrity_score, "
        "dimensions, allowed_actions, denied_actions, evaluated_at, "
        "cache_ttl_seconds, cached_at "
        "FROM trust_cache WHERE passport = ?",
        (passport,),
    ).fetchone()
    if not row:
        return None

    cached_at = _parse_dt(row["cached_at"])
    ttl = timedelta(seconds=max_age_seconds if max_age_seconds is not None
                    else _capped_ttl(row["cache_ttl_seconds"]))
    if datetime.now(timezone.utc) - cached_at > ttl:
        return None

    return TrustSnapshot(
        passport=passport,
        status=row["status"],
        band=row["band"],
        clearance_level=row["clearance_level"],
        tier_multiplier=float(row["tier_multiplier"]),
        integrity_score=int(row["integrity_score"]),
        dimensions=dict(json.loads(row["dimensions"])),
        allowed_actions=list(json.loads(row["allowed_actions"])),
        denied_actions=list(json.loads(row["denied_actions"])),
        evaluated_at=_parse_dt(row["evaluated_at"]),
        cache_ttl_seconds=int(row["cache_ttl_seconds"] or DEFAULT_CACHE_TTL_SECONDS),
    )


def _cache_read_last_known_status(passport: str, db=None) -> str | None:
    """Return the last status we ever observed, ignoring the TTL.

    `_cache_read` deliberately treats an expired row as absent, which is
    right for "what is the trust level now". It is wrong for "were we
    ever told this passport was revoked" — a revocation must not be
    forgotten just because the row went stale and Eternitas is now
    unreachable. Returns None only when we have genuinely never seen a
    verdict for this passport.
    """
    db = db or _db()
    _ensure_table(db)
    row = db.conn.execute(
        "SELECT status FROM trust_cache WHERE passport = ?",
        (passport,),
    ).fetchone()
    return row["status"] if row else None


def _cache_write(snap: TrustSnapshot, db=None) -> None:
    db = db or _db()
    _ensure_table(db)
    db.conn.execute(
        "INSERT OR REPLACE INTO trust_cache "
        "(passport, status, band, clearance_level, tier_multiplier, integrity_score, "
        " dimensions, allowed_actions, denied_actions, evaluated_at, "
        " cache_ttl_seconds, cached_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            snap.passport,
            snap.status,
            snap.band,
            snap.clearance_level,
            snap.tier_multiplier,
            snap.integrity_score,
            json.dumps(snap.dimensions),
            json.dumps(snap.allowed_actions),
            json.dumps(snap.denied_actions),
            snap.evaluated_at.isoformat(),
            int(snap.cache_ttl_seconds),
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    db.conn.commit()


def invalidate_trust_cache(passport: str | None = None, db=None) -> None:
    """Drop cached trust state. Called from the trust.changed webhook."""
    db = db or _db()
    _ensure_table(db)
    if passport:
        db.conn.execute("DELETE FROM trust_cache WHERE passport = ?", (passport,))
    else:
        db.conn.execute("DELETE FROM trust_cache")
    db.conn.commit()


_missing_status_warned: set[str] = set()


def _status_of(passport: str, data: dict) -> str:
    """The passport status from a trust response. A response with no status is NOT read as active:
    it becomes "unknown", which allows() refuses (fail closed). Logged once per passport."""
    status = data.get("status")
    if isinstance(status, str) and status.strip():
        return status.strip()
    if passport not in _missing_status_warned:
        _missing_status_warned.add(passport)
        logger.warning("Trust response for %s has no status; treating it as unknown (actions refused)", passport)
    return "unknown"


def _snapshot_from_response(passport: str, data: dict) -> TrustSnapshot:
    return TrustSnapshot(
        passport=data.get("passport_number", passport),
        status=_status_of(passport, data),
        band=data.get("band", "unknown"),
        clearance_level=data.get("clearance_level", "registered"),
        tier_multiplier=float(data.get("tier_multiplier", 0.0)),
        integrity_score=int(data.get("integrity_score", 0)),
        dimensions=dict(data.get("dimensions", {}) or {}),
        allowed_actions=list(data.get("allowed_actions", []) or []),
        denied_actions=list(data.get("denied_actions", []) or []),
        evaluated_at=_parse_dt(data.get("evaluated_at", "")),
        cache_ttl_seconds=_capped_ttl(data.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS)),
    )


async def _fetch(passport: str) -> TrustSnapshot | None:
    if _use_mock():
        logger.debug("Trust fetch mocked (ETERNITAS_USE_MOCK=true); returning None")
        return None

    url = _eternitas_url()
    if not url:
        logger.debug("Trust fetch skipped: ETERNITAS_URL not set")
        return None

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{url}/api/v1/trust/{passport}")
    except httpx.RequestError as exc:
        logger.warning("Trust fetch failed: %s", exc)
        return None

    if resp.status_code == 404:
        logger.info("Trust fetch: passport %s not found (404)", passport)
        return None
    if resp.status_code == 400:
        logger.warning("Trust fetch: unrecognised passport format %s (400)", passport)
        return None
    if resp.status_code == 429:
        logger.warning(
            "Trust fetch: rate-limited, Retry-After=%s",
            resp.headers.get("Retry-After", "?"),
        )
        return None
    if resp.status_code != 200:
        logger.warning("Trust fetch returned %s", resp.status_code)
        return None

    try:
        data = resp.json()
    except ValueError as exc:
        logger.warning("Trust fetch: malformed JSON: %s", exc)
        return None

    cache_header = resp.headers.get("X-Trust-Cache", "")
    if cache_header:
        logger.debug("Trust fetch X-Trust-Cache=%s", cache_header)

    return _snapshot_from_response(passport, data)


async def get_trust(passport: str | None = None, db=None, force_refresh: bool = False) -> TrustSnapshot | None:
    """Return the current trust snapshot, using the cache unless forced.

    No passport → returns None (caller policy). Humans and standalone
    runs don't have passports, so skipping rather than faking a 404
    keeps their call paths clean.
    """
    passport = passport or os.environ.get("ETERNITAS_PASSPORT", "")
    if not passport:
        return None

    if not force_refresh:
        cached = _cache_read(passport, db=db)
        if cached:
            return cached

    fresh = await _fetch(passport)
    if fresh:
        _cache_write(fresh, db=db)
    return fresh


def _strict_mode() -> bool:
    """True when operators have opted into fail-closed trust gating."""
    return os.environ.get("WINDYFLY_TRUST_STRICT", "").lower() in ("1", "true", "yes")


def decide_unrefreshed(action: str, pp: str, db=None) -> TrustDecision:
    """The decision when NO fresh verdict could be had (the fetch failed, or could not be made). In this order:

    1. A suspended / revoked / not-born status once seen is never forgotten: refuse. Losing contact with Eternitas
       is not evidence of reinstatement; that needs a successful fetch, the one thing an offline agent cannot fake.
    2. WINDYFLY_TRUST_STRICT=1: refuse at once.
    3. ETERNITAS_USE_MOCK (tests and local development only): no registry is asked at all, so there is nothing to
       be stale; allow, as before.
    4. The last verdict that was really fetched, if it is at most MAX_VERDICT_AGE_SECONDS old, still decides: with
       ITS allowed actions, not "everything".
    5. Otherwise the passport counts as not good: refuse, ``unverified=True``.

    Until 2026-11 step 5 was "allow" for as long as the outage lasted, whatever the last verdict had allowed.
    """
    last_known = _cache_read_last_known_status(pp, db=db)
    if last_known in ("suspended", "revoked", "being_born", "not_born"):
        logger.warning(
            "Trust service unreachable; denying '%s' because the last "
            "verdict we received for %s was '%s'. Going offline does not "
            "restore a revoked passport.",
            action, pp, last_known,
        )
        return TrustDecision(
            allowed=False,
            snapshot=TrustSnapshot(passport=pp, status=last_known, band="unknown"),
            reason=(
                f"trust service unavailable and last known status was "
                f"'{last_known}' — revocation is not forgotten on outage"
            ),
        )

    unknown = TrustSnapshot(passport=pp, status="unknown", band="unknown")
    if _strict_mode():
        # Strict means refuse at once and everywhere, the keyboard included: not marked "unverified".
        return TrustDecision(allowed=False, snapshot=unknown, reason="trust service unavailable (strict mode)")
    if _use_mock():
        return TrustDecision(allowed=True, snapshot=unknown, reason="no registry is asked (ETERNITAS_USE_MOCK)")

    last = _cache_read(pp, db=db, max_age_seconds=MAX_VERDICT_AGE_SECONDS)
    if last is not None:
        if last.allows(action):
            return TrustDecision(allowed=True, snapshot=last,
                                 reason="Eternitas did not answer; using its last verdict, under 10 minutes old")
        return TrustDecision(
            allowed=False, snapshot=last,
            reason=f"action '{action}' (mapped to '{map_gate_action(action)}') not allowed for status={last.status} "
                   f"band={last.band} clearance={last.clearance_level}")
    logger.warning("Trust: no verdict for %s in the last %d s; refusing '%s' until a fetch succeeds",
                   pp, MAX_VERDICT_AGE_SECONDS, action)
    return TrustDecision(allowed=False, snapshot=unknown, reason=NO_FRESH_VERDICT, unverified=True)


async def check_trust(action: str, passport: str | None = None, db=None, live: bool = False) -> TrustDecision:
    """Decide whether `action` is currently allowed.

    Humans / standalone mode (no passport) → skip: the gate is a
    property of passport-bearing agents, so asking about the human
    operator would be a category error.

    A passport-bearing agent needs a verdict: the cached one while it is fresh (5 minutes), else a fetch. When the
    fetch fails, ``decide_unrefreshed`` rules (the last fetched verdict for at most 10 minutes, then refuse).

    ``live=True`` is for a high-value act (deleting data, a change of owner, money, consent): the cache is not
    used at all. No live answer = refuse.
    """
    pp = passport or os.environ.get("ETERNITAS_PASSPORT", "")
    if not pp:
        skip_snap = TrustSnapshot(passport="none", status="active", band="unknown")
        return TrustDecision(
            allowed=True,
            snapshot=skip_snap,
            reason="no passport (human or standalone) — trust gate skipped",
        )

    snap: TrustSnapshot | None = await get_trust(passport=pp, db=db, force_refresh=live)
    if snap is None:
        decision = decide_unrefreshed(action, pp, db=db)
        if live and (decision.allowed or decision.unverified) and not _use_mock():
            # No live answer. A cached yes does not stand in for it, and with no verdict at all the live sentence is
            # still the true one. A verdict AGAINST the agent (decided above, not unverified) keeps its own reason.
            return TrustDecision(allowed=False, snapshot=decision.snapshot, reason=NO_LIVE_VERDICT, unverified=True)
        return decision

    if snap.allows(action):
        return TrustDecision(allowed=True, snapshot=snap)
    reason = f"action '{action}' (mapped to '{map_gate_action(action)}') not allowed for status={snap.status} band={snap.band} clearance={snap.clearance_level}"
    return TrustDecision(allowed=False, snapshot=snap, reason=reason)
