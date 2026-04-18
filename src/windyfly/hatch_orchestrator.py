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
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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

    # Windy Cloud storage allocation
    cloud_plan_id: str = ""
    cloud_quota_bytes: int = 0
    cloud_provisioned: bool = False

    # Identity link-back status
    identity_link_pro: str = ""
    identity_link_cloud: str = ""

    # Hardware
    hardware_specs: dict = field(default_factory=dict)

    # Birth certificate
    birth_certificate_path: str = ""
    certificate_number: str = ""
    neural_fingerprint: str = ""

    # SMS-on-hatch
    hatch_sms_sent: bool = False

    # Email-on-hatch
    hatch_email_sent: bool = False

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

    # Collect hardware specs early (used by birth certificate)
    try:
        from windyfly.birth_certificate import collect_hardware_specs
        result.hardware_specs = collect_hardware_specs()
    except Exception as e:
        logger.debug("Hardware spec collection failed: %s", e)

    # Step 1: Eternitas registration (must complete before others)
    await _step_eternitas(result, agent_name, owner_id, owner_name, db, config)

    # Step 1b: Link the passport with the unified Windy identity so
    # Pro and Cloud both hold the bridge. Skips gracefully in offline
    # mode (no owner JWT / no windy_identity_id).
    await _step_link_passport(result, owner_id)

    # Steps 2/3/4: Concurrent provisioning
    await asyncio.gather(
        _step_matrix(result, config),
        _step_mail(result, agent_name, db, owner_id, config),
        _step_phone(result, agent_name, db, config),
        return_exceptions=True,
    )

    # Step 4b: Allocate Windy Cloud storage quota so the agent is born
    # with a cloud home. Runs before birth cert so plan/quota appear
    # on the certificate's "Cloud Storage" line.
    await _step_cloud_quota(result, owner_id)

    # Step 5: Birth certificate (needs passport + first words placeholder)
    await _step_birth_certificate(result, config)

    # Step 6: SMS-on-hatch
    await _step_hatch_sms(result)

    # Step 7: Email birth announcement
    await _step_hatch_email(result)

    # Save recovery file if any provisioning steps failed
    _save_recovery(result)

    return result


async def _step_eternitas(
    result: HatchResult,
    agent_name: str,
    owner_id: str,
    owner_name: str,
    db,
    config: dict | None = None,
) -> None:
    """Register with Eternitas and get a passport."""
    try:
        from windyfly.eternitas.provision import get_eternitas_client
        from windyfly.eternitas.models import RegistrationRequest

        client = get_eternitas_client(db=db, config=config)
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


async def _step_link_passport(result: HatchResult, owner_id: str) -> None:
    """Register the passport ↔ identity link with Pro and Cloud."""
    if not result.passport_id:
        return
    windy_identity_id = os.environ.get("WINDY_IDENTITY_ID", "") or owner_id
    if not windy_identity_id:
        logger.info("Hatch: link-passport skipped (offline hatch, no windy identity)")
        return
    try:
        from windyfly.eternitas.provision import link_passport_with_identity

        summary = await link_passport_with_identity(
            passport_number=result.passport_id,
            windy_identity_id=windy_identity_id,
            operator_email=os.environ.get("OWNER_EMAIL", ""),
        )
        result.identity_link_pro = summary.get("pro", "")
        result.identity_link_cloud = summary.get("cloud", "")
    except Exception as exc:
        result.errors.append(f"Link-passport: {exc}")
        logger.warning("Hatch: link-passport failed: %s", exc)


async def _step_cloud_quota(result: HatchResult, owner_id: str) -> None:
    """Allocate a Windy Cloud storage plan for this agent."""
    if not result.passport_id:
        return
    if not os.environ.get("WINDY_CLOUD_URL", ""):
        logger.info("Hatch: cloud quota skipped (WINDY_CLOUD_URL not set)")
        return

    windy_identity_id = os.environ.get("WINDY_IDENTITY_ID", "") or owner_id
    try:
        from windyfly.cloud_provision import allocate_cloud_quota

        alloc = await allocate_cloud_quota(
            windy_identity_id=windy_identity_id,
            passport_number=result.passport_id,
            tier="free",
        )
        if alloc:
            result.cloud_plan_id = alloc.plan_id
            result.cloud_quota_bytes = alloc.quota_bytes
            result.cloud_provisioned = True
            logger.info("Hatch: cloud quota %s (%d bytes) provisioned", alloc.plan_id, alloc.quota_bytes)
        else:
            result.errors.append("Cloud quota: allocation failed")
    except Exception as exc:
        result.errors.append(f"Cloud quota: {exc}")
        logger.warning("Hatch: cloud quota failed: %s", exc)


