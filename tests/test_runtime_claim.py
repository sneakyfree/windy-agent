"""Tests for windyfly.runtime_claim — Phase A.5 wire-up.

The module's surface:
  • acquire_runtime_slot() — POST /v1/runtime/claim, dispatches on the
    response into 4 outcomes (GRANTED / CONFLICT / DEGRADED / SKIPPED).
  • start_heartbeat() / release_slot() — daemon-thread + atexit pair.

All tests use httpx.MockTransport so we exercise the full code path
(URL, headers, body, JSON parsing) without hitting the wire.
"""
from __future__ import annotations

import threading

import httpx
import pytest

from windyfly import runtime_claim


@pytest.fixture(autouse=True)
def _reset_runtime_state():
    """Each test starts with a clean module state."""
    runtime_claim._reset_state_for_tests()
    yield
    runtime_claim._reset_state_for_tests()


@pytest.fixture
def good_creds(monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-AAAA")
    monkeypatch.setenv("WINDY_JWT", "fake.jwt.value")
    monkeypatch.setenv("MIND_BASE_URL", "https://api.windymind.test")


# ─── acquire_runtime_slot() ───────────────────────────────────────────


def test_skipped_when_passport_missing(monkeypatch):
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.setenv("WINDY_JWT", "fake")
    out = runtime_claim.acquire_runtime_slot()
    assert out == runtime_claim.ClaimOutcome.SKIPPED


def test_skipped_when_jwt_missing(monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-X-Y")
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    out = runtime_claim.acquire_runtime_slot()
    assert out == runtime_claim.ClaimOutcome.SKIPPED


def _make_ept(passport: str) -> str:
    """Build a minimal unsigned JWT whose `sub` is the passport id."""
    import base64
    import json

    def b64(obj):
        raw = json.dumps(obj).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{b64({'alg': 'ES256'})}.{b64({'sub': passport})}.sig"


def test_passport_recovered_from_ept_sub(monkeypatch):
    """Keyless drill finding: the hatch may write the EPT but not the bare
    ETERNITAS_PASSPORT id. The passport must be recovered from the EPT's
    `sub` claim so the claim isn't silently skipped (→ midwife never yields
    → double replies)."""
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _make_ept("ET26-KEYLESS-1"))
    monkeypatch.setenv("MIND_BASE_URL", "https://api.windymind.test")

    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        return httpx.Response(
            200,
            json={
                "claimed": True,
                "expires_at": "2026-07-05T15:00:00.000Z",
                "ttl_seconds": 90,
                "heartbeat_interval_seconds": 30,
            },
        )

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.GRANTED
    assert runtime_claim._state is not None
    assert runtime_claim._state.passport == "ET26-KEYLESS-1"


def test_malformed_ept_still_skips(monkeypatch):
    """A garbage token must not crash; passport stays unknown → SKIPPED."""
    monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
    monkeypatch.delenv("WINDY_JWT", raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "not-a-jwt")
    out = runtime_claim.acquire_runtime_slot()
    assert out == runtime_claim.ClaimOutcome.SKIPPED


def test_granted_on_200(good_creds):
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "claimed": True,
                "expires_at": "2026-05-20T15:00:00.000Z",
                "ttl_seconds": 90,
                "heartbeat_interval_seconds": 30,
            },
        )

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.GRANTED
    assert captured["url"].endswith("/v1/runtime/claim")
    assert captured["auth"] == "Bearer fake.jwt.value"
    # Module state should now hold the active claim
    assert runtime_claim._state is not None
    assert runtime_claim._state.passport == "ET26-TEST-AAAA"


def test_conflict_on_409_returns_holder_summary(good_creds):
    def handler(req: httpx.Request) -> httpx.Response:
        # FastAPI wraps the conflict body inside `detail`
        return httpx.Response(
            409,
            json={
                "detail": {
                    "claimed": False,
                    "holder": {
                        "source": "word",
                        "host": "other-mac.local",
                        "claimed_at": "2026-05-20T14:00:00.000Z",
                        "version": "windyfly/0.4.2",
                    },
                    "retry_advice": "exit_idle",
                }
            },
        )

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.CONFLICT
    summary = runtime_claim.conflict_holder_summary()
    assert "word" in summary
    assert "other-mac.local" in summary


def test_conflict_on_202_treated_same_as_409(good_creds):
    """V1 CLI runtime doesn't retry warm-pool yields — exits like 409."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            202,
            json={
                "detail": {
                    "claimed": False,
                    "holder": {
                        "source": "warm-pool",
                        "host": "mind-pool-7",
                        "claimed_at": "2026-05-20T14:00:00.000Z",
                    },
                    "retry_after_seconds": 5,
                    "retry_advice": "retry",
                }
            },
        )

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.CONFLICT


def test_degraded_on_network_error(good_creds):
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.DEGRADED
    # No claim state — fail-open
    assert runtime_claim._state is None


def test_degraded_on_5xx(good_creds):
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.DEGRADED


def test_degraded_on_401(good_creds):
    """JWT rejected by Mind — log + fail-open per spec."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"detail": "Invalid token"})

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.DEGRADED


