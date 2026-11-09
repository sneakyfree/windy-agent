"""SMS channel for Windy Fly via Twilio.

Receives inbound SMS via webhook, routes through agent_respond,
sends response back via Twilio REST API.

Requires:
  - TWILIO_ACCOUNT_SID env var
  - TWILIO_AUTH_TOKEN env var
  - TWILIO_PHONE_NUMBER env var (the agent's phone number, e.g. +15551234567)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import uuid
from typing import Any

from windyfly.agent.loop import agent_respond
from windyfly.channels.identity import resolve_band
from windyfly.memory.database import Database
from windyfly.memory.nodes import get_nodes_by_type, upsert_node
from windyfly.memory.write_queue import WriteQueue
from windyfly.observability.events import log_event
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Rate limit: max outbound SMS per day per agent
MAX_OUTBOUND_SMS_PER_DAY = 50

# Opt-out keywords (Windy Legal, LEGAL DECISION 28, BOARD 2026-11-09T11:14:19Z): the carrier-standard set,
# matched with case and spaces ignored, so "opt out", "Opt Out" and " STOP " all count. The whole message must be
# the keyword: "please stop texting me so late" goes to the agent as an ordinary message.
_OPT_OUT_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"})


# START / UNSTOP undo an opt-out. "YES" is NOT one of them (LEGAL DECISION 28 (4)(c)): a "yes" to anything must
# never re-subscribe a person who said STOP.
_OPT_IN_KEYWORDS = frozenset({"START", "UNSTOP"})

# The opt-out record. A person who texted STOP to this number is never texted again from this channel until they
# text START: send_sms refuses, and their later messages get no reply (LEGAL DECISION 28 (4)(a)).
_OPTOUT_SQL = """
CREATE TABLE IF NOT EXISTS sms_optouts (
    number TEXT PRIMARY KEY,
    opted_out_at TEXT NOT NULL
);
"""


def _is_opt_out(body: str) -> bool:
    return "".join((body or "").split()).upper() in _OPT_OUT_KEYWORDS


def _is_opt_in(body: str) -> bool:
    return "".join((body or "").split()).upper() in _OPT_IN_KEYWORDS


def _number_key(number: str) -> str:
    """One key per phone number, whatever its spelling: digits only, and a 10-digit North American number gets its
    country code, so "+1 (555) 123-4567", "15551234567" and "5551234567" are the same person."""
    digits = "".join(ch for ch in (number or "") if ch.isdigit())
    return "1" + digits if len(digits) == 10 else digits


def non_owner_allowed() -> bool:
    """WINDY_SMS_ALLOW_NON_OWNER=1 lets this channel text numbers other than the owner's own. Default OFF (Boss's
    opt-out order, BOARD 2026-11-09T11:18:02Z (1)(b)): with it off, the only person this channel ever texts is the
    owner."""
    return os.environ.get("WINDY_SMS_ALLOW_NON_OWNER", "").strip() == "1"


def owner_numbers(config: dict[str, Any] | None = None) -> set[str]:
    """The owner's own SMS numbers, from EXPLICIT settings only: WINDY_OWNER_IDS and config [trust] owner_ids
    ("sms:+1555..."). A first-contact binding never counts here: a stranger who texted first is not the owner."""
    from windyfly.channels.identity import _parse_pairs

    nums = set(_parse_pairs(os.environ.get("WINDY_OWNER_IDS", "")).get("sms", set()))
    for pair in ((config or {}).get("trust", {}) or {}).get("owner_ids", []) or []:
        nums |= _parse_pairs(str(pair)).get("sms", set())
    return {_number_key(n) for n in nums if _number_key(n)}


def may_text(number: str, config: dict[str, Any] | None = None) -> bool:
    """Owner-only unless the allow switch is on."""
    return non_owner_allowed() or _number_key(number) in owner_numbers(config)


def number_opted_out(number: str, db_path: str | None = None) -> bool:
    """For a send path that has no channel object (the /sms command): has this number opted out? Reads the same
    sms_optouts table. No database or no table yet = nothing recorded = False. Any other read error = True: when in
    doubt, do not text."""
    import sqlite3

    from windyfly.memory.db_location import resolve_db_path

    path = db_path or resolve_db_path()
    if not os.path.isfile(path):
        return False
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
        try:
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sms_optouts'").fetchone() is None:
                return False
            return con.execute("SELECT 1 FROM sms_optouts WHERE number = ?", (_number_key(number),)).fetchone() is not None
        finally:
            con.close()
    except sqlite3.Error:
        return True


class WindyFlySMS:
    """Windy Fly SMS channel via Twilio."""

    def __init__(
        self,
        config: dict[str, Any],
        db: Database,
        write_queue: WriteQueue,
        tool_registry: ToolRegistry | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.write_queue = write_queue
        self.tool_registry = tool_registry

        from windyfly.sms_park import sms_enabled

        if not sms_enabled():
            raise RuntimeError("SMS is parked until after launch (set WINDY_ENABLE_SMS=1 to enable)")

        self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.phone_number = os.environ.get("TWILIO_PHONE_NUMBER", "")

        if not all([self.account_sid, self.auth_token, self.phone_number]):
            raise RuntimeError(
                "Missing Twilio credentials. Set TWILIO_ACCOUNT_SID, "
                "TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in .env"
            )

        self.db.execute(_OPTOUT_SQL)
        self.db.commit()

        # Map phone number → session_id
        self._phone_sessions: dict[str, str] = {}

        # Track outbound SMS count today
        self._outbound_today: int = 0
        self._outbound_date: str = ""

    def _get_session_id(self, phone: str) -> str:
        """Get or create a session ID for a phone number."""
        if phone not in self._phone_sessions:
            self._phone_sessions[phone] = str(uuid.uuid4())
        return self._phone_sessions[phone]

    def _check_rate_limit(self) -> bool:
        """Check if we're under the daily outbound SMS limit."""
        import datetime
        today = datetime.date.today().isoformat()
        if today != self._outbound_date:
            self._outbound_date = today
            self._outbound_today = 0
        return self._outbound_today < MAX_OUTBOUND_SMS_PER_DAY

    def record_opt_out(self, number: str) -> None:
        import datetime
        self.db.execute(
            "INSERT OR REPLACE INTO sms_optouts (number, opted_out_at) VALUES (?, ?)",
            (_number_key(number), datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )
        self.db.commit()

    def clear_opt_out(self, number: str) -> None:
        self.db.execute("DELETE FROM sms_optouts WHERE number = ?", (_number_key(number),))
        self.db.commit()

    def is_opted_out(self, number: str) -> bool:
        return self.db.fetchone("SELECT 1 AS x FROM sms_optouts WHERE number = ?", (_number_key(number),)) is not None

    def handle_inbound(self, from_number: str, body: str) -> str:
        """Handle an inbound SMS message.

        Args:
            from_number: Sender's phone number (e.g. +15551234567).
            body: SMS message body.

        Returns:
            Agent's response text.
        """
        # Handle STOP/opt-out: record it FIRST, then confirm (the one text a person gets after STOP).
        if _is_opt_out(body):
            self.record_opt_out(from_number)
            log_event(self.db, self.write_queue, "sms.optout", {"phone": from_number})
            return "You've been unsubscribed. Reply START to re-subscribe."

        # START/UNSTOP: clear the record. No reply of our own (no new sentence; Twilio may send its own).
        if _is_opt_in(body):
            self.clear_opt_out(from_number)
            log_event(self.db, self.write_queue, "sms.optin", {"phone": from_number})
            return ""

        # Still opted out: no reply at all, and the agent is not asked to answer.
        if self.is_opted_out(from_number):
            log_event(self.db, self.write_queue, "sms.inbound_opted_out", {"phone": from_number})
            return ""

        # Owner only unless the allow switch is on: a reply is a text too, so a non-owner gets none and the agent is
        # not asked (Boss's opt-out order (1)(b)). The STOP confirmation above still goes to anyone.
        if not may_text(from_number, self.config):
            log_event(self.db, self.write_queue, "sms.inbound_not_owner", {"phone": from_number})
            return ""

        session_id = self._get_session_id(from_number)

        # Auto-save contact as a node
        upsert_node(
            self.db,
            "contact",
            f"contact:{from_number}",
            metadata={"phone": from_number, "source": "sms_inbound"},
            source="sms_channel",
            epistemic_status="verified",
        )

        # [C1] Resolve the sender's trust band instead of defaulting to
        # Band.OWNER. An unknown SMS sender maps to SANDBOX (no owner toolset:
        # shell/fs/ssh/fleet/send-as-owner), same as the matrix/telegram
        # channels already do — an inbound SMS is NOT proof of the owner.
        response = agent_respond(
            self.config, self.db, self.write_queue,
            body, session_id, self.tool_registry,
            band=resolve_band("sms", from_number, config=self.config),
        )

        log_event(self.db, self.write_queue, "sms.inbound", {
            "from": from_number,
            "body_length": len(body),
            "response_length": len(response),
        })

        return response

    def send_sms(self, to_number: str, message: str) -> dict[str, Any]:
        """Send an outbound SMS via Twilio REST API.

        Args:
            to_number: Recipient phone number.
            message: Message text (max 1600 chars for SMS).

        Returns:
            Dict with status and message_sid.

        Raises:
            RuntimeError: If rate limit exceeded.
        """
        if self.is_opted_out(to_number):
            # Never text a person who sent STOP (LEGAL DECISION 28). Same code as Windy Text's refusal.
            log_event(self.db, self.write_queue, "sms.outbound_refused", {"to": to_number, "code": "recipient_opted_out"})
            return {"status": "refused", "error": "recipient_opted_out"}

        if not may_text(to_number, self.config):
            # Boss's opt-out order (1)(b): owner only unless WINDY_SMS_ALLOW_NON_OWNER=1.
            log_event(self.db, self.write_queue, "sms.outbound_refused", {"to": to_number, "code": "not_owner"})
            return {"status": "refused", "error": "not_owner"}

        if not self._check_rate_limit():
            raise RuntimeError(
                f"Daily SMS limit reached ({MAX_OUTBOUND_SMS_PER_DAY}). "
                "Try again tomorrow or adjust MAX_OUTBOUND_SMS_PER_DAY."
            )

        import urllib.request
        import urllib.parse

        # Truncate to SMS limit with warning
        if len(message) > 1600:
            logger.warning("SMS message truncated from %d to 1600 chars", len(message))
            message = message[:1597] + "..."

        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json"
        data = urllib.parse.urlencode({
            "To": to_number,
            "From": self.phone_number,
            "Body": message,
        }).encode("utf-8")

        # Basic auth
        import base64
        credentials = base64.b64encode(
            f"{self.account_sid}:{self.auth_token}".encode()
        ).decode()

        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Basic {credentials}")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                import json
                result = json.loads(resp.read().decode())
                self._outbound_today += 1
                log_event(self.db, self.write_queue, "sms.outbound", {
                    "to": to_number,
                    "sid": result.get("sid", ""),
                })
                return {"status": "sent", "message_sid": result.get("sid", "")}
        except Exception as e:
            logger.error("Twilio SMS send failed: %s", e)
            return {"status": "failed", "error": str(e)}

    def lookup_contact(self, name: str) -> str | None:
        """Look up a phone number by contact name.

        Args:
            name: Contact name to search for.

        Returns:
            Phone number string or None.
        """
        contacts = get_nodes_by_type(self.db, "contact", limit=100)
        for c in contacts:
            node_name = c.get("name", "")
            metadata = c.get("metadata", "{}")
            if isinstance(metadata, str):
                import json
                try:
                    metadata = json.loads(metadata)
                except (json.JSONDecodeError, TypeError):
                    metadata = {}
            contact_name = metadata.get("display_name", "")
            if name.lower() in node_name.lower() or name.lower() in contact_name.lower():
                return metadata.get("phone")
        return None


def create_webhook_handler(sms: WindyFlySMS):
    """Create an async webhook handler for Twilio inbound SMS.

    This returns an async function suitable for use as an HTTP route handler.
    The webhook receives POST data with 'From' and 'Body' fields.

    Args:
        sms: WindyFlySMS instance.

    Returns:
        Async handler function.
    """
    async def handle_twilio_webhook(request_body: dict) -> str:
        from_number = request_body.get("From", "")
        body = request_body.get("Body", "")
        if not from_number or not body:
            return "<Response><Message>Invalid request</Message></Response>"
        response = sms.handle_inbound(from_number, body)
        if not response:
            return "<Response></Response>"  # nothing is sent (START, or a person who opted out)
        # Return TwiML response
        return f"<Response><Message>{response}</Message></Response>"
    return handle_twilio_webhook


def verify_twilio_signature(
    auth_token: str,
    url: str,
    params: dict[str, str],
    signature: str,
) -> bool:
    """Verify a Twilio webhook signature to prevent spoofing.

    Twilio signs every webhook request. This function recomputes the
    expected signature and compares it to the provided one.

    Args:
        auth_token: Twilio auth token (the signing secret).
        url: The full webhook URL that Twilio called.
        params: POST parameters as a dict.
        signature: The X-Twilio-Signature header value.

    Returns:
        True if the signature is valid.
    """
    import base64

    # Build the validation string: URL + sorted POST params
    data = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    computed = hmac.new(
        auth_token.encode(),
        data.encode(),
        hashlib.sha1,
    ).digest()
    expected = base64.b64encode(computed).decode()
    return hmac.compare_digest(expected, signature)
