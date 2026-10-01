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

Consent (legal review, 2026-09-23; TCPA):
  - The FIRST text to a number needs the owner's yes: ``send_sms``
    returns ``confirm_required`` with a question the agent relays
    verbatim, and ``confirm_sms`` sends only after the yes, recording
    the number as approved. Later texts to that number go straight out.
  - Every text ends with "— <agent>, AI assistant for <owner>. Reply
    STOP to opt out." A sender that enforces STOP answers
    ``recipient_opted_out``; that is surfaced and never retried.
  - SMS stays OFF until such a sender exists (see ``_windy_text_env``).

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

import hashlib
import logging
import os
import re
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx

from windyfly.tools.registry import ToolRegistry

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

_TIMEOUT = 10.0
_DEFAULT_BASE_URL = "https://api.windytext.com"
_E164_RE = re.compile(r"^\+[1-9]\d{6,18}$")
_MAX_SMS_CHARS = 1600  # Twilio's hard cap for one message (10 segments)
_CONFIRM_TTL_S = 600
_UNAVAILABLE = (
    "Texting isn't available yet; I can reach them by email or you can "
    "message them in Windy Chat."
)

_APPROVED_SQL = """
CREATE TABLE IF NOT EXISTS sms_approved (
    number TEXT PRIMARY KEY,
    approved_at TEXT NOT NULL,
    approved_by TEXT NOT NULL DEFAULT 'owner'
);
"""

_db: Database | None = None
_approved_mem: set[str] = set()          # used only when no database is wired
_pending: dict[str, dict[str, Any]] = {}  # confirm_token -> {to, body, body_sha256, expires}


def byo_enabled() -> bool:
    """WINDY_TEXT_BYO=1 (OFF by default; enabled only on Windy Hub's word): texting goes
    through Windy Text on the OWNER's own Twilio account, authenticated with the agent's
    own EPT (it carries the owner's windy_identity_id). Spec: TEXT_BYO_TWILIO.md."""
    return os.environ.get("WINDY_TEXT_BYO", "") == "1"


def _windy_text_env() -> tuple[str, str]:
    """Return (base_url, ept). Either may be empty when unconfigured."""
    base = os.environ.get("WINDY_TEXT_BASE_URL", _DEFAULT_BASE_URL).rstrip("/")
    if byo_enabled():
        return base, os.environ.get("ETERNITAS_PASSPORT_TOKEN", "")
    return (
        base,
        # Do NOT switch to ETERNITAS_PASSPORT_TOKEN until a sender enforces
        # STOP (orchestrator 09-23). Reading only the legacy variable keeps
        # agent SMS off on real installs until then.
        os.environ.get("WINDY_PASSPORT_EPT", ""),
    )


def _is_approved(number: str) -> bool:
    if _db is None:
        return number in _approved_mem
    _db.conn.executescript(_APPROVED_SQL)
    return _db.fetchone("SELECT number FROM sms_approved WHERE number = ?", (number,)) is not None


def _approve(number: str, by: str = "owner") -> None:
    if _db is None:
        _approved_mem.add(number)
        return
    _db.conn.executescript(_APPROVED_SQL)
    _db.execute(
        "INSERT OR IGNORE INTO sms_approved (number, approved_at, approved_by) VALUES (?, ?, ?)",
        (number, datetime.now(timezone.utc).isoformat(), by),
    )
    _db.commit()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _with_footer(body: str) -> str:
    from windyfly.tools import outbound_identity

    footer = outbound_identity.sms_footer()
    if footer in body:
        return body
    return f"{body.rstrip()}\n{footer}"


# Windy Text refusals -> one plain sentence the agent can say to a person.
_PLAIN_ERRORS: dict[str, str] = {
    "carrier_registration_pending": (
        "I can receive texts, but the phone carriers haven't approved sending from this "
        "number yet. That usually takes a few days."),
    "recipient_not_consented": (
        "That person hasn't agreed to get texts from me yet, so I didn't send it."),
    "owner_opted_out": "You've turned off texts from me, so I didn't send it.",
    "recipient_opted_out": "That number has opted out of texts (they replied STOP), so I didn't send it.",
    "quiet_hours": "It's quiet hours for that person, so I didn't send it. I can try in the morning.",
    "spend_cap_reached": "Your texting spending limit has been reached, so I didn't send it.",
    "spend_ledger_unavailable": "Texting is briefly unavailable on Windy's side, so I didn't send it. Try again shortly.",
    "texting_not_set_up": "Texting isn't set up for your account yet, so I didn't send it.",
    "owner_phone_not_verified": "Your phone number isn't verified for texting yet, so I didn't send it.",
    "twilio_key_invalid": "Your Twilio key isn't working, so I didn't send it. Please reconnect Twilio.",
}


