"""Provision a Windy Mail inbox during agent hatch."""

import os
import logging

import httpx

logger = logging.getLogger(__name__)


async def provision_mail(
    agent_name: str,
    eternitas_passport: str,
    owner_id: str,
    windy_identity_id: str = "",
    config: dict | None = None,
) -> dict | None:
    """Provision a Windy Mail inbox for a newly hatched agent.

    POST /api/v1/provision/bot
    Auth: X-Service-Token header

    Returns connection details dict on success, None on skip/failure.
    Mirrors the pattern of matrix_provision.py — never blocks hatch on failure.
    """
    api_url = ""
    if config:
        api_url = config.get("ecosystem", {}).get("windy_mail_url", "")
    if not api_url:
        api_url = os.environ.get("WINDYMAIL_API_URL", "https://api.windymail.ai")
    service_token = os.environ.get("WINDYMAIL_PROVISION_SERVICE_TOKEN") or os.environ.get("WINDYMAIL_SERVICE_TOKEN")
    # The agent's own EPT is the keyless auth path (windy-mail PR #62):
    # provisioning verifies it against the Eternitas JWKS and mints the
    # mailbox for the token's passport — no shared secret needed. The
    # EPT is captured + persisted at hatch (PR #247).
    ept = os.environ.get("ETERNITAS_PASSPORT_TOKEN")

    headers: dict[str, str] = {}
    if ept:
        headers["Authorization"] = f"Bearer {ept}"
    elif service_token:
        headers["X-Service-Token"] = service_token
    else:
        logger.info(
            "No ETERNITAS_PASSPORT_TOKEN or service token — skipping mail "
            "provisioning (hatch the agent first so it has a passport)"
        )
        return None

    identity_id = windy_identity_id or owner_id

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{api_url}/api/v1/provision/bot",
                json={
                    "eternitas_passport": eternitas_passport,
                    "agent_name": agent_name,
                    "owner_id": owner_id,
                    "windy_identity_id": identity_id,
                },
                headers=headers,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                _write_env("WINDYMAIL_EMAIL", data["email"])
                _write_env("WINDYMAIL_JMAP_TOKEN", data["jmap_token"])
                _write_env("WINDYMAIL_SMTP_PASSWORD", data.get("smtp_password", ""))
                _write_env("WINDYMAIL_IMAP_PASSWORD", data.get("imap_password", ""))
                _write_env("WINDYMAIL_JMAP_URL", data.get("jmap_url", "https://mail.windymail.ai/.well-known/jmap"))
                logger.info("Windy Mail inbox provisioned: %s", data["email"])
                return data
            else:
                logger.warning("Mail provisioning failed: %s %s", resp.status_code, resp.text)
                return None
    except httpx.ConnectError:
        logger.warning("Windy Mail is not available right now")
        return None
    except Exception as exc:
        logger.warning("Mail provisioning error: %s", exc)
        return None


def _write_env(key: str, value: str) -> None:
    """Write or update a key in the .env file."""
    if not value:
        return
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
