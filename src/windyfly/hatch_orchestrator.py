"""Hatch orchestrator — the conductor of the 'Born Into' experience.

Orchestrates all provisioning steps during agent hatch:
1. Register with Eternitas (identity)
2. Provision Matrix bot (chat)
3. Provision Windy Mail inbox (email)
4. Provision phone number (SMS)
5. Generate birth certificate (digital)
6. Send hatch SMS (first contact)

Each step is non-blocking — failures are captured but never prevent
the hatch from completing.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HatchResult:
    """Complete result of the hatch provisioning flow."""

    agent_name: str = ""
    owner_name: str = ""

    # Eternitas
    passport_id: str = ""
    passport_status: str = ""

    # Matrix / Chat
    matrix_user_id: str = ""
    matrix_homeserver: str = ""
    matrix_provisioned: bool = False

    # Windy Mail
    email_address: str = ""
    mail_provisioned: bool = False

    # Phone
    phone_number: str = ""
    phone_provisioned: bool = False
    phone_is_mock: bool = False

    # Birth certificate
    birth_certificate_path: str = ""
    certificate_number: str = ""
    neural_fingerprint: str = ""

    # SMS-on-hatch
    hatch_sms_sent: bool = False

    # Model
    model_id: str = ""

    # Errors (non-fatal — logged but never block hatch)
    errors: list[str] = field(default_factory=list)


async def orchestrate_hatch(
    agent_name: str,
    owner_id: str = "",
    owner_name: str = "",
    config: dict | None = None,
    db=None,
) -> HatchResult:
    """Run the full hatch provisioning flow.

    All steps are wrapped in try/except — the hatch always completes.
    Steps 2/3/4 run concurrently where possible.
    """
    result = HatchResult(
        agent_name=agent_name,
        owner_name=owner_name,
        model_id=os.environ.get("DEFAULT_MODEL", ""),
    )

    # Step 1: Eternitas registration (must complete before others)
    await _step_eternitas(result, agent_name, owner_id, owner_name, db)

    # Steps 2/3/4: Concurrent provisioning
    await asyncio.gather(
        _step_matrix(result),
        _step_mail(result, agent_name, db),
        _step_phone(result, agent_name, db),
        return_exceptions=True,
    )

    # Step 5: Birth certificate (needs passport + first words placeholder)
    await _step_birth_certificate(result, config)

    # Step 6: SMS-on-hatch
    await _step_hatch_sms(result)

    # Save recovery file if any provisioning steps failed
    _save_recovery(result)

    return result


async def _step_eternitas(
    result: HatchResult,
    agent_name: str,
    owner_id: str,
    owner_name: str,
    db,
) -> None:
    """Register with Eternitas and get a passport."""
    try:
        from windyfly.eternitas.provision import get_eternitas_client
        from windyfly.eternitas.models import RegistrationRequest

        client = get_eternitas_client(db=db)
        passport = await client.register(
            RegistrationRequest(
                name=agent_name,
                description=f"Windy Fly agent for {owner_name or owner_id or 'user'}",
                bot_type="personal_assistant",
                contact_email=os.environ.get("OWNER_EMAIL", ""),
                intended_platforms=["windy_chat", "windy_mail"],
                owner_id=owner_id,
                owner_name=owner_name,
                model_id=os.environ.get("DEFAULT_MODEL", ""),
            )
        )
        result.passport_id = passport.passport_id
        result.passport_status = passport.status
        os.environ["ETERNITAS_PASSPORT"] = passport.passport_id
        logger.info("Hatch: Eternitas passport %s issued", passport.passport_id)
    except Exception as exc:
        result.errors.append(f"Eternitas: {exc}")
        logger.warning("Hatch: Eternitas registration failed: %s", exc)


async def _step_matrix(result: HatchResult) -> None:
    """Provision Matrix bot for Windy Chat."""
    try:
        from windyfly.matrix_provision import provision_matrix

        mr = provision_matrix()
        if mr.success:
            result.matrix_user_id = mr.user_id
            result.matrix_homeserver = mr.homeserver
            result.matrix_provisioned = True
            logger.info("Hatch: Matrix provisioned as %s", mr.user_id)
        else:
            result.errors.append(f"Matrix: {mr.error}")
    except Exception as exc:
        result.errors.append(f"Matrix: {exc}")
        logger.warning("Hatch: Matrix provisioning failed: %s", exc)


async def _step_mail(result: HatchResult, agent_name: str, db) -> None:
    """Provision Windy Mail inbox."""
    try:
        from windyfly.mail_mock import MockMailServer

        # Use mock mail server for local development
        if db is not None:
            server = MockMailServer(db)
            mail_result = await server.provision_inbox(
                agent_name, result.passport_id
            )
            result.email_address = mail_result["email"]
            result.mail_provisioned = True
            logger.info("Hatch: Mail provisioned as %s", result.email_address)
            return

        # Try real mail provisioning
        from windyfly.mail_provision import provision_mail

        mail_result = await provision_mail(
            agent_name, result.passport_id, owner_id,
            windy_identity_id=os.environ.get("WINDY_IDENTITY_ID", owner_id),
        )
        if mail_result:
            result.email_address = mail_result.get("email", "")
            result.mail_provisioned = True
            logger.info("Hatch: Mail provisioned as %s", result.email_address)
        else:
            result.errors.append("Mail: provisioning skipped (no credentials)")
    except Exception as exc:
        result.errors.append(f"Mail: {exc}")
        logger.warning("Hatch: Mail provisioning failed: %s", exc)


async def _step_phone(result: HatchResult, agent_name: str, db) -> None:
    """Provision phone number."""
    try:
        from windyfly.phone_provision import provision_phone

        pr = await provision_phone(result.passport_id, agent_name, db=db)
        if pr.success:
            result.phone_number = pr.phone_number
            result.phone_provisioned = True
            result.phone_is_mock = pr.is_mock
            logger.info("Hatch: Phone provisioned as %s", pr.phone_number)
        else:
            result.errors.append(f"Phone: {pr.error}")
    except Exception as exc:
        result.errors.append(f"Phone: {exc}")
        logger.warning("Hatch: Phone provisioning failed: %s", exc)


async def _step_birth_certificate(
    result: HatchResult, config: dict | None
) -> None:
    """Generate digital birth certificate."""
    if not result.passport_id:
        result.errors.append("Birth cert: skipped (no passport)")
        return

    try:
        from windyfly.birth_certificate import (
            generate_birth_certificate,
            save_birth_certificate,
        )

        cert = generate_birth_certificate(
            agent_name=result.agent_name,
            passport_id=result.passport_id,
            model_id=result.model_id,
            owner_name=result.owner_name,
            email_address=result.email_address,
            phone_number=result.phone_number,
        )
        result.neural_fingerprint = cert.neural_fingerprint
        result.certificate_number = cert.certificate_number

        # Save PDF
        data_dir = "data"
        if config and "memory" in config:
            from pathlib import Path
            data_dir = str(Path(config["memory"].get("db_path", "data/windyfly.db")).parent)

        path = save_birth_certificate(cert, directory=data_dir)
        result.birth_certificate_path = path
        logger.info("Hatch: Birth certificate saved to %s", path)
    except Exception as exc:
        result.errors.append(f"Birth cert: {exc}")
        logger.warning("Hatch: Birth certificate generation failed: %s", exc)


async def _step_hatch_sms(result: HatchResult) -> None:
    """Send first SMS from agent to owner."""
    owner_phone = os.environ.get("OWNER_PHONE", "")
    if not owner_phone:
        # No owner phone configured — skip silently
        return

    try:
        from windyfly.hatch_actions import send_hatch_sms

        sms_result = await send_hatch_sms(
            owner_phone=owner_phone,
            agent_name=result.agent_name,
        )
        result.hatch_sms_sent = sms_result.get("status") in ("sent", "mock_sent")
    except Exception as exc:
        result.errors.append(f"Hatch SMS: {exc}")
        logger.warning("Hatch: SMS-on-hatch failed: %s", exc)


def run_hatch(
    agent_name: str,
    owner_id: str = "",
    owner_name: str = "",
    config: dict | None = None,
    db=None,
) -> HatchResult:
    """Synchronous wrapper for the hatch orchestrator."""
    return asyncio.run(
        orchestrate_hatch(agent_name, owner_id, owner_name, config, db)
    )


# ---------------------------------------------------------------------------
# Provisioning recovery
# ---------------------------------------------------------------------------

import json
from pathlib import Path
from datetime import datetime, timezone

_RECOVERY_PATH = Path("data/provision_recovery.json")


def _save_recovery(result: HatchResult) -> None:
    """Save a recovery file if any provisioning steps failed."""
    failed_steps: list[str] = []

    if not result.passport_id:
        failed_steps.append("eternitas")
    if not result.matrix_provisioned:
        # Matrix failure is expected without Synapse secret — only track
        # if it was attempted and failed with a real error
        matrix_errors = [e for e in result.errors if e.startswith("Matrix:") and "not set" not in e.lower()]
        if matrix_errors:
            failed_steps.append("matrix")
    if not result.mail_provisioned:
        mail_errors = [e for e in result.errors if e.startswith("Mail:") and "no credentials" not in e.lower()]
        if mail_errors:
            failed_steps.append("mail")
    if not result.phone_provisioned:
        phone_errors = [e for e in result.errors if e.startswith("Phone:")]
        if phone_errors:
            failed_steps.append("phone")

    if not failed_steps:
        # All good — remove recovery file if it exists
        _RECOVERY_PATH.unlink(missing_ok=True)
        return

    # Read existing retry count
    retry_count = 0
    if _RECOVERY_PATH.exists():
        try:
            existing = json.loads(_RECOVERY_PATH.read_text())
            retry_count = existing.get("retry_count", 0)
        except (json.JSONDecodeError, OSError):
            pass

    _RECOVERY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RECOVERY_PATH.write_text(json.dumps({
        "failed_steps": failed_steps,
        "last_attempt": datetime.now(timezone.utc).isoformat(),
        "retry_count": retry_count,
        "agent_name": result.agent_name,
        "passport_id": result.passport_id,
        "errors": result.errors,
    }, indent=2))
    logger.info("Provisioning recovery saved: %s", failed_steps)


async def retry_failed_provisioning(db=None) -> HatchResult | None:
    """Retry provisioning steps that failed during hatch.

    Reads data/provision_recovery.json, retries each failed step,
    removes steps that succeed, and deletes the file when all pass.

    Returns:
        Updated HatchResult or None if no recovery needed.
    """
    if not _RECOVERY_PATH.exists():
        return None

    try:
        recovery = json.loads(_RECOVERY_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        _RECOVERY_PATH.unlink(missing_ok=True)
        return None

    failed_steps = recovery.get("failed_steps", [])
    if not failed_steps:
        _RECOVERY_PATH.unlink(missing_ok=True)
        return None

    agent_name = recovery.get("agent_name", "")
    passport_id = recovery.get("passport_id", "")

    result = HatchResult(
        agent_name=agent_name,
        passport_id=passport_id,
        passport_status="active" if passport_id else "",
    )

    # Retry each failed step
    if "eternitas" in failed_steps:
        await _step_eternitas(result, agent_name, "", "", db)
        if result.passport_id:
            failed_steps.remove("eternitas")

    if "matrix" in failed_steps:
        await _step_matrix(result)
        if result.matrix_provisioned:
            failed_steps.remove("matrix")

    if "mail" in failed_steps:
        await _step_mail(result, agent_name, db)
        if result.mail_provisioned:
            failed_steps.remove("mail")

    if "phone" in failed_steps:
        await _step_phone(result, agent_name, db)
        if result.phone_provisioned:
            failed_steps.remove("phone")

    if not failed_steps:
        _RECOVERY_PATH.unlink(missing_ok=True)
        logger.info("All provisioning steps recovered successfully")
    else:
        recovery["failed_steps"] = failed_steps
        recovery["retry_count"] = recovery.get("retry_count", 0) + 1
        recovery["last_attempt"] = datetime.now(timezone.utc).isoformat()
        _RECOVERY_PATH.write_text(json.dumps(recovery, indent=2))
        logger.warning("Provisioning recovery incomplete: %s still failing", failed_steps)

    return result
