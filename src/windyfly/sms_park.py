"""SMS and phone numbers are PARKED until after launch (Grant, 2026-09-23).

One flag for every SMS / phone-number code path: ``WINDY_ENABLE_SMS=1``
(default: unset = parked). With it unset nothing here sends an SMS, builds an
SMS channel, or buys / mock-assigns a number. Phone numbers will come from one
owner-only Tier-2 telephony service, not from windy-agent.
"""

from __future__ import annotations

import os


def sms_enabled() -> bool:
    return os.environ.get("WINDY_ENABLE_SMS", "").strip() == "1"
