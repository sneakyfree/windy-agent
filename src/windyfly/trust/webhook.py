"""Handler for Eternitas trust.changed webhooks.

Contract: `eternitas/docs/trust-api.md#trust.changed` (eternitas repo).
Either band pair OR clearance pair will be populated for a given
event — not necessarily both.

    {
        "event": "trust.changed",
        "event_type": "trust.changed",
        "passport": "ET26-K7BF-42MN",
        "passport_number": "ET26-K7BF-42MN",
        "reason": "integrity_band: good->fair",
        "old_band": "good",
        "new_band": "fair",
        "old_clearance": null,
        "new_clearance": null,
        "timestamp": "2026-04-16T..."
    }

On receipt we:
  1. Invalidate the cached trust snapshot so the next gated action
     re-fetches from Eternitas immediately.
  2. Rotate the wk_ bot key — the new band/clearance may unlock or
     revoke scopes.
  3. Notify the owner ("clearance improved" / "clearance dropped").
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# Integrity band rank (higher = better). Matches trust-api.md.
_BAND_RANK = {
    "critical": 0,
    "poor": 1,
    "fair": 2,
    "good": 3,
    "exceptional": 4,
}

_CLEARANCE_RANK = {
    "registered": 0,
    "verified": 1,
    "cleared": 2,
    "top_secret": 3,
    "eternal": 4,
}


@dataclass
class TrustChangedResult:
    passport: str
    old_band: str
    new_band: str
    old_clearance: str
    new_clearance: str
    direction: str
    cache_invalidated: bool = False
    key_rotated: bool = False
    owner_notified: bool = False


def _direction(
    old_band: str, new_band: str,
    old_clearance: str, new_clearance: str,
) -> str:
    """improved | dropped | unchanged.

    Picks whichever axis actually changed. If both, band wins
    (behavioural signal is the more urgent cause for rotation).
    """
    if old_band and new_band and old_band != new_band:
        o = _BAND_RANK.get(old_band, -1)
        n = _BAND_RANK.get(new_band, -1)
        if o >= 0 and n >= 0:
            if n > o:
                return "improved"
            if n < o:
                return "dropped"

    if old_clearance and new_clearance and old_clearance != new_clearance:
        o = _CLEARANCE_RANK.get(old_clearance, -1)
        n = _CLEARANCE_RANK.get(new_clearance, -1)
        if o >= 0 and n >= 0:
            if n > o:
                return "improved"
            if n < o:
                return "dropped"

    return "unchanged"


async def handle_trust_changed(payload: dict, db=None) -> TrustChangedResult:
    """Handle one trust.changed webhook delivery.

    Accepts either the new `passport_number`/`event_type` fields or the
    legacy `passport`/`event` aliases — Eternitas emits both.

    Never raises — all side effects are best-effort so a flaky sub-step
    (e.g. owner email down) can't reject the webhook and cause
    duplicate deliveries.
    """
    passport = payload.get("passport_number") or payload.get("passport", "")
    old_band = payload.get("old_band") or ""
    new_band = payload.get("new_band") or ""
    old_clearance = payload.get("old_clearance") or ""
    new_clearance = payload.get("new_clearance") or ""
    reason = payload.get("reason", "")

    result = TrustChangedResult(
        passport=passport,
        old_band=old_band,
        new_band=new_band,
        old_clearance=old_clearance,
        new_clearance=new_clearance,
        direction=_direction(old_band, new_band, old_clearance, new_clearance),
    )

    if not passport:
        logger.warning("trust.changed webhook missing passport; ignoring")
        return result

    try:
        from windyfly.trust.check import invalidate_trust_cache

        invalidate_trust_cache(passport, db=db)
        result.cache_invalidated = True
    except Exception as exc:
        logger.warning("trust cache invalidation failed: %s", exc)

    try:
        from windyfly.auth.bot_credentials import rotate_on_trust_change

        # Rotate on any material change — band or clearance.
        rotate_trigger = new_band or new_clearance or "updated"
        new_cred = await rotate_on_trust_change(rotate_trigger)
        result.key_rotated = new_cred is not None
    except Exception as exc:
        logger.warning("trust-change rotation failed: %s", exc)

    try:
        await _notify_owner(result, reason)
        result.owner_notified = True
    except Exception as exc:
        logger.warning("owner trust-change notification failed: %s", exc)

    return result


async def _notify_owner(result: TrustChangedResult, reason: str) -> None:
    """Send a one-liner to the owner about the trust change.

    Uses Windy Mail if configured; falls back to a log line when not.
    This is intentionally tiny — details live in the dashboard banner.
    """
    owner_email = os.environ.get("OWNER_EMAIL", "")
    mail_api = os.environ.get("WINDYMAIL_API_URL", "")

    subject_verb = {
        "improved": "improved",
        "dropped": "dropped",
        "unchanged": "updated",
    }[result.direction]

    if result.old_band and result.new_band and result.old_band != result.new_band:
        axis = "band"
        old, new = result.old_band, result.new_band
    elif result.old_clearance and result.new_clearance and result.old_clearance != result.new_clearance:
        axis = "clearance"
        old, new = result.old_clearance, result.new_clearance
    else:
        axis = "trust"
        old, new = "-", "-"

    subject = f"Agent {axis} {subject_verb}: {old} -> {new}"
    body = (
        f"Your agent's trust state changed.\n\n"
        f"Axis: {axis}\n"
        f"From: {old}\n"
        f"To:   {new}\n"
        f"Why:  {reason or 'unspecified'}\n\n"
        f"Check the trust dashboard for what this unlocks or locks."
    )

    if not owner_email or not mail_api:
        logger.info("Trust change: %s %s -> %s (%s)", result.passport, result.old_band, result.new_band, result.direction)
        return

    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key

    headers = await ecosystem_auth_header(
        fallback_token=os.environ.get("WINDYMAIL_JMAP_TOKEN", "")
                       or os.environ.get("WINDYMAIL_SERVICE_TOKEN", ""),
    )
    if not headers:
        return

    cred = await get_bot_key()
    target_url = f"{mail_api.rstrip('/')}/api/v1/send"
    async with httpx.AsyncClient(timeout=10.0) as client:
        with audit_bot_key_call(
            key_id=cred.key_id if cred else "",
            scope_used="mail:send",
            target_url=target_url,
        ) as ctx:
            resp = await client.post(
                target_url,
                json={
                    "to": [owner_email],
                    "subject": subject,
                    "body_text": body,
                    "mode": "independent",
                },
                headers=headers,
            )
            ctx["response_status"] = resp.status_code
