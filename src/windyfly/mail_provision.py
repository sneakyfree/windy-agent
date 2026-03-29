"""Provision a Windy Mail inbox during agent hatch."""

import os
import logging

import httpx

logger = logging.getLogger(__name__)


async def provision_mail(agent_name: str, eternitas_passport: str, owner_id: str) -> dict | None:
    """Provision a Windy Mail inbox for a newly hatched agent.

    Returns connection details dict on success, None on skip/failure.
    Mirrors the pattern of matrix_provision.py — never blocks hatch on failure.
    """
    api_url = os.environ.get("WINDYMAIL_API_URL", "https://api.windymail.ai")
    service_token = os.environ.get("WINDYMAIL_SERVICE_TOKEN")

    if not service_token:
        logger.info("WINDYMAIL_SERVICE_TOKEN not set — skipping mail provisioning")
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{api_url}/api/v1/provision/bot",
                json={
                    "eternitas_passport": eternitas_passport,
                    "agent_name": agent_name,
                    "owner_id": owner_id,
                },
                headers={"X-Service-Token": service_token},
            )
            if resp.status_code == 201:
                data = resp.json()
                _write_env("WINDYMAIL_EMAIL", data["email"])
                _write_env("WINDYMAIL_JMAP_TOKEN", data["jmap_token"])
                _write_env("WINDYMAIL_SMTP_PASSWORD", data["password"])
                _write_env("WINDYMAIL_JMAP_URL", data.get("jmap_url", "https://mail.windymail.ai/.well-known/jmap"))
                logger.info("Windy Mail inbox provisioned: %s", data["email"])
                return data
            else:
                logger.warning("Mail provisioning failed: %s %s", resp.status_code, resp.text)
                return None
    except Exception as exc:
        logger.warning("Mail provisioning error: %s", exc)
        return None


def _write_env(key: str, value: str) -> None:
    """Write or update a key in the .env file."""
    env_path = os.path.join(os.getcwd(), ".env")
    lines = []
    found = False

    if os.path.exists(env_path):
        with open(env_path) as f:
            lines = f.readlines()

    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(lines)
