"""Physical birth certificate mailing via print-on-demand API.

Integrates with Lob.com (or similar) to print and mail a physical
birth certificate to the agent's owner.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class MailResult:
    """Result of a physical mail send."""

    success: bool = False
    tracking_id: str = ""
    expected_delivery: str = ""
    error: str = ""


async def mail_birth_certificate(
    pdf_bytes: bytes,
    recipient_name: str,
    recipient_address: dict,
) -> MailResult:
    """Mail a physical birth certificate to the owner.

    Args:
        pdf_bytes: Raw PDF bytes of the birth certificate.
        recipient_name: Name of the recipient.
        recipient_address: Dict with keys: line1, line2, city, state, zip, country.

    The Lob.com API (or similar) handles printing and USPS mailing.
    """
    api_key = os.environ.get("BIRTH_CERT_MAIL_API_KEY", "")

    if not api_key:
        logger.info("No BIRTH_CERT_MAIL_API_KEY — physical mailing skipped")
        return MailResult(error="Print/mail API not configured")

    try:
        import httpx
        import base64

        pdf_b64 = base64.b64encode(pdf_bytes).decode()

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.lob.com/v1/letters",
                json={
                    "to": {
                        "name": recipient_name,
                        "address_line1": recipient_address.get("line1", ""),
                        "address_line2": recipient_address.get("line2", ""),
                        "address_city": recipient_address.get("city", ""),
                        "address_state": recipient_address.get("state", ""),
                        "address_zip": recipient_address.get("zip", ""),
                        "address_country": recipient_address.get("country", "US"),
                    },
                    "from": {
                        "name": "Windy Fly Registry",
                        "address_line1": "",  # Configured in Lob dashboard
                    },
                    "file": pdf_b64,
                    "color": True,
                    "description": "Windy Fly Birth Certificate",
                },
                headers={"Authorization": f"Basic {api_key}"},
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return MailResult(
                    success=True,
                    tracking_id=data.get("id", ""),
                    expected_delivery=data.get("expected_delivery_date", ""),
                )
            return MailResult(error=f"API returned {resp.status_code}")
    except Exception as exc:
        return MailResult(error=str(exc))
