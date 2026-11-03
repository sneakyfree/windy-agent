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
import time
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

# Mode B (EPT+agent aud windy-mail + a DPoP proof on EVERY request, reads included), DARK until its own GO.
# Mail's accept side is windy-mail #249 (WINDYMAIL_EPT_AGENT_ENABLED on Mail's side). The old credential stays
# the fallback until windy-agent stops the 365-day EPT (2026-12-15).
ENV_FLAG = "WINDY_MAIL_EPT_AGENT"
MAIL_AUD = "windy-mail"


def mode_b_enabled() -> bool:
    return os.environ.get(ENV_FLAG, "") == "1"


def _agent_headers(method: str, url: str) -> dict[str, str] | None:
    """EPT+agent + DPoP for this request, or None when the mint failed (the caller uses the old credential)."""
    from windyfly.agent import service_auth

    try:
        return service_auth.agent_headers(MAIL_AUD, method, url)
    except service_auth.ServiceAuthError as exc:
        logger.warning("Windy Mail: no EPT+agent (%s %s); using the old credential", exc.code, exc.detail)
        return None


def _authed(method: str, url: str, bearer: str, do: Any) -> Any:
    """``do(headers)`` with EPT+agent + DPoP when the switch is on, else (or when the mint fails) with
    ``Bearer <bearer>``. None when there is nothing to authenticate with. Mail answers ANY credential it
    can't verify with 401 from its auth dependency, before the route runs (windy-mail send.get_sender_identity),
    so nothing was sent or read: one retry with the old credential is safe."""
    agent = _agent_headers(method, url) if mode_b_enabled() else None
    if agent is None:
        return do({"Authorization": f"Bearer {bearer}"}) if bearer else None
    resp = do(agent)
    if resp.status_code == 401 and bearer:
        logger.info("[mail-auth] Mail answered 401 to the EPT+agent; retried with the old credential")
        resp = do({"Authorization": f"Bearer {bearer}"})
    return resp


# ═══════════════════════════════════════════════════════════════════════
# Windy Mail adapter — JMAP-backed inboxes via Windy Mail API
# ═══════════════════════════════════════════════════════════════════════


def _mail_error_sentence(resp: Any) -> str:
    """Mail's own sentence for a refused send, never raw JSON. Mail wraps most refusals as
    {"detail": <str>} or, for every 429, {"detail": {"message", "error", ...}}; the send-from
    refusals are top-level {"error", "message"} (Windy Mail 10-12). 4xx are final (do not retry);
    a 5xx may or may not have sent: check Sent first."""
    try:
        d = resp.json()
    except Exception:  # noqa: BLE001
        d = None
    for part in ((d.get("detail"), d) if isinstance(d, dict) else ()):
        if isinstance(part, str) and part.strip():
            return part.strip()[:300]
        if isinstance(part, dict):
            for key in ("message", "error"):
                said = part.get(key)
                if isinstance(said, str) and said.strip():
                    return said.strip()[:300]
    if resp.status_code >= 500:
        return _UNSURE_SENTENCE
    return "The mail server refused it."


_UNSURE_SENTENCE = "The mail server had trouble, so I can't be sure it went. Check the Sent folder before sending again."