def plain_error(code: str | None) -> str | None:
    """Map a Windy Text error code (e.g. 'spend_cap_reached:daily') to a plain sentence."""
    if not code:
        return None
    return _PLAIN_ERRORS.get(code) or _PLAIN_ERRORS.get(code.split(":", 1)[0])


def send_sms(to: str | None = None, body: str = "") -> dict[str, Any]:
    """Send a text message to ``to``, or ask the owner first.

    Returns ``{status: confirm_required, question, confirm_token}`` the
    first time this agent texts ``to``; the agent must relay the
    question to its owner verbatim and call ``confirm_sms`` only after
    a yes. Otherwise ``sent`` / ``unavailable`` / ``failed`` /
    ``opted_out`` as before.
    """
    base_url, ept = _windy_text_env()
    if not ept:
        logger.debug("SMS unavailable: WINDY_PASSPORT_EPT unset (SMS stays off until a sender enforces STOP)")
        return {"status": "unavailable", "error": _UNAVAILABLE}

    if byo_enabled():
        return _send_byo(base_url, ept, to, body)

    if not to:
        return {"status": "failed", "error": "`to` is required."}

    if not _E164_RE.match(to):
        return {
            "status": "failed",
            "error": (
                f"`to` must be E.164 (start with +, country code, then "
                f"digits). Got {to!r}. Example: +15551234567."
            ),
        }

    if not _is_approved(to):
        token = secrets.token_urlsafe(16)
        _pending[token] = {
            "to": to,
            "body": body,
            "body_sha256": _sha256(body),
            "expires": time.time() + _CONFIRM_TTL_S,
        }
        return {
            "status": "confirm_required",
            "question": f"Text {to} for the first time? Reply yes to allow."
            + (" Or no." if byo_enabled() else ""),
            "confirm_token": token,
        }

    return _deliver(base_url, ept, to, body)


def confirm_sms(confirm_token: str) -> dict[str, Any]:
    """Send the pending first text AFTER the owner said yes, and approve the number."""
    # The token carries the exact text the owner approved, so the yes can't
    # be spent on a different message. Single use, even if it turns out stale.
    pending = _pending.pop(confirm_token, None)
    if pending is None:
        return {"status": "refused", "error": "Unknown or already-used confirmation. Ask again with send_sms."}
    if time.time() > pending["expires"]:
        return {"status": "refused", "error": "That confirmation expired (10 minutes). Ask again with send_sms."}
    base_url, ept = _windy_text_env()
    if not ept:
        return {"status": "unavailable", "error": _UNAVAILABLE}
    _approve(pending["to"])
    return _deliver(base_url, ept, pending["to"], pending["body"])


def owner_reply(text: str) -> str | None:
    """BYO mode: the OWNER's own 'yes' / 'no' to a pending first text, handled in code by
    channels.base so the model can never approve its own first contact. None = not ours."""
    if not byo_enabled() or not _pending:
        return None
    word = text.strip().strip(".!").lower()
    if word not in ("yes", "no"):
        return None
    token = max(_pending, key=lambda k: _pending[k]["expires"])
    if word == "no":
        _pending.pop(token, None)
        return "OK, I won't text that number."
    res = confirm_sms(token)
    if res.get("status") == "sent":
        return f"Texted {res.get('to')}, with your OK."
    return f"Not sent: {res.get('error') or res.get('status')}"


# ── BYO texting via Windy Text (WINDY_TEXT_BYO=1) ────────────────────
# Windy Text decides consent (owner-approved recipients, STOP) and adds the
# "[Name · Windy] " label to every text; the agent asks it, never keeps its own
# list, and adds no footer. The first text to a contact uses the wording Grant
# approved (10-01). A send whose 200 lacks prefix_applied=true is reported to the
# owner as sent WITHOUT the label.
_FIRST_TEXT = ("Hi, this is {agent}, an AI assistant texting for {owner}. {message} "
               "Reply STOP to opt out, HELP for help.")


def _names() -> tuple[str, str]:
    agent = (os.environ.get("WINDYFLY_AGENT_NAME") or "your Windy assistant").strip()
    owner_full = (os.environ.get("WINDY_OWNER_NAME") or "").strip()
    owner = owner_full.split()[0] if owner_full else "its owner"
    return agent, owner


def first_text(message: str) -> str:
    agent, owner = _names()
    msg = message.strip()
    if msg and msg[-1] not in ".!?":
        msg += "."
    return _FIRST_TEXT.format(agent=agent, owner=owner, message=msg)


