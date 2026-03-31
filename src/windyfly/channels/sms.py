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
import time
import uuid
from typing import Any

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.nodes import get_nodes_by_type, upsert_node
from windyfly.memory.write_queue import WriteQueue
from windyfly.observability.events import log_event
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Rate limit: max outbound SMS per day per agent
MAX_OUTBOUND_SMS_PER_DAY = 50


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

        self.account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
        self.auth_token = os.environ.get("TWILIO_AUTH_TOKEN", "")
        self.phone_number = os.environ.get("TWILIO_PHONE_NUMBER", "")

        if not all([self.account_sid, self.auth_token, self.phone_number]):
            raise RuntimeError(
                "Missing Twilio credentials. Set TWILIO_ACCOUNT_SID, "
                "TWILIO_AUTH_TOKEN, and TWILIO_PHONE_NUMBER in .env"
            )

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

    def handle_inbound(self, from_number: str, body: str) -> str:
        """Handle an inbound SMS message.

        Args:
            from_number: Sender's phone number (e.g. +15551234567).
            body: SMS message body.

        Returns:
            Agent's response text.
        """
        # Handle STOP/opt-out
        if body.strip().upper() in ("STOP", "UNSUBSCRIBE", "CANCEL"):
            log_event(self.db, self.write_queue, "sms.optout", {"phone": from_number})
            return "You've been unsubscribed. Reply START to re-subscribe."

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

        response = agent_respond(
            self.config, self.db, self.write_queue,
            body, session_id, self.tool_registry,
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
