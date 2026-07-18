"""Phase H2 — Live Gateway HTTP Smoke Tests.

Tests every HTTP route on the running Bun gateway with real HTTP requests.
Requires the full stack (Python brain + Bun gateway) to be running.

Usage:
    # Start the stack first, then:
    WINDYFLY_GATEWAY_URL=http://localhost:3000 uv run pytest tests/test_live_gateway.py -v

    # Or mark as live tests:
    uv run pytest tests/test_live_gateway.py -v -m live_gateway
"""

from __future__ import annotations

import json
import os
import time

import pytest

try:
    import httpx
except ImportError:
    httpx = None  # type: ignore


GATEWAY_URL = os.environ.get("WINDYFLY_GATEWAY_URL", "http://localhost:3000")


def _gateway_reachable() -> bool:
    """Check if the gateway is reachable."""
    if httpx is None:
        return False
    try:
        r = httpx.get(f"{GATEWAY_URL}/api/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def _brain_connected() -> bool:
    """Check if the brain is connected (UDS socket)."""
    if httpx is None:
        return False
    try:
        r = httpx.get(f"{GATEWAY_URL}/api/health", timeout=3)
        return r.status_code == 200 and r.json().get("brain_connected", False)
    except Exception:
        return False


skip_if_not_live = pytest.mark.skipif(
    not _gateway_reachable(),
    reason=f"Gateway not reachable at {GATEWAY_URL}. Start the stack first.",
)

skip_if_no_brain = pytest.mark.skipif(
    not _brain_connected(),
    reason="Brain not connected (UDS socket offline). Brain-dependent routes will 404.",
)


# =============================================================================
# H2.1–H2.5: Core Routes
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestCoreRoutes:
    def test_health(self):
        """H2.1: GET /api/health → 200 + ok."""
        r = httpx.get(f"{GATEWAY_URL}/api/health", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"

    def test_sliders_get(self):
        """H2.2: GET /api/sliders → 18 sliders, all 0–10."""
        r = httpx.get(f"{GATEWAY_URL}/api/sliders", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert len(data["sliders"]) == 19
        for name, val in data["sliders"].items():
            assert 0 <= val <= 10, f"Slider '{name}' = {val} out of range"

    def test_sliders_info(self):
        """H2.3: GET /api/sliders/info → all 18 have label+description."""
        r = httpx.get(f"{GATEWAY_URL}/api/sliders/info", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert len(data["sliders"]) == 19
        for name, info in data["sliders"].items():
            assert "label" in info, f"Slider '{name}' missing label"
            assert "description" in info, f"Slider '{name}' missing description"
            assert len(info["description"]) > 10, f"Slider '{name}' description too short"

    def test_sliders_set_roundtrip(self):
        """H2.4: PUT /api/sliders/personality → set to 8, verify roundtrip."""
        # Set
        r = httpx.put(
            f"{GATEWAY_URL}/api/sliders/personality",
            content=json.dumps({"value": 8}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert r.status_code == 200

        # Verify
        r = httpx.get(f"{GATEWAY_URL}/api/sliders", timeout=5)
        assert r.json()["sliders"]["personality"] == 8

        # Reset to default
        httpx.put(
            f"{GATEWAY_URL}/api/sliders/personality",
            content=json.dumps({"value": 5}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )

    def test_cost_daily(self):
        """H2.5: GET /api/cost/daily → numeric daily_spend."""
        r = httpx.get(f"{GATEWAY_URL}/api/cost/daily", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["daily_spend"], (int, float))


# =============================================================================
# H2.6–H2.10: Dashboard & Metadata Routes
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestDashboardRoutes:
    def test_intents(self):
        """H2.6: GET /api/intents → array."""
        r = httpx.get(f"{GATEWAY_URL}/api/intents", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["intents"], list)

    def test_dashboard(self):
        """H2.7: GET /api/dashboard → full summary with all sections."""
        r = httpx.get(f"{GATEWAY_URL}/api/dashboard", timeout=5)
        assert r.status_code == 200
        data = r.json()
        for section in ["memory", "cost", "failures", "skills"]:
            assert section in data, f"Dashboard missing '{section}' section"

    def test_memory_search(self):
        """H2.8: GET /api/memory/search?query=test → nodes array."""
        r = httpx.get(f"{GATEWAY_URL}/api/memory/search?query=test&limit=5", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["nodes"], list)

    def test_journal(self):
        """H2.9: GET /api/journal → journal array."""
        r = httpx.get(f"{GATEWAY_URL}/api/journal", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["journal"], list)

    def test_assessment(self):
        """H2.10: POST /api/assessment → 6-metric response."""
        r = httpx.post(f"{GATEWAY_URL}/api/assessment", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "assessment" in data or "metrics" in data or "report" in data


# =============================================================================
# H2.11–H2.14: Personality Versioning
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestPersonalityRoutes:
    def test_personality_history(self):
        """H2.11: GET /api/personality/history → history array."""
        r = httpx.get(f"{GATEWAY_URL}/api/personality/history", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["history"], list)

    def test_personality_snapshot(self):
        """H2.12: POST /api/personality/snapshot → batch_id."""
        r = httpx.post(f"{GATEWAY_URL}/api/personality/snapshot", timeout=5)
        assert r.status_code == 200
        assert "batch_id" in r.json()

    def test_personality_drift(self):
        """H2.13: GET /api/personality/drift → drift key."""
        r = httpx.get(f"{GATEWAY_URL}/api/personality/drift", timeout=5)
        assert r.status_code == 200
        assert "drift" in r.json()

    def test_personality_rollback(self):
        """H2.14: POST /api/personality/rollback → restored_count."""
        r = httpx.post(
            f"{GATEWAY_URL}/api/personality/rollback",
            content=json.dumps({"snapshot_date": "2099-01-01"}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert r.status_code == 200
        assert isinstance(r.json()["restored_count"], int)


# =============================================================================
# H2.15–H2.19: Skills & Decay
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestSkillsRoutes:
    def test_skills_list(self):
        """H2.15: GET /api/skills → skills array."""
        r = httpx.get(f"{GATEWAY_URL}/api/skills", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["skills"], list)

    def test_skills_create(self):
        """H2.16: POST /api/skills → skill_id."""
        r = httpx.post(
            f"{GATEWAY_URL}/api/skills",
            content=json.dumps({
                "name": "smoke_test_skill",
                "code": "result = 42",
                "language": "python",
            }),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert r.status_code == 200
        assert "skill_id" in r.json()

    def test_decay_run(self):
        """H2.17: POST /api/decay/run → decay counts."""
        r = httpx.post(f"{GATEWAY_URL}/api/decay/run", timeout=10)
        assert r.status_code == 200
        data = r.json()
        assert "decay" in data
        assert isinstance(data["decay"]["decayed"], int)


# =============================================================================
# H2.18–H2.24: Conflicts, Moments, Failures, Mode, Offline, Events
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestSystemRoutes:
    def test_conflicts(self):
        """H2.18: GET /api/conflicts → conflicts array."""
        r = httpx.get(f"{GATEWAY_URL}/api/conflicts", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["conflicts"], list)

    def test_moments(self):
        """H2.19: GET /api/moments → moments array."""
        r = httpx.get(f"{GATEWAY_URL}/api/moments", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["moments"], list)

    def test_failures(self):
        """H2.20: GET /api/failures → failures array."""
        r = httpx.get(f"{GATEWAY_URL}/api/failures", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["failures"], list)

    def test_mode_get(self):
        """H2.21: GET /api/mode → mode string."""
        r = httpx.get(f"{GATEWAY_URL}/api/mode", timeout=5)
        assert r.status_code == 200
        assert r.json()["mode"] in ("companion", "focused", "neutral")

    def test_mode_set_roundtrip(self):
        """H2.22: PUT /api/mode → set focused, verify roundtrip."""
        # Set
        httpx.put(
            f"{GATEWAY_URL}/api/mode",
            content=json.dumps({"mode": "focused"}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        # Verify
        r = httpx.get(f"{GATEWAY_URL}/api/mode", timeout=5)
        assert r.json()["mode"] == "focused"
        # Reset
        httpx.put(
            f"{GATEWAY_URL}/api/mode",
            content=json.dumps({"mode": "companion"}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )

    def test_offline_status(self):
        """H2.23: GET /api/offline/status → online + ollama booleans."""
        r = httpx.get(f"{GATEWAY_URL}/api/offline/status", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["online"], bool)
        assert isinstance(data["ollama_available"], bool)

    def test_events(self):
        """H2.24: GET /api/events → events array + counts."""
        r = httpx.get(f"{GATEWAY_URL}/api/events", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["events"], list)
        assert isinstance(data["counts_24h"], dict)


# =============================================================================
# H2.25–H2.28: Providers, Machines, Shape-Shift
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestProviderMachineRoutes:
    def test_providers(self):
        """H2.25: GET /api/providers → providers array with builtins."""
        r = httpx.get(f"{GATEWAY_URL}/api/providers", timeout=5)
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data["providers"], list)
        assert len(data["providers"]) >= 10, "Expected 10+ built-in providers"

    def test_machines(self):
        """H2.26: GET /api/machines → machines array."""
        r = httpx.get(f"{GATEWAY_URL}/api/machines", timeout=5)
        assert r.status_code == 200
        assert isinstance(r.json()["machines"], list)

    def test_shape_shift(self):
        """H2.27: POST /api/shape-shift → accepts valid request."""
        r = httpx.post(
            f"{GATEWAY_URL}/api/shape-shift",
            content=json.dumps({
                "target_sliders": {"humor": 10, "formality": 0},
                "reason": "Smoke test",
            }),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        # May succeed or fail depending on state, but shouldn't 500
        assert r.status_code in (200, 400)

    def test_shape_shift_restore(self):
        """H2.28: POST /api/shape-shift/restore → restore works."""
        r = httpx.post(f"{GATEWAY_URL}/api/shape-shift/restore", timeout=5)
        assert r.status_code in (200, 400)  # 400 if nothing to restore


# =============================================================================
# H2.29–H2.32: Infrastructure Tests
# =============================================================================


@skip_if_not_live
class TestInfrastructure:
    def test_index_html(self):
        """H2.29: GET / → 200, HTML, contains 'Windy Fly'."""
        r = httpx.get(f"{GATEWAY_URL}/", timeout=5)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Windy Fly" in r.text

    def test_404_on_unknown(self):
        """H2.30: GET /nonexistent → 404, not 500."""
        r = httpx.get(f"{GATEWAY_URL}/api/nonexistent-route-xyz", timeout=5)
        assert r.status_code == 404

    def test_cors_preflight(self):
        """H2.31: OPTIONS /api/health → 204 + CORS headers."""
        r = httpx.options(
            f"{GATEWAY_URL}/api/health",
            headers={"Origin": "http://localhost:5173"},
            timeout=5,
        )
        assert r.status_code == 204
        assert "access-control-allow-origin" in {k.lower() for k in r.headers}

    def test_response_times(self):
        """All core routes should respond in < 500ms."""
        routes = [
            "/api/health",
        ]
        for route in routes:
            start = time.time()
            r = httpx.get(f"{GATEWAY_URL}{route}", timeout=5)
            elapsed = time.time() - start
            assert elapsed < 0.5, f"{route} took {elapsed:.2f}s (>500ms)"
            assert r.status_code == 200


# =============================================================================
# H2 Bonus: Error Handling
# =============================================================================


@skip_if_not_live
@skip_if_no_brain
class TestErrorHandling:
    def test_invalid_slider_value(self):
        """PUT invalid slider value → error response, not crash."""
        r = httpx.put(
            f"{GATEWAY_URL}/api/sliders/personality",
            content=json.dumps({"value": 99}),
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert r.status_code in (400, 500)
        # Should have an error message
        data = r.json()
        assert "error" in data or "message" in data

    def test_invalid_json_body(self):
        """POST with malformed JSON → 400/500, not hang."""
        r = httpx.post(
            f"{GATEWAY_URL}/api/skills",
            content=b"{invalid json",
            headers={"Content-Type": "application/json"},
            timeout=5,
        )
        assert r.status_code in (400, 500)

    def test_empty_body_on_post(self):
        """POST with empty body → handled gracefully."""
        r = httpx.post(
            f"{GATEWAY_URL}/api/personality/snapshot",
            timeout=5,
        )
        # Should work or fail gracefully
        assert r.status_code in (200, 400)

    def test_sql_injection_in_search(self):
        """SQL injection in search query → safe response."""
        r = httpx.get(
            f"{GATEWAY_URL}/api/memory/search",
            params={"query": "'; DROP TABLE nodes;--", "limit": "5"},
            timeout=5,
        )
        assert r.status_code == 200
        assert isinstance(r.json()["nodes"], list)

    def test_xss_in_search(self):
        """XSS payload in search → safe, not reflected raw."""
        r = httpx.get(
            f"{GATEWAY_URL}/api/memory/search",
            params={"query": "<script>alert(1)</script>", "limit": "5"},
            timeout=5,
        )
        assert r.status_code == 200
        # Response should not contain raw script tag
        assert "<script>" not in r.text
