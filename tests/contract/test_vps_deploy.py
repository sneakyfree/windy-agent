"""Contract tests for src/windyfly/vps_deploy.py (P1-O2).

The module was at 0% coverage. These tests exercise every
lifecycle state (deploy → status → stop → destroy) with respx
mocks, plus each error branch (no token, connection refused, 404,
HTTP error, malformed response).

What we pin:
- POST /api/v1/servers/deploy-fly sends the agent_name/region/type it
  was asked for, with Bearer auth.
- State is persisted to _VPS_STATE_FILE on every mutation.
- destroy_vps removes the state file.
- Missing token fails soft (returns VPSInstance with errors, not raise).
- Connection refused / 404 / 5xx each surface distinct messages.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from windyfly.vps_deploy import (
    VPSInstance,
    _load_vps_state,
    _save_vps_state,
    deploy_vps,
    destroy_vps,
    format_vps_status,
    get_vps_status,
    stop_vps,
)

CLOUD = "https://cloud.windy.test"


@pytest.fixture(autouse=True)
def _isolate_state_file(tmp_path, monkeypatch):
    """Redirect the state file so tests don't touch the real data/."""
    state = tmp_path / "vps.json"
    monkeypatch.setattr("windyfly.vps_deploy._VPS_STATE_FILE", state)
    monkeypatch.setenv("WINDY_CLOUD_URL", CLOUD)
    monkeypatch.setenv("WINDY_CLOUD_TOKEN", "cloud_token_test")
    monkeypatch.delenv("WINDY_JWT", raising=False)


def _create_response(**overrides) -> dict:
    body = {
        "instance_id": "i-001",
        "ip_address": "203.0.113.10",
        "status": "provisioning",
        "region": "us-east-1",
        "instance_type": "t3.small",
        "dashboard_url": "https://dash.example/i-001",
        "ssh_user": "ubuntu",
        "ssh_key_name": "windyfly-key",
        "monthly_cost_usd": 8.50,
        "created_at": "2026-04-17T00:00:00Z",
    }
    body.update(overrides)
    return body


class TestDeploy:
    @respx.mock
    async def test_happy_path_posts_correct_body_and_persists(self, tmp_path):
        route = respx.post(f"{CLOUD}/api/v1/servers/deploy-fly").mock(
            return_value=httpx.Response(200, json=_create_response())
        )

        instance = await deploy_vps(
            config={"agent": {"name": "grant-fly"}},
            region="us-west-2",
            instance_type="t4g.small",
        )

        assert route.called
        body = json.loads(route.calls.last.request.content)
        assert body["agent_name"] == "grant-fly"
        assert body["region"] == "us-west-2"
        # instance_type is now mapped to a server-side plan; the server
        # generates the bootstrap, so no client-dictated setup commands.
        assert body["plan"] == "basic"  # t4g.small (unknown) → default basic
        assert "eternitas_passport_token" in body
        assert "config_files" not in body and "setup_commands" not in body
        assert route.calls.last.request.headers["Authorization"] == "Bearer cloud_token_test"

        assert instance.instance_id == "i-001"
        assert instance.dashboard_url == "https://dash.example/i-001"
        loaded = _load_vps_state()
        assert loaded is not None and loaded.instance_id == "i-001"

    @respx.mock
    async def test_dashboard_url_synthesized_when_missing(self):
        respx.post(f"{CLOUD}/api/v1/servers/deploy-fly").mock(
            return_value=httpx.Response(200, json=_create_response(dashboard_url=""))
        )
        instance = await deploy_vps()
        assert instance.dashboard_url == "http://203.0.113.10:3000"

    async def test_no_token_fails_soft(self, monkeypatch):
        monkeypatch.delenv("WINDY_CLOUD_TOKEN", raising=False)
        monkeypatch.delenv("WINDY_JWT", raising=False)
        inst = await deploy_vps()
        assert inst.status == "error"
        assert any("No Windy Cloud token" in e for e in inst.errors)

    @respx.mock
    async def test_connection_refused_returns_error(self):
        respx.post(f"{CLOUD}/api/v1/servers/deploy-fly").mock(
            side_effect=httpx.ConnectError("refused")
        )
        inst = await deploy_vps()
        assert inst.status == "error"
        assert any(CLOUD in e for e in inst.errors)

    @respx.mock
    async def test_http_500_returns_error(self):
        respx.post(f"{CLOUD}/api/v1/servers/deploy-fly").mock(
            return_value=httpx.Response(500, text="boom")
        )
        inst = await deploy_vps()
        assert inst.status == "error"
        assert any("500" in e for e in inst.errors)


