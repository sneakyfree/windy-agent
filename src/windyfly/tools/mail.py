"""Mail tools — let the LLM send email and read its inbox.

Wraps ``windyfly.channels.email.WindyMailAdapter`` so the existing
trust-gate + rate-limiter + Mail-API plumbing is reused untouched.
The adapter requires ``WINDYMAIL_EMAIL`` and ``WINDYMAIL_JMAP_TOKEN``;
when those aren't set (e.g. agent never went through hatch with a
provisioned mailbox), tools return a structured "unavailable" result
the LLM can interpret rather than crashing the whole tool call.

**One send path (Hub, 2026-10-02):** an agent sends ONLY from its own
Windy Mail mailbox, through Windy Mail's ``/api/v1/send`` with its
Eternitas passport token. There is no Resend, Gmail or SendGrid
fallback, so From and Reply-To are always the agent's own address and
every send gets Mail's limits, bounce handling and Sent folder.

Why not fold this into ``channels/email.py``? That file holds the
CLASSES that own the auth/rate-limit lifecycle. This module turns
those classes into LLM-callable tool functions with OpenAI-format
schemas. Splitting keeps ``channels/email.py`` LLM-agnostic and
``tools/mail.py`` adapter-agnostic — either layer can be swapped
without touching the other.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


def _adapter() -> Any | None:
    """Return a ``WindyMailAdapter`` or ``None`` if env isn't set.

    The adapter raises ``RuntimeError`` if ``WINDYMAIL_EMAIL`` or
    ``WINDYMAIL_JMAP_TOKEN`` is unset. We catch that here so tool
    *registration* always succeeds — only tool *execution* surfaces
    the missing-config state, as a structured response the LLM can
    explain to the user.
    """
    from windyfly.channels.email import WindyMailAdapter

    try:
        return WindyMailAdapter()
    except RuntimeError as exc:
        logger.debug("WindyMailAdapter unavailable: %s", exc)
        return None


def _split_recipients(to: str) -> list[str]:
    """Accept a single address or a comma-separated list.

    LLMs tend to emit either form depending on how the prompt was
    phrased. Normalising in one place avoids litter at the call sites.
    """
    return [r.strip() for r in to.split(",") if r.strip()]


def with_ai_footer(body: str) -> str:
    """Append the "Sent by <agent>, an AI agent acting for <owner>." line once."""
    from windyfly.tools import outbound_identity

    footer = outbound_identity.email_footer()
    if footer in body:
        return body
    return f"{body.rstrip()}\n\n--\n{footer}\n"


def _send_email_now(
    to: str, subject: str, body: str, *, approved_by: str | None = None,
) -> dict[str, Any]:
    """Send an email via the agent's own mailbox.

    ``to`` may be a single address or a comma-separated list. Returns
    a dict the registry will JSON-encode for the LLM. On multi-
    recipient sends, status is ``sent`` only if every recipient
    succeeded; ``partial`` if some failed; ``failed`` if all failed.
    """
    adapter = _adapter()
    if adapter is None:
        # No fallback by design: nothing leaves except from the agent's own mailbox.
        return {
            "status": "unavailable",
            "error": (
                "This agent's own Windy Mail mailbox isn't ready to send, "
                "so nothing was sent. Tell the owner."
            ),
        }
    send_fn = adapter.send_email
    path = "windymail"

    recipients = _split_recipients(to)
    if not recipients:
        return {"status": "failed", "error": "No recipients provided"}

    # Mail to third parties says who is really writing (legal review,
    # 2026-09-23): one footer line on every path.
    if approved_by:
        # Who authorised this send, stated in the message itself.
        body = f"{body}\n\n(Sent with the approval of {approved_by}.)"
    body = with_ai_footer(body)

    if len(recipients) == 1:
        result = send_fn(recipients[0], subject, body)
        # Annotate with the chosen path so downstream observability /
        # the LLM can reason about which provider answered.
        result.setdefault("provider", path)
        return result

    per_recipient: list[dict[str, Any]] = []
    successes = 0
    for recipient in recipients:
        try:
            result = send_fn(recipient, subject, body)
        except Exception as exc:  # rate limiter / trust gate may raise
            result = {"status": "failed", "error": str(exc)}
        if result.get("status") == "sent":
            successes += 1
        result.setdefault("provider", path)
        per_recipient.append({"to": recipient, **result})

    if successes == len(recipients):
        overall = "sent"
    elif successes == 0:
        overall = "failed"
    else:
        overall = "partial"

    return {
        "status": overall,
        "successes": successes,
        "total": len(recipients),
        "per_recipient": per_recipient,
    }


# ── owner confirmation before outbound sends (dark: WINDY_SEND_CONFIRM=1) ────────
# The MODEL can only DRAFT. The owner's own message ('send' / 'cancel'), handled
# in code by channels.base (never by the model), is what actually sends.
_PENDING: dict[str, dict[str, Any]] = {}
PENDING_TTL_S = 30 * 60


def send_confirm_enabled() -> bool:
    return os.environ.get("WINDY_SEND_CONFIRM", "") == "1"


def _prune_pending() -> None:
    import time as _t

    now = _t.time()
    for k in [k for k, v in _PENDING.items() if now - v["at"] > PENDING_TTL_S]:
        _PENDING.pop(k, None)


def _queue_draft(to: str, subject: str, body: str) -> dict[str, Any]:
    import time as _t
    import uuid as _u

    _prune_pending()
    draft_id = _u.uuid4().hex[:8]
    _PENDING[draft_id] = {"to": to, "subject": subject, "body": body, "at": _t.time()}
    return {
        "status": "pending_owner_approval",
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "note": (
            "NOT SENT. This is a draft. Show the owner the recipient, subject and body "
            "and tell them to reply 'send' to approve or 'cancel' to drop it. "
            "Never say the email was sent."
        ),
    }


def pending_drafts() -> list[dict[str, Any]]:
    _prune_pending()
    return [{"draft_id": k, **v} for k, v in sorted(_PENDING.items(), key=lambda kv: kv[1]["at"])]


def approve_latest(approved_by: str) -> dict[str, Any]:
    """Send the newest pending draft. Called only from the owner's own message."""
    _prune_pending()
    if not _PENDING:
        return {"status": "none", "error": "There is no draft waiting for approval."}
    draft_id = max(_PENDING, key=lambda k: _PENDING[k]["at"])
    d = _PENDING.pop(draft_id)
    result = _send_email_now(d["to"], d["subject"], d["body"], approved_by=approved_by)
    result["draft_id"] = draft_id
    result.setdefault("to", d["to"])
    return result