def _mail_code(resp: Any) -> str:
    """Mail's machine code for a refusal ({"error": code, "message": ...}, Windy Mail #275), or ""."""
    try:
        d = resp.json()
    except Exception:  # noqa: BLE001
        return ""
    code = d.get("error") if isinstance(d, dict) else None
    return code if isinstance(code, str) else ""


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

        if not self.email or not (self.jmap_token or mode_b_enabled()):
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

    def send_email(self, to: "str | list[str]", subject: str, body: str, *,
                   cc: "list[str] | None" = None, bcc: "list[str] | None" = None) -> dict[str, Any]:
        """Send ONE email (one message, everybody on it) via Windy Mail API.

        POST /api/v1/send
        Auth: Authorization: Bearer <the agent's EPT> (or, with WINDY_MAIL_EPT_AGENT=1, its EPT+agent + DPoP)

        Args:
            to: Recipient email address, or a list of them (one message with all of them).
            cc: Optional carbon-copy addresses (visible to the others).
            bcc: Optional blind-copy addresses (hidden from the others).
            subject: Email subject.
            body: Plain text body.

        Returns:
            Dict with status and message_id on success.

        Sending limits are Windy Mail's: its refusal (429 "Daily send limit reached" and the
        like) comes back in Mail's own words. A trust-gate denial returns status "denied".
        """
        from windyfly.trust.gate import TrustDenied, require_trust_sync
        try:
            require_trust_sync("send_email", db=self.db)
        except TrustDenied as denied:
            logger.warning("Email send blocked by trust gate: %s", denied)
            return {"status": "denied", "error": str(denied)}

        to_list = [to] if isinstance(to, str) else list(to)
        everyone = to_list + list(cc or []) + list(bcc or [])

        import httpx as _httpx

        payload: dict[str, Any] = {
            "to": to_list,
            "subject": subject,
            "body_text": body,
            "mode": "independent",
        }
        if cc:
            payload["cc"] = list(cc)
        if bcc:
            payload["bcc"] = list(bcc)
        url = f"{self.api_url}/api/v1/send"
        # ONE key per intended send (agent.action_key; Windy Mail #275): a repeat of the same send gets the first
        # result back and is never sent again. The same key rides the legacy-credential retry inside _authed.
        from windyfly.agent import action_key as _ak

        # The key is the route + the recipients + an ordinal, never the subject (Boss 09:10Z): a retry with a reworded
        # subject is the SAME send. The ordinal moves on only after a definitive answer (_ak.done below).
        recips = ",".join(sorted(str(x) for x in everyone))
        idem = _ak.headers("mail.send", recips)
        try:
            def post() -> Any:
                return _authed("POST", url, self._send_bearer(),
                               lambda headers: _httpx.post(url, json=payload, headers={**headers, **idem},
                                                           timeout=10.0))

            resp = post()
            if resp is not None and resp.status_code == 409 and _mail_code(resp) == "idempotency_in_progress":
                time.sleep(2.0)  # the first try of this same send is still running: ask once more, same key
                resp = post()
            if resp is None or _ak.definitive(resp.status_code):
                _ak.done("mail.send", recips)  # answered for sure (sent, or refused); the next send is a new one
            if resp is None:
                return {"status": "failed", "error": "I couldn't sign in to Windy Mail, so nothing was sent."}
            if resp.status_code == 409 and _mail_code(resp) in ("idempotency_outcome_unknown", "idempotency_in_progress"):
                # Mail can't tell whether the first try went (Boss 22:24Z: outcome unknown = never resent).
                return {"status": "failed", "error": _UNSURE_SENTENCE}
            if resp.status_code == 422 and _mail_code(resp) == "idempotency_key_reused":
                return {"status": "failed", "error": "The mail server refused it."}  # never Mail's raw wording
            if resp.status_code in (200, 201, 202):
                data = resp.json()
                if resp.headers.get("Idempotent-Replayed", "").lower() == "true":
                    logger.info("Windy Mail: this send was already made (idempotent replay); not sent again")
                    return {"status": "sent", "message_id": data.get("message_id"), "replayed": True}
                logger.info("Windy Mail sent to %d recipient(s) — %s", len(everyone), subject)
                return {"status": "sent", "message_id": data.get("message_id")}
            else:
                logger.warning("Windy Mail send failed: %s %s", resp.status_code, resp.text)
                return {"status": "failed", "error": _mail_error_sentence(resp)}
        except _httpx.ConnectError as e:
            _ak.done("mail.send", recips)  # never reached Mail: nothing was sent
            logger.error("Windy Mail send error: %s", e)
            return {"status": "failed", "error": str(e)}
        except Exception as e:
            # A timeout or a broken connection after the request left: an UNKNOWN outcome (Boss 09:10Z). The key stays
            # the same for a retry in this turn, so Mail can never send it twice.
            logger.error("Windy Mail send error (outcome unknown): %s", type(e).__name__)
            return {"status": "failed", "error": _UNSURE_SENTENCE}

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

        url = f"{self.api_url}/api/v1/inbox"
        try:
            resp = _authed("GET", url, self.jmap_token,
                           lambda headers: _httpx.get(url, params=params, headers=headers, timeout=self.timeout_s))
            if resp is None:
                self.last_error = "no mail credential"
                logger.warning("Windy Mail inbox: %s", self.last_error)
                return []
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
