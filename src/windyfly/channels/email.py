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
from windyfly.memory.nodes import upsert_node
from windyfly.memory.write_queue import WriteQueue
from windyfly.observability.events import log_event
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class RateLimitedError(Exception):
    """Raised when an outbound email is blocked by the rate limiter."""


# ═══════════════════════════════════════════════════════════════════════
# Windy Mail adapter — JMAP-backed inboxes via Windy Mail API
# ═══════════════════════════════════════════════════════════════════════


class WindyMailAdapter:
    """Send and receive email via the Windy Mail API (Stalwart JMAP)."""

    def __init__(self, db: Database | None = None) -> None:
        self.email = os.environ.get("WINDYMAIL_EMAIL", "")
        self.jmap_token = os.environ.get("WINDYMAIL_JMAP_TOKEN", "")
        self.api_url = os.environ.get("WINDYMAIL_API_URL", "https://api.windymail.ai")
        self.db = db

        if not self.email or not self.jmap_token:
            raise RuntimeError(
                "WindyMailAdapter requires WINDYMAIL_EMAIL and WINDYMAIL_JMAP_TOKEN in .env"
            )

    def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        """Send an email via Windy Mail API.

        POST /api/v1/send
        Auth: Authorization: Bearer <jmap_token>

        Args:
            to: Recipient email address.
            subject: Email subject.
            body: Plain text body.

        Returns:
            Dict with status and message_id on success.

        Raises:
            RateLimitedError: If the rate limiter blocks the send.
            TrustDenied: If the agent's integrity band doesn't allow send_email.
        """
        from windyfly.trust.gate import TrustDenied, require_trust_sync
        try:
            require_trust_sync("send_email", db=self.db)
        except TrustDenied as denied:
            logger.warning("Email send blocked by trust gate: %s", denied)
            return {"status": "denied", "error": str(denied)}

        # Rate limit check (only if db is available)
        if self.db is not None:
            try:
                from windyfly.mail_rate_limiter import MailRateLimiter

                limiter = MailRateLimiter(self.db)
                check = limiter.check_send_allowed(self.email, to, subject, body)
                if not check.allowed:
                    raise RateLimitedError(
                        f"Email to {to} blocked by rate limiter: {check.reason}"
                    )
            except RateLimitedError:
                raise
            except Exception as e:
                logger.warning("Rate limiter check failed (sending anyway): %s", e)

        import httpx as _httpx

        try:
            resp = _httpx.post(
                f"{self.api_url}/api/v1/send",
                json={
                    "to": [to],
                    "subject": subject,
                    "body_text": body,
                    "mode": "independent",
                },
                headers={"Authorization": f"Bearer {self.jmap_token}"},
                timeout=10.0,
            )
            if resp.status_code in (200, 201, 202):
                data = resp.json()
                logger.info("Windy Mail sent to %s — %s", to, subject)
                if self.db is not None:
                    try:
                        from windyfly.mail_rate_limiter import MailRateLimiter

                        MailRateLimiter(self.db).record_send(self.email, to, body)
                    except Exception as e:
                        logger.warning("Rate limiter record_send failed: %s", e)
                return {"status": "sent", "message_id": data.get("message_id")}
            else:
                logger.warning("Windy Mail send failed: %s %s", resp.status_code, resp.text)
                return {"status": "failed", "error": resp.text}
        except Exception as e:
            logger.error("Windy Mail send error: %s", e)
            return {"status": "failed", "error": str(e)}

    def check_inbox(self, unread_only: bool = True) -> list[dict[str, Any]]:
        """Fetch messages from the Windy Mail inbox.

        Args:
            unread_only: If True, return only unread messages.

        Returns:
            List of message dicts (from, subject, body, date, …).
        """
        import httpx as _httpx

        params: dict[str, Any] = {}
        if unread_only:
            params["unread"] = "true"

        try:
            resp = _httpx.get(
                f"{self.api_url}/api/v1/inbox",
                params=params,
                headers={"Authorization": f"Bearer {self.jmap_token}"},
                timeout=30.0,
            )
            if resp.status_code == 200:
                return resp.json().get("messages", [])
            else:
                logger.warning("Windy Mail inbox fetch failed: %s", resp.status_code)
                return []
        except Exception as e:
            logger.error("Windy Mail inbox error: %s", e)
            return []


# ═══════════════════════════════════════════════════════════════════════
# Adapter factory — pick the best available email backend
# ═══════════════════════════════════════════════════════════════════════


def get_email_adapter() -> WindyMailAdapter | None:
    """Return the best available email adapter, or None.

    Priority:
        1. Windy Mail (WINDYMAIL_EMAIL set)
        2. SendGrid   (SENDGRID_API_KEY set) — returns None here;
           callers use WindyFlyEmail directly for the SendGrid path.
    """
    if os.environ.get("WINDYMAIL_EMAIL"):
        try:
            return WindyMailAdapter()
        except RuntimeError:
            logger.warning("WINDYMAIL_EMAIL set but adapter init failed — falling back")
    # SendGrid path is handled by WindyFlyEmail class (legacy / HiFly)
    return None


# ═══════════════════════════════════════════════════════════════════════
# SendGrid adapter — original WindyFlyEmail (HiFly / legacy)
# ═══════════════════════════════════════════════════════════════════════


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
        html_body: str | None = None,
    ) -> dict[str, Any]:
        """Send outbound email via SendGrid.

        Args:
            to_email: Recipient email address.
            subject: Email subject.
            body: Plain text body.
            reply_to: Optional reply-to address.
            html_body: Optional HTML body (sent alongside plain text).

        Returns:
            Dict with status.

        Raises:
            RateLimitedError: If the rate limiter blocks the send.
            TrustDenied: If the agent's integrity band doesn't allow send_email.
        """
        from windyfly.trust.gate import TrustDenied, require_trust_sync
        try:
            require_trust_sync("send_email", db=self.db)
        except TrustDenied as denied:
            logger.warning("Email send blocked by trust gate: %s", denied)
            return {"status": "denied", "error": str(denied)}

        # Rate limit check
        try:
            from windyfly.mail_rate_limiter import MailRateLimiter

            limiter = MailRateLimiter(self.db)
            result = limiter.check_send_allowed(self.from_email, to_email, subject, body)
            if not result.allowed:
                raise RateLimitedError(
                    f"Email to {to_email} blocked by rate limiter: {result.reason}"
                )
        except RateLimitedError:
            raise
        except Exception as e:
            logger.warning("Rate limiter check failed (sending anyway): %s", e)

        import urllib.request

        url = "https://api.sendgrid.com/v3/mail/send"
        content = [{"type": "text/plain", "value": body}]
        if html_body:
            content.append({"type": "text/html", "value": html_body})
        payload = {
            "personalizations": [{"to": [{"email": to_email}]}],
            "from": {"email": self.from_email, "name": self.from_name},
            "subject": subject,
            "content": content,
        }
        if reply_to:
            payload["reply_to"] = {"email": reply_to}

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {self.api_key}")
        req.add_header("Content-Type", "application/json")

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                log_event(self.db, self.write_queue, "email.outbound", {
                    "to": to_email, "subject": subject, "status": status,
                })
                # Record successful send for rate tracking
                try:
                    from windyfly.mail_rate_limiter import MailRateLimiter

                    MailRateLimiter(self.db).record_send(self.from_email, to_email, body)
                except Exception as e:
                    logger.warning("Rate limiter record_send failed: %s", e)
                return {"status": "sent", "http_status": status}
        except Exception as e:
            logger.error("SendGrid email failed: %s", e)
            return {"status": "failed", "error": str(e)}
