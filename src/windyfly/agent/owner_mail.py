"""One plain-text mail to the agent's owner through Windy Mail (OWNER_EMAIL + WINDYMAIL_API_URL), for alerts
raised outside any chat channel. The same send path as trust/webhook.py's _notify_owner. Not configured = a log
line only; never raises."""

from __future__ import annotations

import asyncio
import logging
import os

import httpx

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(os.environ.get("OWNER_EMAIL", "") and os.environ.get("WINDYMAIL_API_URL", ""))


async def send(subject: str, body: str) -> bool:
    owner_email = os.environ.get("OWNER_EMAIL", "")
    mail_api = os.environ.get("WINDYMAIL_API_URL", "")
    if not owner_email or not mail_api:
        logger.info("owner mail not configured; alert stays in the log: %s", subject)
        return False
    from windyfly.auth.audit import audit_bot_key_call
    from windyfly.auth.bot_credentials import ecosystem_auth_header, get_bot_key

    headers = await ecosystem_auth_header(
        fallback_token=os.environ.get("WINDYMAIL_JMAP_TOKEN", "") or os.environ.get("WINDYMAIL_SERVICE_TOKEN", ""))
    if not headers:
        return False
    cred = await get_bot_key()
    target_url = f"{mail_api.rstrip('/')}/api/v1/send"
    async with httpx.AsyncClient(timeout=10.0) as client:
        with audit_bot_key_call(key_id=cred.key_id if cred else "", scope_used="mail:send",
                                target_url=target_url) as ctx:
            resp = await client.post(target_url, headers=headers, json={
                "to": [owner_email], "subject": subject, "body_text": body, "mode": "independent"})
            ctx["response_status"] = resp.status_code
    return 200 <= resp.status_code < 300


def send_sync(subject: str, body: str) -> bool:
    try:
        return asyncio.run(send(subject, body))
    except Exception as exc:  # noqa: BLE001
        logger.warning("owner mail failed: %s", type(exc).__name__)
        return False
