"""End-to-end integration tests against a live Eternitas Trust API.

These hit real HTTP — no respx mocks — and skip automatically when
Eternitas isn't reachable at ETERNITAS_URL.

How to run locally:

    cd /Users/thewindstorm/eternitas
    ./scripts/dev-start.sh          # starts API on :8200

    cd /Users/thewindstorm/windy-agent
    ETERNITAS_URL=http://localhost:8200 \
    ETERNITAS_USE_MOCK=false \
    .venv/bin/python -m pytest tests/integration/test_trust_live.py -v

Required scenarios (per the Wave 4 spec):
  - exceptional bot → max privileges
  - critical bot    → blocked
  - suspended       → cache flushes
  - revoked         → cache flushes
  - human (no passport) → gate skipped, no HTTP hit
"""

from __future__ import annotations

import os
import socket
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest

try:
    from dotenv import load_dotenv
    _ENV_PATH = Path(__file__).resolve().parents[2] / ".env"
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=False)
except ImportError:
    pass

from windyfly.memory.database import Database
from windyfly.trust import TrustDenied, check_trust, invalidate_trust_cache, require_trust
from windyfly.trust.check import _cache_read, get_trust
from windyfly.trust.webhook import handle_trust_changed


def _default_live_url() -> str:
    """Same resolution order as the trust client."""
    return (
        os.environ.get("ETERNITAS_URL", "")
        or os.environ.get("ETERNITAS_API_URL", "")
        or "http://localhost:8200"
    ).rstrip("/")


def _eternitas_reachable(url: str) -> bool:
    """Probe for Eternitas specifically — not just any service on the port.

    Some other service (Node, nginx) may be occupying port 8200 in a dev
    machine. We check an Eternitas-specific endpoint to avoid mistaking
    an unrelated 404 for a live trust service.
    """
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            pass
    except OSError:
        return False
    try:
        # JWKS is an Eternitas-native endpoint; a foreign server on this
        # port will 404 or return non-JSON.
        resp = httpx.get(f"{url}/.well-known/eternitas-keys", timeout=2.0)
        if resp.status_code != 200:
            return False
        body = resp.json()
        return isinstance(body, dict) and "keys" in body
    except (httpx.RequestError, ValueError):
        return False


LIVE_URL = _default_live_url()
pytestmark = pytest.mark.skipif(
    not _eternitas_reachable(LIVE_URL),
    reason=f"Eternitas not reachable at {LIVE_URL}. Start it via scripts/dev-start.sh.",
)


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "agent.db"))


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("ETERNITAS_URL", LIVE_URL)
    monkeypatch.setenv("ETERNITAS_USE_MOCK", "false")
    monkeypatch.delenv("WINDYFLY_TRUST_STRICT", raising=False)
    monkeypatch.setenv("WINDYFLY_DB_PATH", str(tmp_path / "agent.db"))


# Defaults match the passports seeded into a fresh Eternitas via
# scripts/seed-test-trust.* — override per-env when a deployment uses
# different fixture names.
_SEEDED_DEFAULTS: dict[str, str] = {
    "WINDYFLY_TEST_PASSPORT_EXCEPTIONAL": "ET26-TEST-EXCP",
    "WINDYFLY_TEST_PASSPORT_CRITICAL": "ET26-TEST-REVD",
    "WINDYFLY_TEST_PASSPORT_ANY": "ET26-TEST-EXCP",
}


def _pick_passport(env_var: str, fallback: str | None = None) -> str:
    """Resolve a test passport.

    Lookup order: explicit env var, seeded default for this env var,
    caller fallback. Tests skip only when nothing resolves — and with
    seeded defaults baked in, a stock `dev-start.sh` run is enough.
    """
    value = (
        os.environ.get(env_var, "")
        or _SEEDED_DEFAULTS.get(env_var, "")
        or (fallback or "")
    )
    if not value:
        pytest.skip(f"set {env_var} to a passport_number to run this scenario")
    return value


