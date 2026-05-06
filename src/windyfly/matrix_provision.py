"""Matrix bot auto-provisioning for Windy Fly.

Registers ``@windyfly:chat.windychat.ai`` on the Synapse homeserver
using the admin registration API + shared secret.  Called during
``windy go`` / ``windy init`` so the user never touches Matrix config.

Requires:
    SYNAPSE_REGISTRATION_SECRET — shared secret from Synapse homeserver
    MATRIX_HOMESERVER           — homeserver URL (default: https://chat.windychat.ai)

If the secret is not available (e.g., user is running their own Synapse
or using a different homeserver), this gracefully skips and the user
can configure Matrix manually later.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
from dataclasses import dataclass

from rich.console import Console

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)

console = Console()
PROJECT_ROOT = get_project_root()


@dataclass
class MatrixProvisionResult:
    """Result of Matrix bot provisioning."""

    success: bool
    user_id: str = ""
    access_token: str = ""
    device_id: str = ""
    password: str = ""
    homeserver: str = ""
    error: str = ""


def provision_matrix_bot(
    homeserver: str | None = None,
    bot_username: str = "windyfly",
    display_name: str = "Windy Fly 🪰",
    registration_secret: str | None = None,
) -> dict[str, str] | None:
    """Register the Windy Fly bot on the Matrix homeserver.

    Returns:
        Dict with user_id, access_token, device_id on success.
        None if provisioning is skipped or fails.
    """
    import httpx

    homeserver = homeserver or os.environ.get(
        "MATRIX_HOMESERVER", "https://chat.windychat.ai"
    )
    secret = registration_secret or os.environ.get("SYNAPSE_REGISTRATION_SECRET", "")

    if not secret:
        # No shared secret — can't auto-provision
        return None

    # Strip trailing slash
    homeserver = homeserver.rstrip("/")

    # Use internal Synapse URL if available (Docker networking)
    admin_url = os.environ.get("SYNAPSE_ADMIN_URL", f"{homeserver}")

    try:
        # Step 1: Get nonce
        r = httpx.get(f"{admin_url}/_synapse/admin/v1/register", timeout=10)
        if r.status_code != 200:
            return None
        nonce = r.json()["nonce"]

        # Step 2: Generate password and HMAC
        password = secrets.token_hex(32)
        mac = _generate_mac(nonce, bot_username, password, admin=False, secret=secret)

        # Step 3: Register
        r = httpx.post(
            f"{admin_url}/_synapse/admin/v1/register",
            json={
                "nonce": nonce,
                "username": bot_username,
                "password": password,
                "displayname": display_name,
                "admin": False,
                "mac": mac,
            },
            timeout=15,
        )

        if r.status_code in (200, 201):
            data = r.json()
            return {
                "user_id": data["user_id"],
                "access_token": data["access_token"],
                "device_id": data.get("device_id", ""),
                "password": password,
            }

        # 400 with "User ID already taken" means bot exists — try login instead
        if r.status_code == 400:
            error_msg = r.json().get("error", "")
            if "already taken" in error_msg.lower() or "in use" in error_msg.lower():
                return _login_existing_bot(homeserver, bot_username, password)

        return None

    except Exception as e:
        logger.warning("Matrix bot provisioning failed: %s", e)
        return None


def _login_existing_bot(
    homeserver: str,
    username: str,
    password: str,
) -> dict[str, str] | None:
    """Login to an existing bot account to get a fresh access token."""
    import httpx

    try:
        r = httpx.post(
            f"{homeserver}/_matrix/client/v3/login",
            json={
                "type": "m.login.password",
                "identifier": {
                    "type": "m.id.user",
                    "user": username,
                },
                "password": password,
                "device_id": "WINDYFLY",
                "initial_device_display_name": "Windy Fly Agent",
            },
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return {
                "user_id": data["user_id"],
                "access_token": data["access_token"],
                "device_id": data.get("device_id", ""),
                "password": password,
            }
    except Exception as e:
        logger.warning("Matrix bot login failed: %s", e)
    return None


def _generate_mac(
    nonce: str,
    username: str,
    password: str,
    admin: bool,
    secret: str,
) -> str:
    """Generate HMAC-SHA1 for Synapse admin registration.

    Format: HMAC-SHA1(key=secret, msg=nonce\\x00username\\x00password\\x00admin_flag)
    """
    admin_flag = "admin" if admin else "notadmin"
    msg = "\x00".join([nonce, username, password, admin_flag])
    return hmac.new(
        secret.encode("utf-8"),
        msg.encode("utf-8"),
        hashlib.sha1,
    ).hexdigest()


def provision_matrix(
    homeserver: str | None = None,
    bot_username: str = "windyfly",
    display_name: str = "Windy Fly 🪰",
    config: dict | None = None,
) -> MatrixProvisionResult:
    """Provision Matrix bot and return structured result.

    Used by the hatch orchestrator. Never raises — failures are captured
    in the result object.
    """
    hs = homeserver
    if not hs and config:
        hs = config.get("ecosystem", {}).get("matrix_homeserver", "")
    if not hs:
        hs = os.environ.get("MATRIX_HOMESERVER", "")
    if not hs:
        hs = config.get("matrix", {}).get("homeserver", "https://chat.windychat.ai") if config else "https://chat.windychat.ai"
    raw = provision_matrix_bot(
        homeserver=hs,
        bot_username=bot_username,
        display_name=display_name,
    )
    if raw is None:
        return MatrixProvisionResult(
            success=False,
            homeserver=hs,
            error="No Synapse registration secret or provisioning failed",
        )
    return MatrixProvisionResult(
        success=True,
        user_id=raw["user_id"],
        access_token=raw["access_token"],
        device_id=raw.get("device_id", ""),
        password=raw.get("password", ""),
        homeserver=hs,
    )


def auto_provision_and_save() -> bool:
    """Attempt auto-provisioning and write credentials to .env.

    Returns True if Matrix was provisioned, False if skipped/failed.
    """
    console.print("  [cyan]Provisioning Windy Chat bot...[/cyan]")

    result = provision_matrix_bot()
    if result is None:
        console.print("  [dim]○ Windy Chat — skipped (no Synapse secret available)[/dim]")
        console.print("  [dim]  Set SYNAPSE_REGISTRATION_SECRET to enable auto-provisioning[/dim]")
        console.print("  [dim]  Or add MATRIX_BOT_TOKEN manually to .env[/dim]")
        return False

    # Write to .env
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        content = env_file.read_text()

        # Update or add MATRIX_BOT_TOKEN
        lines = content.splitlines()
        new_lines = []
        token_written = False
        password_written = False

        for line in lines:
            if line.startswith("MATRIX_BOT_TOKEN="):
                new_lines.append(f"MATRIX_BOT_TOKEN={result['access_token']}")
                token_written = True
            elif line.startswith("MATRIX_BOT_PASSWORD="):
                new_lines.append(f"MATRIX_BOT_PASSWORD={result['password']}")
                password_written = True
            else:
                new_lines.append(line)

        if not token_written:
            new_lines.append(f"MATRIX_BOT_TOKEN={result['access_token']}")
        if not password_written:
            new_lines.append(f"MATRIX_BOT_PASSWORD={result['password']}")

        env_file.write_text("\n".join(new_lines) + "\n")

    console.print(f"  [green]✓[/green] Windy Chat — {result['user_id']} provisioned")
    return True
