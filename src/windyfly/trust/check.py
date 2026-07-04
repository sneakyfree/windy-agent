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
        if self.status in ("suspended", "revoked"):
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
    db_path = os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")
    return Database(db_path)


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


def _cache_read(passport: str, db=None) -> TrustSnapshot | None:
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
    ttl = timedelta(seconds=int(row["cache_ttl_seconds"] or DEFAULT_CACHE_TTL_SECONDS))
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


def _snapshot_from_response(passport: str, data: dict) -> TrustSnapshot:
    return TrustSnapshot(
        passport=data.get("passport_number", passport),
        status=data.get("status", "active"),
        band=data.get("band", "unknown"),
        clearance_level=data.get("clearance_level", "registered"),
        tier_multiplier=float(data.get("tier_multiplier", 0.0)),
        integrity_score=int(data.get("integrity_score", 0)),
        dimensions=dict(data.get("dimensions", {}) or {}),
        allowed_actions=list(data.get("allowed_actions", []) or []),
        denied_actions=list(data.get("denied_actions", []) or []),
        evaluated_at=_parse_dt(data.get("evaluated_at", "")),
        cache_ttl_seconds=int(data.get("cache_ttl_seconds", DEFAULT_CACHE_TTL_SECONDS)),
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


async def check_trust(action: str, passport: str | None = None, db=None) -> TrustDecision:
    """Decide whether `action` is currently allowed.

    Humans / standalone mode (no passport) → skip: the gate is a
    property of passport-bearing agents, so asking about the human
    operator would be a category error. Fail-open.

    Default policy (fail-open): if we have no snapshot — URL unset,
    service down, rate-limited, 404 — we allow the action. Trust is a
    safety/audit layer; a downed trust service shouldn't silently
    freeze the agent.

    Operators can flip to fail-closed with WINDYFLY_TRUST_STRICT=1.
    """
    pp = passport or os.environ.get("ETERNITAS_PASSPORT", "")
    if not pp:
        skip_snap = TrustSnapshot(passport="none", status="active", band="unknown")
        return TrustDecision(
            allowed=True,
            snapshot=skip_snap,
            reason="no passport (human or standalone) — trust gate skipped",
        )

    snap: TrustSnapshot | None = await get_trust(passport=pp, db=db)
    if snap is None:
        snap = TrustSnapshot(passport=pp, status="unknown", band="unknown")
        if _strict_mode():
            return TrustDecision(
                allowed=False,
                snapshot=snap,
                reason="trust service unavailable (strict mode)",
            )
        return TrustDecision(
            allowed=True,
            snapshot=snap,
            reason="no trust snapshot available (fail-open)",
        )

    if snap.allows(action):
        return TrustDecision(allowed=True, snapshot=snap)
    reason = f"action '{action}' (mapped to '{map_gate_action(action)}') not allowed for status={snap.status} band={snap.band} clearance={snap.clearance_level}"
    return TrustDecision(allowed=False, snapshot=snap, reason=reason)