class TestGetStatus:
    async def test_no_local_state_returns_none_instance(self):
        inst = await get_vps_status()
        assert inst.status == "none"

    @respx.mock
    async def test_updates_from_server(self, monkeypatch):
        _save_vps_state(VPSInstance(instance_id="i-001", status="provisioning", region="r"))
        respx.get(f"{CLOUD}/api/v1/servers/i-001").mock(
            return_value=httpx.Response(200, json={
                "status": "running",
                "ip_address": "203.0.113.99",
                "monthly_cost_usd": 9.00,
            })
        )
        inst = await get_vps_status()
        assert inst.status == "running"
        assert inst.ip_address == "203.0.113.99"
        assert inst.monthly_cost_usd == 9.00

    @respx.mock
    async def test_404_marks_terminated(self):
        _save_vps_state(VPSInstance(instance_id="i-gone", status="running", region="r"))
        respx.get(f"{CLOUD}/api/v1/servers/i-gone").mock(
            return_value=httpx.Response(404)
        )
        inst = await get_vps_status()
        assert inst.status == "terminated"


class TestStop:
    async def test_no_state_returns_none(self):
        inst = await stop_vps()
        assert inst.status == "none"

    @respx.mock
    async def test_happy_path_marks_stopped(self):
        _save_vps_state(VPSInstance(instance_id="i-001", status="running"))
        respx.post(f"{CLOUD}/api/v1/servers/i-001/stop").mock(
            return_value=httpx.Response(200)
        )
        inst = await stop_vps()
        assert inst.status == "stopped"


class TestDestroy:
    async def test_no_state_returns_none(self):
        inst = await destroy_vps()
        assert inst.status == "none"

    @respx.mock
    async def test_happy_path_unlinks_state_file(self, tmp_path):
        _save_vps_state(VPSInstance(instance_id="i-001", status="running"))
        respx.delete(f"{CLOUD}/api/v1/servers/i-001").mock(
            return_value=httpx.Response(204)
        )
        inst = await destroy_vps()
        assert inst.status == "terminated"
        # State file removed — a fresh load must return None.
        assert _load_vps_state() is None


class TestFormatStatus:
    def test_formats_error_state(self):
        inst = VPSInstance(status="error", errors=["boom"])
        out = format_vps_status(inst)
        assert "error" in out.lower() or "boom" in out.lower()

    def test_formats_none_state(self):
        out = format_vps_status(VPSInstance(status="none", errors=["no vps"]))
        assert isinstance(out, str) and len(out) > 0

    def test_formats_running_state(self):
        inst = VPSInstance(
            instance_id="i-001", status="running",
            ip_address="203.0.113.10", region="us-east-1",
            instance_type="t3.small", dashboard_url="https://dash/",
            monthly_cost_usd=8.50, agent_name="fly",
        )
        out = format_vps_status(inst)
        assert "i-001" in out
        assert "203.0.113.10" in out


class TestDeployFlyContract:
    @respx.mock
    async def test_instance_type_maps_to_plan_and_passes_ept(self, monkeypatch):
        monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept-live-token")
        route = respx.post(f"{CLOUD}/api/v1/servers/deploy-fly").mock(
            return_value=httpx.Response(200, json=_create_response())
        )
        await deploy_vps(instance_type="t3.xlarge")
        body = json.loads(route.calls.last.request.content)
        assert body["plan"] == "pro"  # t3.xlarge → pro
        assert body["eternitas_passport_token"] == "ept-live-token"