async def _step_matrix(result: HatchResult, config: dict | None = None) -> None:
    """Provision Matrix bot for Windy Chat."""
    try:
        from windyfly.matrix_provision import provision_matrix

        mr = provision_matrix(config=config)
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


async def _step_mail(result: HatchResult, agent_name: str, db, owner_id: str = "", config: dict | None = None) -> None:
    """Provision Windy Mail inbox."""
    try:
        # Check if a real mail URL is explicitly configured in the config file
        mail_url = config.get("ecosystem", {}).get("windy_mail_url", "") if config else ""

        # If a real URL is set in config → use real provisioning
        if mail_url and not mail_url.startswith("mock"):
            from windyfly.mail_provision import provision_mail

            mail_result = await provision_mail(
                agent_name, result.passport_id, owner_id,
                windy_identity_id=os.environ.get("WINDY_IDENTITY_ID", owner_id),
                config=config,
            )
            if mail_result:
                result.email_address = mail_result.get("email", "")
                result.mail_provisioned = True
                logger.info("Hatch: Mail provisioned as %s", result.email_address)
            else:
                result.errors.append("Mail: provisioning failed (service unavailable)")
            return

        # Use mock mail server for local development when db is available
        if db is not None:
            from windyfly.mail_mock import MockMailServer

            server = MockMailServer(db)
            mail_result = await server.provision_inbox(
                agent_name, result.passport_id
            )
            result.email_address = mail_result["email"]
            result.mail_provisioned = True
            logger.info("Hatch: Mail provisioned as %s (mock)", result.email_address)
            return

        # No config URL and no db — try real provisioning via env var as fallback
        from windyfly.mail_provision import provision_mail

        mail_result = await provision_mail(
            agent_name, result.passport_id, owner_id,
            windy_identity_id=os.environ.get("WINDY_IDENTITY_ID", owner_id),
            config=config,
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


async def _step_phone(result: HatchResult, agent_name: str, db, config: dict | None = None) -> None:
    """Provision phone number."""
    try:
        from windyfly.phone_provision import provision_phone

        pr = await provision_phone(result.passport_id, agent_name, db=db, config=config)
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
            cloud_plan_id=result.cloud_plan_id,
            cloud_quota_bytes=result.cloud_quota_bytes,
            hardware_specs=result.hardware_specs or None,
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


async def _step_hatch_email(result: HatchResult) -> None:
    """Send birth announcement email to owner via Windy Mail."""
    owner_email = os.environ.get("OWNER_EMAIL", "")
    if not owner_email:
        return  # No owner email — skip silently

    mail_api_url = os.environ.get("WINDYMAIL_API_URL", "")
    mail_token = os.environ.get("WINDYMAIL_JMAP_TOKEN", "") or os.environ.get(
        "WINDYMAIL_SERVICE_TOKEN", ""
    )

    from windyfly.auth.bot_credentials import ecosystem_auth_header
    auth_header = await ecosystem_auth_header(fallback_token=mail_token)

    if not mail_api_url or not auth_header:
        result.errors.append("Birth email: Windy Mail not configured")
        return

    try:
        import base64
        from pathlib import Path
        from windyfly.hatch_email import format_hatch_email
        from datetime import datetime, timezone

        email_data = format_hatch_email(
            agent_name=result.agent_name,
            passport_id=result.passport_id,
            agent_email=result.email_address,
            agent_phone=result.phone_number,
            model_id=result.model_id,
            hatch_time=datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC"),
            dashboard_url="https://windyword.ai/app/fly",
            certificate_number=result.certificate_number,
            neural_fingerprint=result.neural_fingerprint,
        )

        # Attach the birth certificate PDF if available
        cert_attachment = None
        if result.birth_certificate_path:
            try:
                pdf_bytes = Path(result.birth_certificate_path).read_bytes()
                cert_attachment = base64.b64encode(pdf_bytes).decode("ascii")
            except Exception as e:
                logger.debug("PDF attachment read failed: %s", e)

        payload: dict = {
            "to": [owner_email],
            "subject": email_data["subject"],
            "body_text": email_data["text"],
            "body_html": email_data["html"],
            "mode": "independent",
        }
        if cert_attachment:
            payload["attachments"] = [{
                "filename": f"birth_certificate_{result.agent_name}.pdf",
                "content_base64": cert_attachment,
                "content_type": "application/pdf",
            }]

        import httpx

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{mail_api_url}/api/v1/send",
                json=payload,
                headers=auth_header,
            )
        if resp.status_code in (200, 201):
            result.hatch_email_sent = True
            logger.info("Birth announcement email sent to %s", owner_email)
        else:
            result.errors.append(f"Birth email: {resp.status_code}")
    except Exception as exc:
        result.errors.append(f"Birth email: {exc}")
        logger.warning("Birth announcement email failed: %s", exc)


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