TELEPHONY_AUD = "windy-telephony"
# Mode-B is unavailable (not refused) for these: fall back to the legacy EPT Bearer,
# which works until Windy Text sets TELEPHONY_REQUIRE_MODE_B (then its 401 is final).
_MODE_B_UNAVAILABLE = {"no_key", "unknown_audience", "unreachable"}


def _send_auth(url: str, legacy: dict[str, str]) -> tuple[dict[str, str], dict[str, Any] | None]:
    """Auth for the money route POST /sms/send: a ≤5-min EPT+agent token for
    aud windy-telephony bound to the agent's registered key, plus a DPoP proof for
    this exact URL. A revoked/suspended passport is refused (never a fallback)."""
    from windyfly.eternitas import agent_keys as ak

    try:
        tok = ak.request_agent_token(TELEPHONY_AUD)["token"]
        proof = ak.service_dpop("POST", url)
    except ak.AgentTokenError as e:
        if e.code in _MODE_B_UNAVAILABLE:
            return legacy, None
        return legacy, {"status": "failed", "sent": False,
                        "error": "My Eternitas standing doesn't allow sending texts right now, so I didn't send it."}
    except Exception:
        return legacy, None  # no key store yet, etc.
    return {**legacy, "Authorization": f"DPoP {tok}", "DPoP": proof}, None


def _request_approval(base_url: str, headers: dict[str, str], to: str) -> dict[str, Any]:
    """Ask Windy Text to put an approval request in the owner's Windy Inbox
    (POST /sms/recipient/request, windy-text c98c2e0). Only the owner's tap there
    approves; the agent never approves a recipient itself, and doesn't retry until
    GET /sms/recipient says approved."""
    try:
        r = httpx.post(f"{base_url}/sms/recipient/request", json={"to": to},
                       headers=headers, timeout=_TIMEOUT)
    except httpx.HTTPError:
        return {"status": "needs_owner_approval", "sent": False,
                "error": "I need your OK before texting this number, and I couldn't ask Windy just now."}
    detail = ""
    try:
        detail = str((r.json() or {}).get("detail") or "")
    except ValueError:
        pass
    if r.status_code == 200:
        return {"status": "approved_retry", "sent": False,
                "error": "That number was just approved. Ask me once more and I'll send it."}
    if r.status_code == 202:
        asked_inbox = False
        try:
            asked_inbox = (r.json() or {}).get("owner_asked") == "inbox"
        except ValueError:
            pass
        _agent, owner = _names()
        msg = (f"I've asked {owner} in the Windy Inbox before texting this number. I'll wait for the OK."
               if asked_inbox else
               "I asked for your OK in Windy before texting this number. I'll wait for it.")
        return {"status": "needs_owner_approval", "sent": False, "requested": True, "error": msg}
    if r.status_code == 403 and "opted_out" in detail:
        return {"status": "opted_out", "sent": False,
                "error": "That number has opted out of texts (they replied STOP). I won't text it."}
    if r.status_code == 429:
        return {"status": "needs_owner_approval", "sent": False,
                "error": "There are already a lot of texting requests waiting for your OK in Windy. Please review them first."}
    plain = plain_error(detail.split(":", 1)[0]) if detail else None
    return {"status": "failed", "sent": False, "error": plain or f"Windy Text answered {r.status_code}"}


