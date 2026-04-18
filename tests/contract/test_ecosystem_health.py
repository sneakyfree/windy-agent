"""Contract tests for src/windyfly/ecosystem_health.py (P1-O3).

The module was at 0% coverage. These tests exercise the five
service probes (Eternitas, Matrix, Mail, Cloud, Pro) and the
three configuration states each can be in (configured + reachable,
configured + unreachable, not configured).

What we pin:
- Each service probe hits the expected path (e.g. /api/v1/health,
  /_matrix/client/versions).
- Connect error, timeout, and HTTP 5xx each render distinct
  messages.
- Not-configured services show "Not configured".
- Local-mode (no URL but credentials set) shows "Local mode".
"""

from __future__ import annotations

import httpx
import pytest
import respx

from windyfly.ecosystem_health import _check_health, check_ecosystem_health


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Strip every ecosystem-adjacent env var so config starts clean."""
    for var in (
        "ETERNITAS_URL", "ETERNITAS_API_URL", "ETERNITAS_PASSPORT",
        "MATRIX_HOMESERVER", "MATRIX_BOT_USER", "MATRIX_BOT_TOKEN",
        "WINDYMAIL_API_URL", "WINDYMAIL_EMAIL",
        "WINDY_CLOUD_URL", "WINDY_API_URL",
    ):
        monkeypatch.delenv(var, raising=False)
    # Prevent load_config from finding a real windyfly.toml on disk.
    monkeypatch.setattr(
        "windyfly.ecosystem_health.load_config",
        lambda: {},
    )


# ────────────────────────────────────────────────────────────────────
# _check_health
# ────────────────────────────────────────────────────────────────────


class TestCheckHealth:
    @respx.mock
    async def test_ok_with_latency(self):
        respx.get("https://svc.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={})
        )
        status, latency = await _check_health("https://svc.test", "/api/v1/health")
        assert status == "ok"
        assert latency.startswith("(") and latency.endswith("ms)")

    @respx.mock
    async def test_strips_trailing_slash(self):
        route = respx.get("https://svc.test/api/v1/health").mock(
            return_value=httpx.Response(200)
        )
        await _check_health("https://svc.test///", "/api/v1/health")
        assert route.called

    @respx.mock
    async def test_connection_refused(self):
        respx.get("https://down.test/api/v1/health").mock(
            side_effect=httpx.ConnectError("refused")
        )
        status, latency = await _check_health("https://down.test", "/api/v1/health")
        assert status == "connection refused"
        assert latency == ""

    @respx.mock
    async def test_timeout(self):
        respx.get("https://slow.test/api/v1/health").mock(
            side_effect=httpx.TimeoutException("too slow")
        )
        status, _ = await _check_health("https://slow.test", "/api/v1/health")
        assert status == "timeout"

    @respx.mock
    async def test_http_500_reports_status(self):
        respx.get("https://broken.test/api/v1/health").mock(
            return_value=httpx.Response(502)
        )
        status, _ = await _check_health("https://broken.test", "/api/v1/health")
        assert status == "HTTP 502"

    @respx.mock
    async def test_http_4xx_treated_as_ok(self):
        """A 404 on /health still means the service is up and
        answering — we only fail on 5xx."""
        respx.get("https://svc.test/api/v1/health").mock(
            return_value=httpx.Response(404)
        )
        status, _ = await _check_health("https://svc.test", "/api/v1/health")
        assert status == "ok"


# ────────────────────────────────────────────────────────────────────
# check_ecosystem_health — full renders
# ────────────────────────────────────────────────────────────────────


class TestCheckEcosystem:
    async def test_all_services_not_configured(self):
        out = await check_ecosystem_health()
        assert "Windy Ecosystem Status" in out
        assert out.count("Not configured") >= 4  # Eternitas, Chat, Mail, Pro, Cloud

    @respx.mock
    async def test_eternitas_connected_shows_passport(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_API_URL", "https://eternitas.test")
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0001")
        respx.get("https://eternitas.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={})
        )

        out = await check_ecosystem_health()

        assert "Eternitas" in out
        assert "Connected" in out
        assert "ET26-TEST-0001" in out

    @respx.mock
    async def test_eternitas_unreachable(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_API_URL", "https://down.test")
        respx.get("https://down.test/api/v1/health").mock(
            side_effect=httpx.ConnectError("refused")
        )

        out = await check_ecosystem_health()

        assert "Eternitas" in out
        assert "Unreachable" in out or "refused" in out

    async def test_eternitas_local_mode_with_passport_only(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET-L00042")

        out = await check_ecosystem_health()

        assert "Local mode" in out
        assert "ET-L00042" in out

    @respx.mock
    async def test_matrix_connected_shows_bot_user(self, monkeypatch):
        monkeypatch.setenv("MATRIX_HOMESERVER", "https://chat.test")
        monkeypatch.setenv("MATRIX_BOT_USER", "@windyfly:chat.test")
        respx.get("https://chat.test/_matrix/client/versions").mock(
            return_value=httpx.Response(200, json={"versions": ["r0.6.1"]})
        )

        out = await check_ecosystem_health()

        assert "Windy Chat" in out
        assert "Connected" in out
        assert "@windyfly:chat.test" in out

    @respx.mock
    async def test_mail_connected_shows_address(self, monkeypatch):
        monkeypatch.setenv("WINDYMAIL_API_URL", "https://mail.test")
        monkeypatch.setenv("WINDYMAIL_EMAIL", "fly@windymail.ai")
        respx.get("https://mail.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={})
        )

        out = await check_ecosystem_health()

        assert "Windy Mail" in out
        assert "fly@windymail.ai" in out

    @respx.mock
    async def test_cloud_connected(self, monkeypatch):
        monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.test")
        respx.get("https://cloud.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={})
        )

        out = await check_ecosystem_health()

        assert "Windy Cloud" in out
        assert "Connected" in out

    @respx.mock
    async def test_pro_connected(self, monkeypatch):
        monkeypatch.setenv("WINDY_API_URL", "https://pro.test")
        respx.get("https://pro.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={})
        )

        out = await check_ecosystem_health()

        assert "Windy Pro" in out
        assert "Connected" in out

    @respx.mock
    async def test_mixed_states_single_render(self, monkeypatch):
        """One connected, one unreachable, one not-configured — all
        render in the same output without crashing."""
        monkeypatch.setenv("ETERNITAS_API_URL", "https://eternitas.test")
        monkeypatch.setenv("WINDY_CLOUD_URL", "https://cloud.down")

        respx.get("https://eternitas.test/api/v1/health").mock(
            return_value=httpx.Response(200, json={})
        )
        respx.get("https://cloud.down/api/v1/health").mock(
            side_effect=httpx.ConnectError("refused")
        )

        out = await check_ecosystem_health()

        assert "Connected" in out
        assert "Unreachable" in out or "refused" in out
        assert "Not configured" in out  # at least Pro is still missing
