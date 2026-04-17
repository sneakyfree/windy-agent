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

import httpx
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


def get_eternitas_client(db=None, config: dict | None = None):
    """Return the appropriate Eternitas client based on configuration.

    Checks ecosystem.eternitas_url from config, then ETERNITAS_API_URL env var.
    Uses real HTTP client when a URL is set, mock client otherwise.
    """
    api_url = ""
    if config:
        api_url = config.get("ecosystem", {}).get("eternitas_url", "")
    if not api_url:
        api_url = os.environ.get("ETERNITAS_API_URL", "")

    if api_url and not api_url.startswith("mock"):
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


async def link_passport_with_identity(
    passport_number: str,
    windy_identity_id: str,
    operator_email: str = "",
    owner_jwt: str = "",
    pro_url: str = "",
    cloud_url: str = "",
    timeout: float = 10.0,
) -> dict:
    """Tell Windy Pro and Windy Cloud about the passport ↔ identity link.

    POST {WINDY_PRO_URL}/api/v1/identity/link-passport
    POST {WINDY_CLOUD_URL}/api/v1/identity/link-passport

    Called after passport creation so both services hold the bridge
    between the Eternitas passport and the unified Windy identity.

    Skips gracefully in offline/standalone mode (no JWT, no identity id,
    or no service URL). Never raises — returns a summary dict.

    **Coupling note (P1-E3).** The same owner JWT is sent as the
    Bearer to both Pro and Cloud. This only works as long as the two
    services validate against a **shared JWKS** — typically
    ``https://{pro_host}/.well-known/jwks.json``. If Cloud ever
    diverges (e.g., rotates its signing key independently or
    validates against its own JWKS), one call will return 200 and the
    other 401. The ``summary`` dict surfaces that per-service, so
    callers can see a half-linked state rather than a global failure.
    The hatch orchestrator writes both results into
    ``HatchResult.identity_link_pro`` and ``identity_link_cloud``.
    If you divide these services, mint per-audience tokens here
    instead of reusing one owner JWT.
    """
    summary = {"pro": "skipped", "cloud": "skipped"}

    if not windy_identity_id:
        logger.info("Link-passport skipped: no windy_identity_id (offline/standalone hatch)")
        return summary
    if not passport_number:
        logger.info("Link-passport skipped: no passport_number")
        return summary

    pro = (pro_url or os.environ.get("WINDY_PRO_URL", "") or os.environ.get("WINDY_API_URL", "")).rstrip("/")
    cloud = (cloud_url or os.environ.get("WINDY_CLOUD_URL", "")).rstrip("/")
    jwt = owner_jwt or os.environ.get("WINDY_JWT", "")
    email = operator_email or os.environ.get("OWNER_EMAIL", "")

    payload = {
        "passport_number": passport_number,
        "windy_identity_id": windy_identity_id,
        "operator_email": email,
    }
    headers = {"Authorization": f"Bearer {jwt}"} if jwt else {}

    async with httpx.AsyncClient(timeout=timeout) as client:
        for label, base in (("pro", pro), ("cloud", cloud)):
            if not base:
                continue
            try:
                resp = await client.post(
                    f"{base}/api/v1/identity/link-passport",
                    json=payload,
                    headers=headers,
                )
                if resp.status_code in (200, 201, 204):
                    summary[label] = "linked"
                    logger.info("Passport %s linked with identity on %s", passport_number, label)
                else:
                    summary[label] = f"http_{resp.status_code}"
                    logger.warning(
                        "Link-passport on %s returned %s: %s",
                        label, resp.status_code, resp.text[:200],
                    )
            except httpx.RequestError as exc:
                summary[label] = f"error: {exc.__class__.__name__}"
                logger.warning("Link-passport on %s failed: %s", label, exc)

    return summary


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
