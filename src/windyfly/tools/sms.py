"""SMS tool — let the LLM send a text message via the windy-text service.

Master plan codon **D.3.1**. Mirror of `tools/chat.py` for the SMS
channel. Wraps an authenticated HTTP POST to the windy-text service
(`api.windytext.com/sms/send`), which then dispatches via Twilio +
posts an integrity event to Eternitas under the agent's passport.

Why route through windy-text instead of Twilio directly:
  - Per-EII rate limiting (your tier scales with reputation)
  - Per-passport monthly USD cost cap (prevents runaway spend)
  - Trust-gate enforcement (high-risk passports get throttled)
  - Audit log to Eternitas as integrity events (`sms_sent`)
  - One Twilio number rotation = single point of change

Returns the same {status: sent | unavailable | failed} shape every
other tool uses, so the LLM can interpret the result uniformly.

Environment:
    WINDY_TEXT_BASE_URL   default `https://api.windytext.com`
    WINDY_PASSPORT_EPT    the agent's bot-passport EPT (JWT).
                          Same env var that windy-search uses (B.12).

E.164 enforcement: the destination MUST be E.164 (`+countrycode +
digits`). Pre-validating here keeps the round-trip cheap when the
LLM hallucinates "5551234567" instead of "+15551234567" — windy-text
422s on bad format anyway, but a local check produces a friendlier
LLM-facing error.

Trial-account note (for v0): Twilio trial accounts only deliver SMS
to *verified* destination numbers and prepend the body with "Sent
from your Twilio trial account." Once the trial is upgraded + A2P
10DLC compliance is approved, those constraints lift. The tool
surfaces Twilio's error_code (e.g. 21608 "unverified destination")
verbatim so the LLM can explain the constraint to the user.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_DEFAULT_BASE_URL = "https://api.windytext.com"
_E164_RE = re.compile(r"^\+[1-9]\d{6,18}$")


def _windy_text_env() -> tuple[str, str]:
    """Return (base_url, ept). Either may be empty when unconfigured."""
    return (
        os.environ.get("WINDY_TEXT_BASE_URL", _DEFAULT_BASE_URL).rstrip("/"),
        os.environ.get("WINDY_PASSPORT_EPT", ""),
    )


def send_sms(to: str, body: str) -> dict[str, Any]:
    """Send a text message to ``to``.

    Args:
        to: E.164-formatted destination number (e.g. ``+15551234567``).
        body: SMS body text. windy-text enforces a max length consistent
            with Twilio's segmentation rules.

    Returns:
        dict with ``status``: ``sent``, ``unavailable``, or ``failed``.
        On success: ``{status: sent, sid, to, from, integrity_event_posted}``.
        On unconfigured: ``{status: unavailable, error}``.
        On failure: ``{status: failed, error, http_status?, error_code?}``.
    """
    base_url, ept = _windy_text_env()
    if not ept:
        return {
            "status": "unavailable",
            "error": (
                "SMS is not configured for this agent. WINDY_PASSPORT_EPT "
                "must be set (usually populated by hatch provisioning + the "
                "agent's broker token mint). See master-plan D.3.1."
            ),
        }

    if not _E164_RE.match(to):
        return {
            "status": "failed",
            "error": (
                f"`to` must be E.164 (start with +, country code, then "
                f"digits). Got {to!r}. Example: +15551234567."
            ),
        }

    try:
        resp = httpx.post(
            f"{base_url}/sms/send",
            headers={
                "Authorization": f"Bearer {ept}",
                "Content-Type": "application/json",
            },
            json={"to": to, "body": body},
            timeout=_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        return {
            "status": "failed",
            "error": f"Cannot reach windy-text at {base_url}: {exc}",
        }
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"windy-text transport error: {exc}"}

    if resp.status_code in (200, 201):
        try:
            data = resp.json()
        except ValueError:
            data = {}
        return {
            "status": "sent",
            "sid": data.get("sid", ""),
            "to": data.get("to", to),
            "from": data.get("from", ""),
            "integrity_event_posted": data.get("integrity_event_posted", False),
        }

    # 4xx/5xx — surface windy-text's error verbatim so the LLM can
    # explain the constraint (rate-limit hit, trial-account block,
    # cost-cap exceeded, etc.).
    try:
        err = resp.json()
    except ValueError:
        return {
            "status": "failed",
            "error": f"HTTP {resp.status_code}: {resp.text[:200]}",
            "http_status": resp.status_code,
        }
    return {
        "status": "failed",
        "error": err.get("detail", err.get("error", resp.text[:200])),
        "http_status": resp.status_code,
        "error_code": err.get("error_code"),
    }


def register_sms_tools(registry: ToolRegistry) -> None:
    """Register ``send_sms`` with the tool registry."""
    registry.register(
        name="send_sms",
        description=(
            "Send a text message (SMS) from the agent to a phone number. "
            "Use this when the user says 'text Mom' / 'send a text to "
            "+1...' / etc. The destination must be E.164 (`+1` + 10 "
            "digits for US, etc.). Returns {status: 'sent', sid, to, "
            "from, integrity_event_posted} on success, "
            "{status: 'unavailable', error} if SMS isn't configured for "
            "this agent (no passport EPT in env), or {status: 'failed', "
            "error, http_status?, error_code?} on validation / rate-limit "
            "/ Twilio errors. The agent's per-tier monthly cost cap and "
            "EII rate limit are enforced by windy-text — failures with "
            "error_code 21608 mean Twilio's trial-account verified-"
            "destination check; ask the user to verify their number in "
            "the Twilio console or upgrade off the trial plan."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "E.164 destination number, e.g. '+15551234567'. "
                        "MUST start with + and a country code."
                    ),
                },
                "body": {
                    "type": "string",
                    "description": "The text-message body to send.",
                },
            },
            "required": ["to", "body"],
        },
        fn=send_sms,
    )
