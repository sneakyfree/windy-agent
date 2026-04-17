"""Contract tests for the ecosystem_health shape canary (P3-E5).

Before this fix: `windy ecosystem` considered any 200-response on
the health path as "Connected". But a different service squatting
on the port (Node on :8200 being a historical example) would also
return 200 and the owner would see a green check for a service
they can't actually reach.

Fix: when the caller supplies `expected_service`, the response
body must be JSON with `service == <expected>`, case-insensitively.
A missing service field is tolerated — only a mismatched one is
treated as "wrong service".
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.ecosystem_health import _check_health


class TestShapeCanary:
    @respx.mock
    async def test_matching_service_passes(self):
        respx.get("https://eternitas.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={"service": "eternitas"})
        )
        status, _ = await _check_health(
            "https://eternitas.test", "/api/v1/health",
            expected_service="eternitas",
        )
        assert status == "ok"

    @respx.mock
    async def test_case_insensitive(self):
        respx.get("https://eternitas.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={"service": "ETERNITAS"})
        )
        status, _ = await _check_health(
            "https://eternitas.test", "/api/v1/health",
            expected_service="eternitas",
        )
        assert status == "ok"

    @respx.mock
    async def test_wrong_service_fails(self):
        """Some other service on the port returns its own name."""
        respx.get("https://imposter.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={"service": "different-app"})
        )
        status, _ = await _check_health(
            "https://imposter.test", "/api/v1/health",
            expected_service="eternitas",
        )
        assert "wrong service" in status
        assert "different-app" in status

    @respx.mock
    async def test_non_json_body_refused(self):
        """A static nginx 200 page is not a valid /health response."""
        respx.get("https://nginx.test/api/v1/health").mock(
            return_value=httpx.Response(200, text="<html>Welcome</html>")
        )
        status, _ = await _check_health(
            "https://nginx.test", "/api/v1/health",
            expected_service="eternitas",
        )
        assert "wrong service" in status
        assert "non-JSON" in status

    @respx.mock
    async def test_json_array_refused(self):
        """A JSON array is not the expected object shape."""
        respx.get("https://wrong.test/api/v1/health").mock(
            return_value=httpx.Response(200, json=["ok", "thanks"])
        )
        status, _ = await _check_health(
            "https://wrong.test", "/api/v1/health",
            expected_service="eternitas",
        )
        assert "wrong service" in status

    @respx.mock
    async def test_missing_service_field_tolerated(self):
        """Older builds may return a minimal body — we don't punish
        absence, only mismatch."""
        respx.get("https://minimal.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={"status": "ok"})
        )
        status, _ = await _check_health(
            "https://minimal.test", "/api/v1/health",
            expected_service="eternitas",
        )
        assert status == "ok"

    @respx.mock
    async def test_no_expected_means_no_canary(self):
        """Back-compat: callers that don't opt in keep the old
        lenient behaviour."""
        respx.get("https://any.test/api/v1/health").mock(
            return_value=httpx.Response(200, text="<html>hi</html>")
        )
        status, _ = await _check_health("https://any.test", "/api/v1/health")
        assert status == "ok"
