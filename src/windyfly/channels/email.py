"""Email for Windy Fly.

Outbound mail goes ONLY through Windy Mail (``WindyMailAdapter``): the agent's own
mailbox, POST /api/v1/send, authenticated with its Eternitas passport token
(Hub, 2026-10-02). There is no SendGrid, Resend or Gmail sender.

``WindyFlyEmail`` is the legacy INBOUND handler (an Inbound Parse webhook relayed by the
local bridge): it turns an inbound email into an agent turn. It never sends.
"""

from __future__ import annotations

import logging
import os
import uuid
from typing import Any

from windyfly.agent.loop import agent_respond
from windyfly.channels.identity import resolve_band
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
        # Windy Mail authenticates an agent by its own Eternitas passport
        # token on /api/v1/send; a body brought home (`windy bring-home`)
        # has that and no separate JMAP token.
        self.jmap_token = (os.environ.get("WINDYMAIL_JMAP_TOKEN", "")
                           or os.environ.get("ETERNITAS_PASSPORT_TOKEN", ""))
        self.api_url = os.environ.get("WINDYMAIL_API_URL", "https://api.windymail.ai")
        self.db = db
        # Read budget. Production Mail sat behind a CPU-throttled host on
        # 2026-09-05 and /inbox took >30 s; a 30 s wait inside an LLM tool
        # call or a maintenance tick is too long, and the caller got an
        # empty list with no reason. Tunable; the last failure is kept so
        # tools can say "mail is slow" instead of "no mail".
        try:
            self.timeout_s = float(os.environ.get("WINDYMAIL_TIMEOUT_S", "15"))
        except ValueError:
            self.timeout_s = 15.0
        self.last_error: str = ""

        if not self.email or not self.jmap_token:
            raise RuntimeError(
                "WindyMailAdapter requires WINDYMAIL_EMAIL and WINDYMAIL_JMAP_TOKEN in .env"
            )

    def _send_bearer(self) -> str:
        """Bearer for POST /api/v1/send. Windy Mail authenticates a SEND by the agent's own
        Eternitas passport token, so the EPT is always used when present (Hub, 10-02; was
        WINDY_MAIL_SEND_EPT=1). The JMAP token is for inbox reads, and for a send only when
        there is no EPT: still Mail's /send, still the agent's own From."""
        ept = os.environ.get("ETERNITAS_PASSPORT_TOKEN", "").strip()
        return ept or self.jmap_token

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
                headers={"Authorization": f"Bearer {self._send_bearer()}"},
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
                timeout=self.timeout_s,
            )
            if resp.status_code == 200:
                self.last_error = ""
                return resp.json().get("messages", [])
            self.last_error = f"inbox HTTP {resp.status_code}"
            logger.warning("Windy Mail inbox fetch failed: %s", resp.status_code)
            return []
        except _httpx.TimeoutException:
            self.last_error = f"inbox timed out after {self.timeout_s:g}s"
            logger.warning("Windy Mail inbox error: %s", self.last_error)
            return []
        except Exception as e:
            self.last_error = f"inbox error: {e}"
            logger.error("Windy Mail inbox error: %s", e)
            return []


# ═══════════════════════════════════════════════════════════════════════
# Adapter factory — pick the best available email backend
# ═══════════════════════════════════════════════════════════════════════


def get_email_adapter() -> WindyMailAdapter | None:
    """Return the Windy Mail adapter, or None when the agent has no mailbox yet.

    Windy Mail is the only email backend (Hub, 2026-10-02): there is no fallback.
    """
    if os.environ.get("WINDYMAIL_EMAIL"):
        try:
            return WindyMailAdapter()
        except RuntimeError:
            logger.warning("WINDYMAIL_EMAIL set but the Windy Mail adapter failed to start")
    return None


# ═══════════════════════════════════════════════════════════════════════
# Inbound handler — legacy WindyFlyEmail (receives only; never sends)
# ═══════════════════════════════════════════════════════════════════════


class WindyFlyEmail:
    """Inbound email → an agent turn. Outbound mail goes through Windy Mail only."""

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
        """Handle an inbound email (Inbound Parse webhook via the local bridge).

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

        # [C1] Resolve the sender's trust band instead of defaulting to
        # Band.OWNER. An unknown email sender maps to SANDBOX (no owner toolset:
        # shell/fs/ssh/fleet/send-as-owner), same as the matrix/telegram
        # channels — an inbound email is NOT proof of the owner, and its body is
        # attacker-controllable (indirect prompt-injection surface).
        response = agent_respond(
            self.config, self.db, self.write_queue,
            full_message, session_id, self.tool_registry,
            band=resolve_band("email", from_email, config=self.config),
        )

        log_event(self.db, self.write_queue, "email.inbound", {
            "from": from_email,
            "subject": subject,
            "body_length": len(body),
        })

        return response
