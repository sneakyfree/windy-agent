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
import re
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


def _split_recipients(to: Any) -> list[str]:
    """Accept one address, a comma-separated string, or a list of either.

    LLMs emit any of these depending on how the prompt was phrased. Normalising in one
    place avoids litter at the call sites.
    """
    if to is None:
        return []
    if isinstance(to, (list, tuple, set)):
        out: list[str] = []
        for item in to:
            out.extend(_split_recipients(item))
        return out
    return [r.strip() for r in str(to).replace(";", ",").split(",") if r.strip()]


_ANGLE_RE = re.compile(r"<\s*([^<>\s]+@[^<>\s]+)\s*>")
_ADDR_RE = re.compile(r"^[^@\s,;<>\"']+@[^@\s,;<>\"']+\.[^@\s,;<>\"']+$")
MAX_RECIPIENTS = 50      # Mail enforces the plan caps (free tier 10 per message); this is a sanity bound


def normalize_recipients(to: Any, cc: Any = None, bcc: Any = None) -> tuple[list[str], list[str], list[str]]:
    """(to, cc, bcc) as clean lists: "Name <a@b.c>" becomes "a@b.c", each address once (to wins
    over cc wins over bcc), shape-checked (no header injection). Raises ValueError with a
    plain sentence."""
    seen: set[str] = set()
    out: list[list[str]] = []
    for group in (to, cc, bcc):
        clean: list[str] = []
        for raw in _split_recipients(group):
            m = _ANGLE_RE.search(raw)
            addr = (m.group(1) if m else raw).strip()
            if not _ADDR_RE.match(addr):
                raise ValueError(f"'{raw[:60]}' doesn't look like an email address, so nothing was sent.")
            if addr.lower() in seen:
                continue
            seen.add(addr.lower())
            clean.append(addr)
        out.append(clean)
    if not out[0]:
        raise ValueError("No recipients provided")
    if sum(len(g) for g in out) > MAX_RECIPIENTS:
        raise ValueError(f"That is more than {MAX_RECIPIENTS} recipients on one email, so nothing was sent.")
    return out[0], out[1], out[2]


def with_ai_footer(body: str) -> str:
    """Append the "Sent by <agent>, an AI agent acting for <owner>." line once."""
    from windyfly.tools import outbound_identity

    footer = outbound_identity.email_footer()
    if footer in body:
        return body
    return f"{body.rstrip()}\n\n--\n{footer}\n"


def _send_email_now(
    to: Any, subject: str, body: str, *, cc: Any = None, bcc: Any = None,
    approved_by: str | None = None,
) -> dict[str, Any]:
    """Send ONE email from the agent's own mailbox, with everybody on it.

    ``to``/``cc``/``bcc`` may each be an address, a comma-separated string or a list. One
    message goes out (one ``/send``), so every recipient sees the same mail and the owner
    confirms once. Returns a dict the registry will JSON-encode for the LLM.
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
    path = "windymail"

    try:
        to_l, cc_l, bcc_l = normalize_recipients(to, cc, bcc)
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}

    # Mail to third parties says who is really writing (legal review,
    # 2026-09-23): one footer line on every path.
    if approved_by:
        # Who authorised this send, stated in the message itself.
        body = f"{body}\n\n(Sent with the approval of {approved_by}.)"
    body = with_ai_footer(body)

    total = len(to_l) + len(cc_l) + len(bcc_l)
    try:
        if len(to_l) == 1 and not cc_l and not bcc_l:
            result = adapter.send_email(to_l[0], subject, body)
        else:
            result = adapter.send_email(to_l, subject, body, cc=cc_l or None, bcc=bcc_l or None)
    except Exception as exc:  # rate limiter / trust gate may raise
        result = {"status": "failed", "error": str(exc)}
    # Annotate with the chosen path so downstream observability / the LLM can reason about it.
    result.setdefault("provider", path)
    result.setdefault("recipients", {"to": len(to_l), "cc": len(cc_l), "bcc": len(bcc_l)})
    result.setdefault("total", total)
    return result


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


def _queue_draft(to: Any, subject: str, body: str, cc: Any = None, bcc: Any = None) -> dict[str, Any]:
    import time as _t
    import uuid as _u

    try:
        to_l, cc_l, bcc_l = normalize_recipients(to, cc, bcc)
    except ValueError as exc:
        return {"status": "failed", "error": str(exc)}
    _prune_pending()
    draft_id = _u.uuid4().hex[:8]
    # ONE draft for the whole batch: the owner confirms once and one message goes out.
    _PENDING[draft_id] = {"to": ", ".join(to_l), "cc": ", ".join(cc_l), "bcc": ", ".join(bcc_l),
                          "subject": subject, "body": body, "at": _t.time()}
    return {
        "status": "pending_owner_approval",
        "draft_id": draft_id,
        "to": ", ".join(to_l),
        "cc": ", ".join(cc_l),
        "bcc": ", ".join(bcc_l),
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
    result = _send_email_now(d["to"], d["subject"], d["body"], cc=d.get("cc"), bcc=d.get("bcc"),
                             approved_by=approved_by)
    result["draft_id"] = draft_id
    result.setdefault("to", d["to"])
    return result


def cancel_pending() -> int:
    n = len(_PENDING)
    _PENDING.clear()
    return n


def send_email(to: Any, subject: str, body: str, cc: Any = None, bcc: Any = None) -> dict[str, Any]:
    """Model-facing send: ONE email to everybody (to, cc, bcc).
    With WINDY_SEND_CONFIRM=1 it only DRAFTS (owner approves once)."""
    if send_confirm_enabled():
        return _queue_draft(to, subject, body, cc, bcc)
    return _send_email_now(to, subject, body, cc=cc, bcc=bcc)


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
            "don't need to specify it. Put EVERYONE in ONE call: all "
            "recipients in 'to' (comma-separated), carbon copies in 'cc', "
            "blind copies in 'bcc'. That sends ONE email that all of them "
            "receive; never call this once per person. The owner "
            "confirms once for the whole email. Returns {status, "
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
                        "'alice@example.com, bob@example.com'. They all get ONE email."
                    ),
                },
                "cc": {
                    "type": "string",
                    "description": "Optional carbon-copy addresses, comma-separated (the others see them).",
                },
                "bcc": {
                    "type": "string",
                    "description": "Optional blind-copy addresses, comma-separated (hidden from the others).",
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