def _send_byo(base_url: str, ept: str, to: str | None, body: str) -> dict[str, Any]:
    if to and not _E164_RE.match(to):
        return {"status": "failed",
                "error": f"`to` must be E.164 (start with +, country code, then digits). Got {to!r}."}
    headers = {"Authorization": f"Bearer {ept}", "Content-Type": "application/json"}
    try:
        r = httpx.get(f"{base_url}/sms/recipient", params={"to": to} if to else None,
                      headers=headers, timeout=_TIMEOUT)
        rec = r.json() if r.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        rec = None
    if not isinstance(rec, dict):
        # Fail closed: without the server's consent answer, don't text anyone.
        return {"status": "failed", "sent": False,
                "error": "I couldn't check whether I'm allowed to text that number, so I didn't send it."}
    if rec.get("opted_out"):
        return {"status": "opted_out", "sent": False,
                "error": "That number has opted out of texts (they replied STOP). I won't text it."}
    if not rec.get("approved"):
        if not to:
            return {"status": "failed", "sent": False,
                    "error": "Your phone isn't set up for texting yet, so I didn't send it."}
        return _request_approval(base_url, headers, to)
    text = first_text(body) if rec.get("first_contact") and rec.get("kind") == "contact" else body
    if len(text) > _MAX_SMS_CHARS:
        return {"status": "failed", "sent": False,
                "error": f"Text too long ({len(text)} characters; the limit is {_MAX_SMS_CHARS})."}
    send_url = f"{base_url}/sms/send"
    send_headers, refused = _send_auth(send_url, headers)
    if refused:
        return refused
    try:
        resp = httpx.post(send_url,
                          json={"body": text} if to is None else {"to": to, "body": text},
                          headers=send_headers, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        return {"status": "failed", "sent": False, "error": f"Couldn't reach Windy Text: {type(exc).__name__}"}
    if resp.status_code in (200, 201):
        try:
            data = resp.json()
        except ValueError:
            data = {}
        out = {"status": "sent", "sent": True, "to": "owner" if to is None else to,
               "first_contact": bool(data.get("first_contact")), "sid": data.get("sid", "")}
        if data.get("prefix_applied") is not True:
            out.update({"status": "sent_without_label",
                        "notice_to_user": ("That text went out WITHOUT the usual '[name · Windy]' "
                                           "label. Please tell Windy support.")})
        return out
    try:
        err = resp.json()
    except ValueError:
        err = {}
    raw_code = err.get("error_code") or err.get("error")
    plain = plain_error(raw_code if isinstance(raw_code, str) else None)
    return {"status": "failed", "sent": False, "http_status": resp.status_code,
            "error_code": raw_code,
            "error": plain or err.get("detail") or f"Windy Text answered {resp.status_code}"}


def _deliver(base_url: str, ept: str, to: str | None, body: str) -> dict[str, Any]:
    text = _with_footer(body)
    if len(text) > _MAX_SMS_CHARS:
        return {
            "status": "failed",
            "error": f"Text too long ({len(text)} characters with the sign-off; the limit is {_MAX_SMS_CHARS}).",
        }
    try:
        resp = httpx.post(
            f"{base_url}/sms/send",
            headers={
                "Authorization": f"Bearer {ept}",
                "Content-Type": "application/json",
            },
            # Never a `from`: Windy Text picks the owner's number (422 if sent).
            json={"body": text} if to is None else {"to": to, "body": text},
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
            "to": data.get("to", to or "owner"),
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
    code = err.get("error_code") or (err.get("detail") if isinstance(err.get("detail"), str) else None)
    if code == "recipient_opted_out" or err.get("error") == "recipient_opted_out":
        # They replied STOP. Final: never retry, never work around it.
        return {
            "status": "opted_out",
            "error": f"{to or 'That number'} has opted out of texts (they replied STOP). Don't text this number again.",
            "http_status": resp.status_code,
        }
    raw_code = err.get("error_code") or err.get("error")
    plain = plain_error(raw_code if isinstance(raw_code, str) else None)
    return {
        "status": "failed",
        "error": plain or err.get("detail", err.get("error", resp.text[:200])),
        "http_status": resp.status_code,
        "error_code": raw_code,
        "sent": False,
    }


def register_sms_tools(registry: ToolRegistry, db: Database | None = None) -> None:
    """Register ``send_sms`` and ``confirm_sms`` with the tool registry."""
    global _db
    _db = db
    registry.register(
        name="send_sms",
        description=(
            "Send a text message (SMS) from the agent to a phone number. "
            "The destination must be E.164 (`+1` + 10 digits for US, etc.). "
            "The FIRST text to any number returns {status: 'confirm_required', "
            "question, confirm_token}: RELAY THE QUESTION VERBATIM to your "
            "owner and call confirm_sms with the token ONLY after they say yes. "
            "Never confirm on your own. Later texts to an approved number send "
            "directly. Every text ends with a sign-off naming you and offering "
            "STOP. Other results: {status: 'sent', sid, ...}; {status: "
            "'unavailable'} when SMS isn't available for this agent; "
            "{status: 'opted_out'} when the person replied STOP (never text "
            "them again); {status: 'failed', error, http_status?, error_code?} "
            "on validation / rate-limit / carrier errors."
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
            # BYO: omit `to` to text the owner on their own verified phone.
            "required": ["body"] if byo_enabled() else ["to", "body"],
        },
        fn=send_sms,
    )
    if byo_enabled():
        return  # the owner approves first contact by replying yes (owner_reply), not the model
    registry.register(
        name="confirm_sms",
        description=(
            "Send a first text AFTER the owner said yes to the question "
            "send_sms returned. Pass that confirm_token. The token works once "
            "and expires after 10 minutes."
        ),
        parameters={
            "type": "object",
            "properties": {
                "confirm_token": {
                    "type": "string",
                    "description": "The confirm_token from send_sms.",
                },
            },
            "required": ["confirm_token"],
        },
        fn=confirm_sms,
    )