def test_degraded_on_403_ownership(good_creds):
    """Pro's owns-passport returned not-owned → Mind 403's the claim."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": {"error": "not owner"}})

    out = runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(handler)
    )
    assert out == runtime_claim.ClaimOutcome.DEGRADED


def test_claim_sends_expected_body(good_creds):
    captured = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        captured["body"] = json.loads(req.content.decode())
        return httpx.Response(200, json={"claimed": True, "ttl_seconds": 90})

    runtime_claim.acquire_runtime_slot(
        source="cli", transport=httpx.MockTransport(handler)
    )

    body = captured["body"]
    assert body["passport"] == "ET26-TEST-AAAA"
    assert body["source"] == "cli"
    assert isinstance(body["runtime_id"], str)
    assert len(body["runtime_id"]) >= 32  # UUID shape
    assert isinstance(body["host"], str)
    assert body["version"].startswith("windyfly/")


# ─── heartbeat + release lifecycle ────────────────────────────────────


def test_start_heartbeat_noop_when_no_claim():
    """Calling heartbeat before claim grants should be a no-op."""
    runtime_claim.start_heartbeat()  # Must not raise
    assert runtime_claim._state is None


def test_release_swallows_network_error(good_creds):
    # First grant a claim so release has something to send
    def claim_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"claimed": True, "ttl_seconds": 90})

    runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(claim_handler)
    )
    assert runtime_claim._state is not None

    def release_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("net down")

    # Must not raise — release is best-effort
    runtime_claim.release_slot(transport=httpx.MockTransport(release_handler))


def test_release_swallows_4xx(good_creds):
    def claim_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"claimed": True, "ttl_seconds": 90})

    runtime_claim.acquire_runtime_slot(
        transport=httpx.MockTransport(claim_handler)
    )

    def release_handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="oops")

    runtime_claim.release_slot(transport=httpx.MockTransport(release_handler))


def test_heartbeat_lifecycle(good_creds, monkeypatch):
    """Heartbeat thread launches + can be cleanly stopped via release."""
    # Speed up the heartbeat interval so the test runs in <1s
    monkeypatch.setattr(runtime_claim, "_HEARTBEAT_INTERVAL_S", 0.05)

    heartbeats_received = threading.Event()
    n_calls = {"claim": 0, "heartbeat": 0, "release": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/claim"):
            n_calls["claim"] += 1
            return httpx.Response(200, json={"claimed": True, "ttl_seconds": 90})
        if path.endswith("/heartbeat"):
            n_calls["heartbeat"] += 1
            if n_calls["heartbeat"] >= 2:
                heartbeats_received.set()
            return httpx.Response(
                200,
                json={"ok": True, "expires_at": "x", "yield_requested": False},
            )
        if path.endswith("/release"):
            n_calls["release"] += 1
            return httpx.Response(200, json={"ok": True, "released": True})
        return httpx.Response(404)

    # We need to inject the transport into all 3 paths. The module's
    # internal httpx.Client constructors don't share a transport, so
    # patch httpx.Client to default-use ours.
    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        # Always override transport — production code paths pass
        # transport=None explicitly, which would defeat setdefault.
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client_factory)

    out = runtime_claim.acquire_runtime_slot()
    assert out == runtime_claim.ClaimOutcome.GRANTED
    runtime_claim.start_heartbeat()

    # Wait for at least 2 heartbeats
    assert heartbeats_received.wait(timeout=3.0), "heartbeats didn't fire"

    runtime_claim.release_slot()
    assert n_calls["claim"] == 1
    assert n_calls["heartbeat"] >= 2
    assert n_calls["release"] == 1


def test_heartbeat_exits_on_404(good_creds, monkeypatch):
    """When heartbeat sees 404 (claim lost), the loop exits cleanly."""
    monkeypatch.setattr(runtime_claim, "_HEARTBEAT_INTERVAL_S", 0.02)

    n_heartbeats = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/claim"):
            return httpx.Response(200, json={"claimed": True, "ttl_seconds": 90})
        if path.endswith("/heartbeat"):
            n_heartbeats["n"] += 1
            return httpx.Response(
                404, json={"ok": False, "reason": "claim_expired"}
            )
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client

    def _client_factory(*args, **kwargs):
        # Always override transport — production code paths pass
        # transport=None explicitly, which would defeat setdefault.
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", _client_factory)

    runtime_claim.acquire_runtime_slot()
    runtime_claim.start_heartbeat()

    # Wait for the heartbeat thread to terminate after a 404
    import time as _time

    deadline = _time.monotonic() + 2.0
    while _time.monotonic() < deadline:
        if (
            runtime_claim._state is not None
            and runtime_claim._state.heartbeat_thread is not None
            and not runtime_claim._state.heartbeat_thread.is_alive()
        ):
            break
        _time.sleep(0.01)

    assert runtime_claim._state.heartbeat_thread is not None
    assert not runtime_claim._state.heartbeat_thread.is_alive()
    assert n_heartbeats["n"] >= 1


class TestEptBearer:
    """One-soul drill finding (2026-07-05): keyless agents have no
    WINDY_JWT — the EPT must be an accepted bearer or the grandma-path
    agents never claim their slot and the midwife never yields."""

    def test_ept_token_alone_is_sufficient(self, monkeypatch):
        from windyfly.runtime_claim import _read_creds

        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-X")
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-bearer")
        monkeypatch.delenv("WINDY_JWT", raising=False)
        assert _read_creds() == ("ET26-X", "ept-bearer")

    def test_ept_preferred_over_jwt(self, monkeypatch):
        from windyfly.runtime_claim import _read_creds

        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-X")
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-bearer")
        monkeypatch.setenv("WINDY_JWT", "jwt-bearer")
        assert _read_creds() == ("ET26-X", "ept-bearer")

    def test_jwt_fallback_still_works(self, monkeypatch):
        from windyfly.runtime_claim import _read_creds

        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-X")
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
        monkeypatch.setenv("WINDY_JWT", "jwt-bearer")
        assert _read_creds() == ("ET26-X", "jwt-bearer")

    def test_no_bearer_skips(self, monkeypatch):
        from windyfly.runtime_claim import _read_creds

        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-X")
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        assert _read_creds() is None
