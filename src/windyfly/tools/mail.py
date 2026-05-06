"""Mail tools — let the LLM send email and read its inbox.

Wraps ``windyfly.channels.email.WindyMailAdapter`` so the existing
trust-gate + rate-limiter + Mail-API plumbing is reused untouched.
The adapter requires ``WINDYMAIL_EMAIL`` and ``WINDYMAIL_JMAP_TOKEN``;
when those aren't set (e.g. agent never went through hatch with a
provisioned mailbox), tools return a structured "unavailable" result
the LLM can interpret rather than crashing the whole tool call.

Why not fold this into ``channels/email.py``? That file holds the
CLASSES that own the auth/rate-limit lifecycle. This module turns
those classes into LLM-callable tool functions with OpenAI-format
schemas. Splitting keeps ``channels/email.py`` LLM-agnostic and
``tools/mail.py`` adapter-agnostic — either layer can be swapped
without touching the other.
"""

from __future__ import annotations

import logging
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


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    """Send an email via the agent's own mailbox.

    ``to`` may be a single address or a comma-separated list. Returns
    a dict the registry will JSON-encode for the LLM. On multi-
    recipient sends, status is ``sent`` only if every recipient
    succeeded; ``partial`` if some failed; ``failed`` if all failed.
    """
    adapter = _adapter()
    if adapter is None:
        return {
            "status": "unavailable",
            "error": (
                "Email is not configured for this agent. "
                "WINDYMAIL_EMAIL and WINDYMAIL_JMAP_TOKEN must be set "
                "(usually populated by mail provisioning during hatch)."
            ),
        }

    recipients = _split_recipients(to)
    if not recipients:
        return {"status": "failed", "error": "No recipients provided"}

    if len(recipients) == 1:
        result = adapter.send_email(recipients[0], subject, body)
        # Adapter returns {status, message_id} or {status, error}; pass through.
        return result

    per_recipient: list[dict[str, Any]] = []
    successes = 0
    for recipient in recipients:
        try:
            result = adapter.send_email(recipient, subject, body)
        except Exception as exc:  # rate limiter / trust gate may raise
            result = {"status": "failed", "error": str(exc)}
        if result.get("status") == "sent":
            successes += 1
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
    return {
        "messages": trimmed,
        "count": len(trimmed),
        "unread_only": unread_only,
    }


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
