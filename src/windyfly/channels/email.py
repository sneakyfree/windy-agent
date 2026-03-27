"""Email channel for Windy Fly via SendGrid.

Each agent gets agentname@windyfly.ai.
Receives inbound email via SendGrid Inbound Parse webhook,
sends outbound email via SendGrid Mail Send API.

Requires:
  - SENDGRID_API_KEY env var
  - WINDYFLY_EMAIL_ADDRESS env var (e.g. grant@windyfly.ai)
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.nodes import get_nodes_by_type, upsert_node
from windyfly.memory.write_queue import WriteQueue
from windyfly.observability.events import log_event
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class WindyFlyEmail:
    """Windy Fly email channel via SendGrid."""

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

        self.api_key = os.environ.get("SENDGRID_API_KEY", "")
        self.from_email = os.environ.get("WINDYFLY_EMAIL_ADDRESS", "fly@windyfly.ai")
        self.from_name = config.get("email", {}).get("from_name", "Windy Fly")

        if not self.api_key:
            raise RuntimeError("Set SENDGRID_API_KEY in .env")

        # Map email address → session_id
        self._email_sessions: dict[str, str] = {}

    def _get_session_id(self, email: str) -> str:
        """Get or create session ID for an email address."""
        if email not in self._email_sessions:
            self._email_sessions[email] = str(uuid.uuid4())
        return self._email_sessions[email]

    def handle_inbound(
        self,
        from_email: str,
        subject: str,
        body: str,
    ) -> str:
        """Handle inbound email via SendGrid Inbound Parse.

        Args:
            from_email: Sender's email address.
            subject: Email subject line.
            body: Plain text email body.

        Returns:
            Agent's response text.
        """
        session_id = self._get_session_id(from_email)

        # Auto-save contact
        upsert_node(
            self.db,
            "contact",
            f"contact:{from_email}",
            metadata={"email": from_email, "source": "email_inbound"},
            source="email_channel",
            epistemic_status="verified",
        )

        # Combine subject + body for context
        full_message = f"[Email from {from_email}] Subject: {subject}\n\n{body}"

        response = agent_respond(
            self.config, self.db, self.write_queue,
            full_message, session_id, self.tool_registry,
        )

        log_event(self.db, self.write_queue, "email.inbound", {
            "from": from_email,
            "subject": subject,
            "body_length": len(body),
        })

        return response

    def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        """Send outbound email via SendGrid.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            body: Plain text body.
            reply_to: Optional reply-to address.

        Returns:
            Dict with status.
        """
        import urllib.request

        url = "https://api.sendgrid.com/v3/mail/send"
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self.from_email, "name": self.from_name},
            "subject": subject,
            "content": [{"type": "text/plain", "value": body}],
        }
        if reply_to:
            payload["reply_to"] = {"email": reply_to}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
                log_event(self.db, self.write_queue, "email.outbound", {
                    "to": to_email, "subject": subject, "status": status,
                })
                return {"status": "sent", "http_status": status}
        except Exception as e:
            logger.error("SendGrid email failed: %s", e)
            return {"status": "failed", "error": str(e)}
