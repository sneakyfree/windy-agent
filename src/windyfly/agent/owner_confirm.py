"""ONE owner-confirm gate for outward actions (the lean Windy Fly, 2026-11-02).

Anything that reaches a person or the outside world on the owner's behalf asks ONE rule "ask the owner first?",
and only the OWNER's own yes approves. Boss's rules, unchanged:
- ON by default; only the exact lab switch ``WINDY_SEND_CONFIRM=0`` turns it off (Boss 2026-10-29). Unset, empty
  or anything else = on, so a missing or mistyped setting can't quietly reopen unconfirmed sends.
- One plain recipient the owner already approved goes straight out; a NEW recipient, or any bulk / cc / bcc send,
  asks (Boss ruling 10-07, rule 4).
- The yes is the owner's (band OWNER), handled in code (channels.base), never by the model and never a stranger's.

``OUTWARD`` names every outward tool and how its owner confirm happens; tests/test_owner_confirm_gate.py walks the
registered tools and fails when an outward tool is missing from it. The ask texts stay in each tool (word for word).
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from typing import Any

LAB_SWITCH = "WINDY_SEND_CONFIRM"

# How each outward tool gets the owner's yes.
GATE = "gate"                    # asks through needs_owner_yes(); the owner's reply is checked by owner_may_approve()
ON_PHONE = "on_phone"            # the owner taps Send on their own phone (phone-tools.v1); nothing goes out before
IN_VAULT = "in_vault"            # Windy Vault holds it until the owner approves there (vault-tools.v1, fresh sign-in)
OWNER_ASKED = "owner_asked"      # OWNER band only: it runs only on the owner's own request in their own turn
NOT_YET = "not_yet"              # reaches people with no ask today; an ask needs new words (Boss + a #9) first

OUTWARD: dict[str, str] = {
    "send_email": GATE,
    "send_sms": GATE,
    "confirm_sms": GATE,
    "phone_sms_compose": ON_PHONE,
    "vault.offer_save": IN_VAULT,
    "vault.request_access": IN_VAULT,
    "vault.call_api": IN_VAULT,
    "mind.switch_model": OWNER_ASKED,
    "windy_calendar.add_event": OWNER_ASKED,
    "windy_calendar.create_meeting": OWNER_ASKED,
    "ask_agent": OWNER_ASKED,
    "send_chat_message": NOT_YET,
    "message_agent": NOT_YET,
    "windy_calendar.book": NOT_YET,
    "windy_calendar.block": NOT_YET,
}

# Registered legacy tools that reach no one: reads, or the owner's own Windy Cloud space.
NOT_OUTWARD: frozenset[str] = frozenset({
    "my_mailbox", "list_inbox", "web_search", "fetch_url", "windy_plans",
    "list_cloud_files", "upload_to_cloud", "download_cloud_file",
})


def enabled() -> bool:
    """The gate is ON unless the env says exactly "0" (the lab switch)."""
    return os.environ.get(LAB_SWITCH, "1").strip() != "0"


def needs_owner_yes(recipients: Iterable[str], known: Iterable[str], *, bulk: bool = False,
                    switchable: bool = True) -> bool:
    """Ask the owner first? Yes unless it is ONE plain recipient the owner already approved.

    ``bulk``: a cc / bcc copy (always asks). ``switchable=False`` for a tool whose ask never had the lab switch
    (texting: Windy Text's first-contact rule), so the switch can't turn it off."""
    if switchable and not enabled():
        return False
    to = [str(r).strip().lower() for r in recipients if str(r).strip()]
    approved = {str(k).strip().lower() for k in known}
    return not (len(to) == 1 and not bulk and to[0] in approved)


def owner_may_approve(band: Any) -> bool:
    """Only the owner's own message approves a held outward action (never a stranger, never a sibling agent)."""
    from windyfly.agent.capabilities import Band

    try:
        return band is not None and int(band) >= int(Band.OWNER)
    except (TypeError, ValueError):
        return False
