"""Email send capability — Gmail via OAuth2.

Mirrors calendar's OAuth scaffolding but is registered as a Capability
Plane handler (not legacy ToolRegistry) so every send shows up in the
``agent_actions`` audit ledger from the first invocation. Email is the
highest-stakes outbound action the bot has — a sent email cannot be
unsent, and it crosses the user/recipient trust boundary. Auditing it
is non-negotiable.

Tier: ``EXTERNAL_EFFECT`` (TRUSTED+ band, dry_run supported, no undo).
Gracefully degrades when Gmail OAuth isn't connected — returns a
user-visible "run windy setup-gmail" message instead of crashing.

Setup
-----

1. Reuse the Google OAuth credentials JSON used for calendar (same
   Cloud project; just need to add the ``gmail.send`` scope to the
   OAuth consent screen).
2. ``windy setup-gmail`` (future CLI) → opens browser for consent on
   the ``gmail.send`` scope, writes ``data/gmail_token.json``.
3. Restart the bot — ``email.send`` becomes callable on TRUSTED+
   sessions.

Header-injection defense
------------------------

To/cc/bcc/subject MUST NOT contain CR or LF — caller-supplied newlines
in headers are how Bcc-injection attacks work. We refuse upfront with
``ValueError``; ``MIMEText`` plus modern ``email.policy`` would also
reject these on serialization, but failing at the door gives a clearer
error and never even constructs the message.
"""

from __future__ import annotations

import base64
import logging
import os
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

# Token file lives separately from calendar's because the Gmail scope
# is distinct (sending vs reading events). Credentials JSON can be
# shared across both — same Google Cloud project.
_CREDS_PATH = Path(os.environ.get(
    "GOOGLE_OAUTH_CREDENTIALS",
    os.environ.get(
        "GOOGLE_CALENDAR_CREDENTIALS",
        "data/google_oauth_creds.json",
    ),
))
_TOKEN_PATH = Path(os.environ.get("GMAIL_TOKEN", "data/gmail_token.json"))

_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

# Cap subject + body so a runaway loop can't blast a 50MB email.
_MAX_SUBJECT_LEN = 998   # RFC 2822 line length
_MAX_BODY_BYTES = 5 * 1024 * 1024  # 5 MB


def _is_configured() -> bool:
    """True if a Gmail token file exists. Doesn't validate it works."""
    return _TOKEN_PATH.exists()


def _get_service():
    """Return an authenticated Gmail API service, or None on any failure.

    Returns None for: missing token, missing google libs, bad token,
    refresh failure. The handler converts None to a user-visible
    ``error`` field — the LLM doesn't have to deal with exceptions.
    """
    try:
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except ImportError:
        logger.warning("google-api-python-client not installed; email.send disabled")
        return None
    if not _TOKEN_PATH.exists():
        return None
    try:
        creds = Credentials.from_authorized_user_file(str(_TOKEN_PATH))
        if creds.expired and creds.refresh_token:
            from google.auth.transport.requests import Request
            creds.refresh(Request())
            _TOKEN_PATH.write_text(creds.to_json())
        return build("gmail", "v1", credentials=creds)
    except Exception as e:
        logger.warning("Gmail auth failed: %s", e)
        return None


def _check_no_header_injection(field: str, value: str) -> None:
    """Refuse CR/LF in any header field — Bcc-injection defense."""
    if "\r" in value or "\n" in value:
        raise ValueError(
            f"{field!r} contains a newline — refused (header injection guard)"
        )


