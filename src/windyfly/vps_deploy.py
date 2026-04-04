"""VPS deployment — deploy a Windy Fly agent to a cloud VPS.

Uses Windy Cloud's server provisioning API to create AWS EC2 instances,
upload agent config, install the agent, and manage the lifecycle.

Endpoints:
    POST   /api/v1/servers/create   — Provision a new VPS
    GET    /api/v1/servers/{id}     — Get server status
    POST   /api/v1/servers/{id}/stop — Stop server
    DELETE /api/v1/servers/{id}     — Destroy server
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field

import httpx

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

PROJECT_ROOT = get_project_root()
_VPS_STATE_FILE = PROJECT_ROOT / "data" / "vps_instance.json"
_TIMEOUT = 30.0


@dataclass
class VPSInstance:
    """Represents a deployed VPS instance."""

    instance_id: str = ""
    ip_address: str = ""
    status: str = ""  # provisioning, running, stopped, terminated
    region: str = ""
    instance_type: str = ""
    dashboard_url: str = ""
    ssh_user: str = "ubuntu"
    ssh_key_name: str = ""
    monthly_cost_usd: float = 0.0
    created_at: str = ""
    agent_name: str = ""
    errors: list[str] = field(default_factory=list)


def _get_cloud_url(config: dict | None = None) -> str:
    """Get the Windy Cloud API URL from config or env."""
    if config:
        url = config.get("ecosystem", {}).get("windy_cloud_url", "")
        if url:
            return url.rstrip("/")
    return os.environ.get("WINDY_CLOUD_URL", "https://cloud.windyfly.ai").rstrip("/")


def _get_cloud_token() -> str:
    """Get auth token for Windy Cloud API."""
    return os.environ.get("WINDY_CLOUD_TOKEN", "") or os.environ.get("WINDY_JWT", "")


def _save_vps_state(instance: VPSInstance) -> None:
    """Persist VPS instance state to disk."""
    _VPS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _VPS_STATE_FILE.write_text(json.dumps({
        "instance_id": instance.instance_id,
        "ip_address": instance.ip_address,
        "status": instance.status,
        "region": instance.region,
        "instance_type": instance.instance_type,
        "dashboard_url": instance.dashboard_url,
        "ssh_user": instance.ssh_user,
        "ssh_key_name": instance.ssh_key_name,
        "monthly_cost_usd": instance.monthly_cost_usd,
        "created_at": instance.created_at,
        "agent_name": instance.agent_name,
    }, indent=2))


def _load_vps_state() -> VPSInstance | None:
    """Load VPS instance state from disk."""
    if not _VPS_STATE_FILE.exists():
        return None
    try:
        data = json.loads(_VPS_STATE_FILE.read_text())
        return VPSInstance(**data)
    except (json.JSONDecodeError, TypeError):
        return None


async def deploy_vps(
    config: dict | None = None,
    region: str = "us-east-1",
    instance_type: str = "t3.small",
) -> VPSInstance:
    """Provision a new VPS and deploy the agent to it.

    1. Calls Windy Cloud to create an EC2 instance
    2. Uploads agent config (windyfly.toml, SOUL.md, API keys)
    3. Installs windy-agent on the VPS
    4. Starts the agent as a systemd service

    Returns VPSInstance with connection details.
    """
    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    if not token:
        return VPSInstance(
            status="error",
            errors=["No Windy Cloud token. Set WINDY_CLOUD_TOKEN or WINDY_JWT."],
        )

    agent_name = "Windy Fly"
    if config:
        agent_name = config.get("agent", {}).get("name", "Windy Fly")

    # Collect config files to upload
    config_files = _collect_config_files()

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            # Step 1: Create server
            resp = await client.post(
                f"{cloud_url}/api/v1/servers/create",
                json={
                    "agent_name": agent_name,
                    "region": region,
                    "instance_type": instance_type,
                    "config_files": config_files,
                    "setup_commands": [
                        "curl -LsSf https://astral.sh/uv/install.sh | sh",
                        "uv pip install windyfly",
                        "systemctl --user enable windyfly",
                        "systemctl --user start windyfly",
                    ],
                },
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()

            instance = VPSInstance(
                instance_id=data.get("instance_id", ""),
                ip_address=data.get("ip_address", ""),
                status=data.get("status", "provisioning"),
                region=data.get("region", region),
                instance_type=data.get("instance_type", instance_type),
                dashboard_url=data.get("dashboard_url", ""),
                ssh_user=data.get("ssh_user", "ubuntu"),
                ssh_key_name=data.get("ssh_key_name", ""),
                monthly_cost_usd=data.get("monthly_cost_usd", 0.0),
                created_at=data.get("created_at", ""),
                agent_name=agent_name,
            )

            if not instance.dashboard_url and instance.ip_address:
                instance.dashboard_url = f"http://{instance.ip_address}:3000"

            _save_vps_state(instance)
            logger.info("VPS deployed: %s (%s)", instance.instance_id, instance.ip_address)
            return instance

    except httpx.ConnectError:
        return VPSInstance(status="error", errors=[f"Cannot reach Windy Cloud at {cloud_url}"])
    except httpx.HTTPStatusError as e:
        return VPSInstance(status="error", errors=[f"Windy Cloud error: {e.response.status_code} {e.response.text[:200]}"])
    except Exception as e:
        return VPSInstance(status="error", errors=[str(e)])


async def get_vps_status(config: dict | None = None) -> VPSInstance:
    """Get current VPS instance status from Windy Cloud."""
    local = _load_vps_state()
    if not local or not local.instance_id:
        return VPSInstance(status="none", errors=["No VPS deployed. Run: windy deploy --vps"])

    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    if not token:
        # Return local state with warning
        local.errors.append("No cloud token — showing cached state")
        return local

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{cloud_url}/api/v1/servers/{local.instance_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            if resp.status_code == 404:
                local.status = "terminated"
                _save_vps_state(local)
                return local
            resp.raise_for_status()
            data = resp.json()

            local.status = data.get("status", local.status)
            local.ip_address = data.get("ip_address", local.ip_address)
            local.monthly_cost_usd = data.get("monthly_cost_usd", local.monthly_cost_usd)
            _save_vps_state(local)
            return local

    except httpx.ConnectError:
        local.errors.append(f"Cannot reach Windy Cloud at {cloud_url}")
        return local
    except Exception as e:
        local.errors.append(str(e))
        return local


async def stop_vps(config: dict | None = None) -> VPSInstance:
    """Stop the VPS instance (can be restarted later)."""
    local = _load_vps_state()
    if not local or not local.instance_id:
        return VPSInstance(status="none", errors=["No VPS deployed"])

    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{cloud_url}/api/v1/servers/{local.instance_id}/stop",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            local.status = "stopped"
            _save_vps_state(local)
            return local
    except Exception as e:
        local.errors.append(str(e))
        return local


async def destroy_vps(config: dict | None = None) -> VPSInstance:
    """Permanently destroy the VPS instance."""
    local = _load_vps_state()
    if not local or not local.instance_id:
        return VPSInstance(status="none", errors=["No VPS deployed"])

    cloud_url = _get_cloud_url(config)
    token = _get_cloud_token()

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.delete(
                f"{cloud_url}/api/v1/servers/{local.instance_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            local.status = "terminated"
            _save_vps_state(local)
            _VPS_STATE_FILE.unlink(missing_ok=True)
            return local
    except Exception as e:
        local.errors.append(str(e))
        return local


def _collect_config_files() -> dict[str, str]:
    """Collect agent config files for upload to VPS."""
    files: dict[str, str] = {}

    toml_path = PROJECT_ROOT / "windyfly.toml"
    if toml_path.exists():
        files["windyfly.toml"] = toml_path.read_text()

    soul_path = PROJECT_ROOT / "SOUL.md"
    if soul_path.exists():
        files["SOUL.md"] = soul_path.read_text()

    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        # Filter out local-only vars, keep secrets
        filtered = []
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                filtered.append(line)
                continue
            key = stripped.split("=", 1)[0]
            # Skip local paths and localhost URLs
            if key in ("WINDYFLY_DB_PATH", "SYNAPSE_ADMIN_URL"):
                continue
            filtered.append(line)
        files[".env"] = "\n".join(filtered)

    return files


def format_vps_status(instance: VPSInstance) -> str:
    """Format VPS status for terminal display."""
    if instance.status == "none":
        return "\n".join(instance.errors) if instance.errors else "No VPS deployed."

    if instance.status == "error":
        return "VPS deployment failed:\n" + "\n".join(f"  \u2717 {e}" for e in instance.errors)

    lines = [
        f"\U0001f5a5\ufe0f  Windy Fly VPS — {instance.agent_name}",
        "",
        f"  Instance:   {instance.instance_id}",
        f"  Status:     {_status_icon(instance.status)} {instance.status}",
        f"  IP:         {instance.ip_address}",
        f"  Region:     {instance.region}",
        f"  Type:       {instance.instance_type}",
        f"  Dashboard:  {instance.dashboard_url}",
        f"  SSH:        ssh {instance.ssh_user}@{instance.ip_address}",
        f"  Cost:       ${instance.monthly_cost_usd:.2f}/month",
    ]
    if instance.created_at:
        lines.append(f"  Created:    {instance.created_at}")
    if instance.errors:
        lines.append("")
        for e in instance.errors:
            lines.append(f"  \u26a0\ufe0f  {e}")
    return "\n".join(lines)


def _status_icon(status: str) -> str:
    """Return an icon for a VPS status string."""
    return {
        "provisioning": "\U0001f504",
        "running": "\u2705",
        "stopped": "\u23f8\ufe0f",
        "terminated": "\u274c",
    }.get(status, "\u2753")
