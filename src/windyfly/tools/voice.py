"""Voice tool — let the LLM place a phone call via the windy-call service.

Master plan codon **D.3.2**. Mirror of `tools/sms.py` for the voice
channel. Wraps an authenticated HTTP POST to the windy-call service
(`api.windycall.com/voice/call`), which dispatches via Twilio + posts
an integrity event to Eternitas under the agent's passport.

Why route through windy-call instead of Twilio directly:
  - Per-EII rate limiting (your tier scales with reputation)
  - Per-passport monthly USD cost cap (voice calls are pricier than SMS;
    the tier-aware caps in windy-call reflect that)
  - Trust-gate enforcement (high-risk passports get throttled)
  - Audit log to Eternitas as integrity events (`voice_call_outbound`)
  - One Twilio number rotation point

How the call works on Twilio's side: windy-call assembles a TwiML
`<Response><Say voice="alice">{message}</Say></Response>` document
and uses Twilio's REST API to dial `to`. When the recipient picks up,
Twilio's TTS reads the message, then hangs up. Voice-script options
(custom recordings, `<Gather>`, multi-turn) are deferred to a future
codon — v1 is "agent says one thing, hangs up."

Use cases the LLM should reach for `make_call`:
  - "Tell my dentist I need to reschedule"
  - "Call the restaurant and let them know I'm running 10 minutes late"
  - "Leave a voicemail for Mom about dinner Sunday"
Use cases that should go to send_sms instead (cheaper + less intrusive):
  - Confirmations, status pings, anything multi-line / detailed

Returns the same {status: sent | unavailable | failed} shape every
other tool uses, so the LLM can interpret the result uniformly.

Environment:
    WINDY_CALL_BASE_URL   default `https://api.windycall.com`
    WINDY_PASSPORT_EPT    the agent's bot-passport EPT (JWT).
                          Same env var that windy-search + send_sms use.

A2P 10DLC compliance is SMS-only; voice calls don't need that
registration. Trial Twilio accounts still require verified destination
numbers (error 21219 instead of 21608 for the voice path).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Voice calls take longer to dial than SMS to send — give windy-call
# more headroom before the agent gives up on the round-trip.
_TIMEOUT = 20.0
_DEFAULT_BASE_URL = "https://api.windycall.com"
_E164_RE = re.compile(r"^\+[1-9]\d{6,18}$")
_VALID_VOICES = ("alice", "man", "woman", "Polly.Joanna", "Polly.Matthew")


def _windy_call_env() -> tuple[str, str]:
    """Return (base_url, ept). Either may be empty when unconfigured."""
    return (
        os.environ.get("WINDY_CALL_BASE_URL", _DEFAULT_BASE_URL).rstrip("/"),
        os.environ.get("WINDY_PASSPORT_EPT", ""),
    )


def make_call(to: str, message: str, voice: str = "alice") -> dict[str, Any]:
    """Place an outbound voice call to ``to`` and have ``message`` read aloud.

    Args:
        to: E.164-formatted destination number (e.g. ``+15551234567``).
        message: Text that Twilio TTS will speak when the recipient answers.
            windy-call enforces a max length consistent with TTS pacing.
        voice: TTS voice name. Default ``"alice"`` (Twilio's classic);
            falls back to alice if the value isn't in the allow-list.

    Returns:
        dict with ``status``: ``sent``, ``unavailable``, or ``failed``.
        On success: ``{status: sent, sid, to, from, integrity_event_posted}``.
        ``sid`` is Twilio's CallSid (CA...), useful for follow-up status
        polling. ``status='sent'`` here means the call was *initiated* —
        whether the recipient answered is a separate observation (poll
        the call_status webhook for that).
    """
    base_url, ept = _windy_call_env()
    if not ept:
        return {
            "status": "unavailable",
            "error": (
                "Voice calls are not configured for this agent. "
                "WINDY_PASSPORT_EPT must be set (usually populated by "
                "hatch provisioning + the agent's broker token mint). "
                "See master-plan D.3.2."
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

    safe_voice = voice if voice in _VALID_VOICES else "alice"

    try:
        resp = httpx.post(
            f"{base_url}/voice/call",
            headers={
                "Authorization": f"Bearer {ept}",
                "Content-Type": "application/json",
            },
            json={"to": to, "message": message, "voice": safe_voice},
            timeout=_TIMEOUT,
        )
    except httpx.ConnectError as exc:
        return {
            "status": "failed",
            "error": f"Cannot reach windy-call at {base_url}: {exc}",
        }
    except httpx.HTTPError as exc:
        return {"status": "failed", "error": f"windy-call transport error: {exc}"}

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

    # 4xx/5xx — surface windy-call's error verbatim so the LLM can
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


def register_voice_tools(registry: ToolRegistry) -> None:
    """Register ``make_call`` with the tool registry."""
    registry.register(
        name="make_call",
        description=(
            "Place a phone call from the agent to a phone number, with the "
            "agent's `message` read aloud by text-to-speech when the "
            "recipient answers. Use this when the user says 'call the "
            "dentist and reschedule' / 'call Mom and tell her I'm running "
            "late' / etc. The destination must be E.164 (`+1` + 10 digits "
            "for US). Voice calls are PRICIER than SMS — prefer send_sms "
            "for confirmations, status pings, and anything multi-line. "
            "Reach for make_call when the use case genuinely needs voice "
            "(reaching a person who doesn't text, leaving a voicemail, "
            "etc.). Returns {status: 'sent', sid, to, from, "
            "integrity_event_posted} on call-initiation success "
            "(`sid` is Twilio's CallSid; whether the recipient ANSWERED "
            "is a separate observation), {status: 'unavailable', error} "
            "if voice isn't configured for this agent (no passport EPT), "
            "or {status: 'failed', error, http_status?, error_code?} on "
            "validation / rate-limit / Twilio errors. error_code 21219 "
            "means Twilio's trial-account verified-destination check; "
            "ask the user to verify their number in the Twilio console "
            "or upgrade off the trial plan."
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
                "message": {
                    "type": "string",
                    "description": (
                        "What the agent wants spoken when the call is "
                        "answered. Read aloud by Twilio TTS. Keep it "
                        "concise — voice calls bill by the minute."
                    ),
                },
                "voice": {
                    "type": "string",
                    "description": (
                        "TTS voice name. Default 'alice' (Twilio's "
                        "classic). Falls back to alice if the value "
                        "isn't allow-listed."
                    ),
                    "enum": list(_VALID_VOICES),
                },
            },
            "required": ["to", "message"],
        },
        fn=make_call,
    )