def cancel_pending() -> int:
    n = len(_PENDING)
    _PENDING.clear()
    return n


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Model-facing send. With WINDY_SEND_CONFIRM=1 it only DRAFTS (owner approves)."""
    if send_confirm_enabled():
        return _queue_draft(to, subject, body)
    return _send_email_now(to, subject, body)


def list_inbox(unread_only: bool = False, limit: int = 20) -> dict[str, Any]:
    """List recent messages in the agent's inbox.

    Returns ``{messages, count, unread_only}`` on success or
    ``{status: "unavailable", messages: []}`` if the adapter isn't
    configured. ``limit`` clamps the returned slice; the underlying
    adapter doesn't paginate, so this is a client-side trim.
    """
    adapter = _adapter()
    if adapter is None:
        return {
            "status": "unavailable",
            "messages": [],
            "error": "Email is not configured for this agent.",
        }

    messages = adapter.check_inbox(unread_only=unread_only)
    trimmed = messages[: max(0, limit)]
    result: dict[str, Any] = {
        "messages": trimmed,
        "count": len(trimmed),
        "unread_only": unread_only,
    }
    last_error = getattr(adapter, "last_error", "")
    if not messages and last_error:
        # An empty list because Mail did not answer is not "no mail".
        result["status"] = "error"
        result["error"] = f"Could not reach the mailbox ({last_error})."
    return result


def register_mail_tools(registry: ToolRegistry) -> None:
    """Register ``send_email`` and ``list_inbox`` with the tool registry."""
    registry.register(
        name="send_email",
        description=(
            "Send an email from the agent's own mailbox. Use this whenever "
            "the user asks you to email someone — e.g. 'email Bob the "
            "report' or 'send a thank-you note to alice@example.com'. The "
            "'from' address is your agent's mailbox automatically; you "
            "don't need to specify it. Multiple recipients can be passed "
            "as a single comma-separated string. Returns {status, "
            "message_id} on success, or {status: 'unavailable', error} "
            "if email isn't configured for this agent. Always verify the "
            "recipient address with the user before sending if it wasn't "
            "explicit in the request."
        ),
        parameters={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Recipient email address. For multiple recipients, "
                        "pass a comma-separated string like "
                        "'alice@example.com, bob@example.com'."
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "Subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text email body.",
                },
            },
            "required": ["to", "subject", "body"],
        },
        fn=send_email,
    )

    registry.register(
        name="list_inbox",
        description=(
            "List recent messages in the agent's own inbox. Use when the "
            "user asks 'has anyone emailed me?', 'check my inbox', or when "
            "context suggests the agent should be aware of incoming "
            "correspondence. Returns {messages, count, unread_only}."
        ),
        parameters={
            "type": "object",
            "properties": {
                "unread_only": {
                    "type": "boolean",
                    "description": "If true, only return unread messages.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum messages to return (default 20).",
                },
            },
            "required": [],
        },
        fn=list_inbox,
    )