class TestLiveTrustContract:
    async def test_ok_response_has_required_fields(self, db):
        passport = _pick_passport("WINDYFLY_TEST_PASSPORT_ANY", "ET26-TEST-0001")
        snap = await get_trust(passport, db=db)
        if snap is None:
            pytest.skip(f"{passport} not present in live Eternitas (404)")

        assert snap.status in ("active", "suspended", "revoked")
        assert snap.band in ("exceptional", "good", "fair", "poor", "critical")
        assert snap.clearance_level in (
            "registered", "verified", "cleared", "top_secret", "eternal",
        )
        assert 0 <= snap.integrity_score <= 1000
        assert snap.tier_multiplier >= 0.0
        assert set(snap.dimensions.keys()) >= {
            "honesty", "reliability", "compliance", "safety", "reputation",
        }
        assert isinstance(snap.allowed_actions, list)
        assert isinstance(snap.denied_actions, list)

    async def test_exceptional_bot_gets_max_privileges(self, db):
        passport = _pick_passport("WINDYFLY_TEST_PASSPORT_EXCEPTIONAL")
        snap = await get_trust(passport, db=db)
        if snap is None:
            pytest.skip(f"{passport} not in live Eternitas")
        if snap.band != "exceptional":
            pytest.skip(f"{passport} is band={snap.band}, not exceptional")

        decision = await require_trust("send_email", db=db, passport=passport)
        assert decision.allowed

    async def test_critical_bot_is_blocked(self, db):
        passport = _pick_passport("WINDYFLY_TEST_PASSPORT_CRITICAL")
        snap = await get_trust(passport, db=db)
        if snap is None:
            pytest.skip(f"{passport} not in live Eternitas")
        if snap.band != "critical":
            pytest.skip(f"{passport} is band={snap.band}, not critical")

        with pytest.raises(TrustDenied):
            await require_trust("send_email", db=db, passport=passport)

    async def test_suspended_bot_webhook_flushes_cache(self, db):
        passport = _pick_passport("WINDYFLY_TEST_PASSPORT_ANY", "ET26-TEST-0001")
        snap = await get_trust(passport, db=db)
        if snap is None:
            pytest.skip(f"{passport} not in live Eternitas")

        assert _cache_read(passport, db=db) is not None

        await handle_trust_changed(
            {
                "event_type": "trust.changed",
                "passport_number": passport,
                "old_band": snap.band,
                "new_band": "critical",
                "reason": "simulated suspension in test",
            },
            db=db,
        )

        assert _cache_read(passport, db=db) is None

    async def test_revoked_bot_webhook_flushes_cache(self, db):
        passport = _pick_passport("WINDYFLY_TEST_PASSPORT_ANY", "ET26-TEST-0001")
        snap = await get_trust(passport, db=db)
        if snap is None:
            pytest.skip(f"{passport} not in live Eternitas")

        invalidate_trust_cache(passport, db=db)
        await get_trust(passport, db=db)
        assert _cache_read(passport, db=db) is not None

        await handle_trust_changed(
            {
                "event_type": "trust.changed",
                "passport_number": passport,
                "old_band": snap.band,
                "new_band": "critical",
                "reason": "simulated revocation in test",
            },
            db=db,
        )
        assert _cache_read(passport, db=db) is None

    async def test_human_no_passport_skips_trust_call(self, db, monkeypatch):
        """No passport → gate is skipped entirely. No HTTP, no cache."""
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)

        decision = await check_trust("send_email", db=db, passport="")
        assert decision.allowed
        assert "no passport" in decision.reason

    async def test_unknown_passport_returns_none(self, db):
        snap = await get_trust("ET26-DOES-NEVR", db=db)
        assert snap is None  # 404 from live server


# Per-band × per-action matrix. Seeded passports are documented in the
# Eternitas fixtures (ET26-TEST-EXCP/GOOD/FAIR/POOR/REVD). Each row is
# (passport, gate_action, expected_allowed). If a seeded passport isn't
# present in the live server we skip that row rather than fail — fixtures
# can be re-seeded independently.
_GATED_ACTIONS = ("send_email", "post_chat_message", "run_command", "upload_file")

_BAND_EXPECTATIONS = {
    "ET26-TEST-EXCP": {a: True for a in _GATED_ACTIONS},
    "ET26-TEST-GOOD": {a: True for a in _GATED_ACTIONS},
    "ET26-TEST-FAIR": {a: True for a in _GATED_ACTIONS},
    "ET26-TEST-POOR": {a: True for a in _GATED_ACTIONS},
    "ET26-TEST-REVD": {a: False for a in _GATED_ACTIONS},
}


@pytest.mark.parametrize(
    "passport,action,should_allow",
    [
        (p, a, expected)
        for p, actions in _BAND_EXPECTATIONS.items()
        for a, expected in actions.items()
    ],
)
async def test_gated_action_matrix(db, passport, action, should_allow):
    """Every gated action × seeded band: allow/deny matches live Eternitas."""
    snap = await get_trust(passport, db=db)
    if snap is None:
        pytest.skip(f"{passport} not seeded on this Eternitas instance")

    if should_allow:
        decision = await require_trust(action, db=db, passport=passport)
        assert decision.allowed, (
            f"{passport} ({snap.band}/{snap.status}) should allow {action} "
            f"but gate said: {decision.reason}"
        )
    else:
        with pytest.raises(TrustDenied) as exc:
            await require_trust(action, db=db, passport=passport)
        assert exc.value.action == action