def _send_email_handler(
    *, to: str, subject: str, body: str,
    cc: str | None = None, bcc: str | None = None,
    dry_run: bool = False,
    _service_factory=_get_service,
) -> dict[str, Any]:
    """Send an email via Gmail. Body is plain text (no HTML).

    Returns a dict with ``executed``, ``plan``, and either
    ``message_id`` (success) or ``error`` (graceful failure). Never
    raises for operational failures — only for caller-input bugs
    (header injection, missing required fields, oversized payload).
    """
    if not to:
        raise ValueError("to is required")
    if not subject:
        raise ValueError("subject is required")
    if not body:
        raise ValueError("body is required")

    _check_no_header_injection("to", to)
    _check_no_header_injection("subject", subject)
    if cc is not None:
        _check_no_header_injection("cc", cc)
    if bcc is not None:
        _check_no_header_injection("bcc", bcc)

    if len(subject) > _MAX_SUBJECT_LEN:
        raise ValueError(
            f"subject too long ({len(subject)} chars > {_MAX_SUBJECT_LEN})"
        )
    body_bytes = body.encode("utf-8")
    if len(body_bytes) > _MAX_BODY_BYTES:
        raise ValueError(
            f"body too large ({len(body_bytes)} bytes > {_MAX_BODY_BYTES})"
        )

    plan = {
        "action": "send_email",
        "to": to,
        "cc": cc,
        "bcc": bcc,
        "subject": subject,
        "body_chars": len(body),
        "body_bytes": len(body_bytes),
    }

    if not _is_configured():
        return {
            "executed": False,
            "error": (
                "Gmail not connected. Run `windy setup-gmail` to link a "
                "Google account with the gmail.send scope before I can "
                "send email."
            ),
            "plan": plan,
        }

    if dry_run:
        return {"plan": plan, "executed": False, "preview_only": True}

    service = _service_factory()
    if service is None:
        return {
            "executed": False,
            "error": (
                "Gmail authentication failed (token may have expired or "
                "the gmail.send scope is missing). Try re-running "
                "`windy setup-gmail`."
            ),
            "plan": plan,
        }

    msg = MIMEText(body)
    msg["to"] = to
    msg["subject"] = subject
    if cc:
        msg["cc"] = cc
    if bcc:
        msg["bcc"] = bcc

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        result = service.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        logger.info(
            "[email.send] OK: to=%s subject=%r message_id=%s",
            to, subject[:60], result.get("id"),
        )
        return {
            "plan": plan,
            "executed": True,
            "message_id": result.get("id"),
            "thread_id": result.get("threadId"),
            "outcome_score": 1.0,
        }
    except Exception as e:
        logger.warning("[email.send] FAILED: to=%s err=%s", to, e)
        return {
            "executed": False,
            "error": f"Gmail API send failed: {e}",
            "plan": plan,
        }


def register_email_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register ``email.send`` on the Capability Plane."""
    configured = _is_configured()
    logger.info(
        "Registering email.* capabilities (Gmail OAuth, configured=%s, "
        "token=%s)",
        configured, _TOKEN_PATH if configured else "<missing>",
    )

    def email_send(
        *, to: str, subject: str, body: str,
        cc: str | None = None, bcc: str | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _send_email_handler(
            to=to, subject=subject, body=body,
            cc=cc, bcc=bcc, dry_run=dry_run,
        )

    registry.register(Capability(
        id="email.send",
        description=(
            "Send an email via Gmail. Body is plain text (no HTML). "
            "to/cc/bcc are comma-separated addresses. Use dry_run=true "
            "to preview the plan without sending. Requires Gmail OAuth "
            "(run `windy setup-gmail` once if not configured) — until "
            "then this returns a graceful 'not connected' error rather "
            "than failing silently. Tier EXTERNAL_EFFECT (TRUSTED+ "
            "band): once sent, cannot be undone."
        ),
        handler=email_send,
        input_schema={
            "type": "object",
            "properties": {
                "to": {
                    "type": "string",
                    "description": (
                        "Recipient email address(es). For multiple, "
                        "use a comma-separated list."
                    ),
                },
                "subject": {
                    "type": "string",
                    "description": "Email subject line.",
                },
                "body": {
                    "type": "string",
                    "description": "Plain-text body. No HTML.",
                },
                "cc": {
                    "type": "string",
                    "description": (
                        "Optional CC address(es), comma-separated."
                    ),
                },
                "bcc": {
                    "type": "string",
                    "description": (
                        "Optional BCC address(es), comma-separated."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": (
                        "If true, return the send plan without "
                        "actually sending. Useful for confirming the "
                        "recipient + subject before committing."
                    ),
                },
            },
            "required": ["to", "subject", "body"],
        },
        tier=Tier.EXTERNAL_EFFECT,
        scope="gmail",
        audit_required=True,
    ))
