"""Phone number provisioning for Windy Fly agents.

Assigns a phone number from a Twilio pool on hatch.
Falls back to a SQLite-backed mock for local development.
"""

from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Mock phone number pool (used when Twilio isn't configured)
_MOCK_PHONE_POOL = [
    f"+1555{i:07d}" for i in range(1000, 1100)
]

_MOCK_PROVISION_SQL = """
CREATE TABLE IF NOT EXISTS mock_phone_assignments (
    id TEXT PRIMARY KEY,
    phone_number TEXT UNIQUE NOT NULL,
    passport_id TEXT NOT NULL,
    agent_name TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    released_at DATETIME
);
"""


@dataclass
class PhoneProvisionResult:
    """Result of phone number provisioning."""

    success: bool
    phone_number: str = ""
    error: str = ""
    is_mock: bool = False


async def provision_phone(
    passport_id: str,
    agent_name: str = "",
    area_code: str = "",
    db=None,
    config: dict | None = None,
) -> PhoneProvisionResult:
    """Provision a phone number for a newly hatched agent.

    Tries Twilio first (if TWILIO_ACCOUNT_SID is set with number-buying
    permissions), then Windy Cloud (if ecosystem.windy_cloud_url is set),
    falls back to mock phone pool.
    """
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")

    # Check if a number is already assigned
    existing = os.environ.get("TWILIO_PHONE_NUMBER", "")
    if existing:
        return PhoneProvisionResult(success=True, phone_number=existing)

    # Try Twilio real provisioning
    if account_sid and auth_token:
        try:
            return await _provision_twilio(account_sid, auth_token, area_code)
        except Exception as exc:
            logger.warning("Twilio provisioning failed, falling back to mock: %s", exc)

    # Mock mode
    if db is not None:
        return await _provision_mock(db, passport_id, agent_name)

    return PhoneProvisionResult(
        success=False,
        error="No Twilio credentials and no database for mock mode",
    )


async def release_phone(
    passport_id: str,
    phone_number: str,
    db=None,
) -> bool:
    """Release a phone number back to the pool.

    Called when a passport is revoked.
    """
    # For mock mode
    if db is not None:
        db.conn.executescript(_MOCK_PROVISION_SQL)
        db.execute(
            "UPDATE mock_phone_assignments SET status = 'released', released_at = ? "
            "WHERE passport_id = ? AND phone_number = ?",
            (datetime.now(timezone.utc).isoformat(), passport_id, phone_number),
        )
        db.commit()
        return True

    # Real Twilio release would go here
    return False


async def _provision_twilio(
    account_sid: str, auth_token: str, area_code: str
) -> PhoneProvisionResult:
    """Provision a real phone number via Twilio API."""
    import base64
    import json
    import urllib.request
    import urllib.parse

    # Search for available numbers
    search_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        f"/AvailablePhoneNumbers/US/Local.json"
    )
    params = {"SmsEnabled": "true", "VoiceEnabled": "true", "Limit": "1"}
    if area_code:
        params["AreaCode"] = area_code

    credentials = base64.b64encode(
        f"{account_sid}:{auth_token}".encode()
    ).decode()

    search_url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(search_url)
    req.add_header("Authorization", f"Basic {credentials}")

    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read().decode())

    numbers = data.get("available_phone_numbers", [])
    if not numbers:
        return PhoneProvisionResult(success=False, error="No available numbers")

    phone_number = numbers[0]["phone_number"]

    # Purchase the number
    buy_url = (
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}"
        f"/IncomingPhoneNumbers.json"
    )
    buy_data = urllib.parse.urlencode({"PhoneNumber": phone_number}).encode()
    buy_req = urllib.request.Request(buy_url, data=buy_data, method="POST")
    buy_req.add_header("Authorization", f"Basic {credentials}")

    with urllib.request.urlopen(buy_req, timeout=15) as resp:
        result = json.loads(resp.read().decode())

    return PhoneProvisionResult(
        success=True,
        phone_number=result.get("phone_number", phone_number),
    )


async def _provision_mock(
    db, passport_id: str, agent_name: str
) -> PhoneProvisionResult:
    """Provision a mock phone number from the local pool."""
    db.conn.executescript(_MOCK_PROVISION_SQL)

    # Check if already assigned
    existing = db.fetchone(
        "SELECT * FROM mock_phone_assignments WHERE passport_id = ? AND status = 'active'",
        (passport_id,),
    )
    if existing:
        return PhoneProvisionResult(
            success=True,
            phone_number=existing["phone_number"],
            is_mock=True,
        )

    # Find an unassigned number
    assigned = db.fetchall(
        "SELECT phone_number FROM mock_phone_assignments WHERE status = 'active'"
    )
    assigned_set = {r["phone_number"] for r in assigned}

    for number in _MOCK_PHONE_POOL:
        if number not in assigned_set:
            row_id = str(uuid.uuid4())
            db.execute(
                """INSERT INTO mock_phone_assignments
                   (id, phone_number, passport_id, agent_name)
                   VALUES (?, ?, ?, ?)""",
                (row_id, number, passport_id, agent_name),
            )
            db.commit()
            logger.info("Mock phone: assigned %s to %s", number, passport_id)
            return PhoneProvisionResult(
                success=True, phone_number=number, is_mock=True
            )

    return PhoneProvisionResult(
        success=False,
        error="Mock phone pool exhausted",
        is_mock=True,
    )
