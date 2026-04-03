"""Provision an Eternitas passport during agent hatch.

Attempts the real Eternitas API first, falls back to MockEternitasClient
for local development. Writes ETERNITAS_PASSPORT to .env on success.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
import uuid
from dataclasses import dataclass

from rich.console import Console

from windyfly.eternitas.models import EternitasPassport, RegistrationRequest

from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)
console = Console()
PROJECT_ROOT = get_project_root()


@dataclass
class EternitasProvisionResult:
    """Result of Eternitas provisioning during hatch."""

    success: bool
    passport: EternitasPassport | None = None
    error: str = ""


def get_eternitas_client(db=None):
    """Return the appropriate Eternitas client based on configuration.

    Uses the real client if ETERNITAS_API_URL is set to a real endpoint.
    Falls back to MockEternitasClient (SQLite-backed) otherwise.
    """
    api_url = os.environ.get("ETERNITAS_API_URL", "")

    if api_url and api_url != "mock://local" and not api_url.startswith("mock"):
        from windyfly.eternitas.client import EternitasClient
        return EternitasClient(api_url=api_url)

    # Mock mode — need a database
    if db is None:
        from windyfly.memory.database import Database
        db_path = os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")
        db = Database(db_path)

    from windyfly.eternitas.mock import MockEternitasClient
    return MockEternitasClient(db)


async def _do_provision(
    agent_name: str,
    owner_id: str = "",
    owner_name: str = "",
    db=None,
) -> EternitasProvisionResult:
    """Internal async provisioning logic."""
    client = get_eternitas_client(db=db)

    # Check for existing passport in env
    existing_passport = os.environ.get("ETERNITAS_PASSPORT", "")
    if existing_passport:
        passport = await client.verify(existing_passport)
        if passport and passport.status == "active":
            return EternitasProvisionResult(success=True, passport=passport)

    # Register new bot
    request = RegistrationRequest(
        name=agent_name,
        description=f"Windy Fly agent for {owner_name or owner_id or 'user'}",
        bot_type="personal_assistant",
        contact_email=os.environ.get("OWNER_EMAIL", ""),
        intended_platforms=["windy_chat", "windy_mail"],
        owner_id=owner_id,
        owner_name=owner_name,
        model_id=os.environ.get("DEFAULT_MODEL", ""),
        hatch_machine_id=_get_machine_id(),
    )

    try:
        passport = await client.register(request)
    except Exception as exc:
        logger.warning("Eternitas registration failed: %s", exc)
        return EternitasProvisionResult(success=False, error=str(exc))

    # Write passport ID to .env
    _write_env("ETERNITAS_PASSPORT", passport.passport_id)
    os.environ["ETERNITAS_PASSPORT"] = passport.passport_id

    return EternitasProvisionResult(success=True, passport=passport)


def provision_eternitas(
    agent_name: str,
    owner_id: str = "",
    owner_name: str = "",
    db=None,
) -> EternitasProvisionResult:
    """Synchronous wrapper for Eternitas provisioning.

    Called by the hatch orchestrator. Never raises — failures are
    captured in the result object.
    """
    try:
        return asyncio.run(_do_provision(agent_name, owner_id, owner_name, db))
    except RuntimeError:
        # Already in an event loop — use nest_asyncio pattern
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(
            _do_provision(agent_name, owner_id, owner_name, db)
        )


def _get_machine_id() -> str:
    """Get a stable machine identifier."""
    try:
        return str(uuid.getnode())
    except Exception as e:
        logger.debug("uuid.getnode() failed: %s", e)
        return platform.node()


def _write_env(key: str, value: str) -> None:
    """Write or update a key in the project .env file."""
    env_path = PROJECT_ROOT / ".env"
    lines: list[str] = []
    found = False

    if env_path.exists():
        lines = env_path.read_text().splitlines(keepends=True)

    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}\n"
            found = True
            break

    if not found:
        lines.append(f"{key}={value}\n")

    env_path.write_text("".join(lines))
