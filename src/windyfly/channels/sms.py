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
from windyfly import sms_optout
from windyfly.observability.events import log_event
from windyfly.sms_optout import mask
from windyfly.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Rate limit: max outbound SMS per day per agent
MAX_OUTBOUND_SMS_PER_DAY = 50

# Opt-out keywords (Windy Legal, LEGAL DECISION 28, BOARD 2026-11-09T11:14:19Z): the carrier-standard set,
# matched on letters only (case, spaces and punctuation ignored), so "opt out", "Opt-Out" and "Stop." all count.
# Short phrases count too, by LEGAL DECISION 28 (5): see _is_opt_out.
_OPT_OUT_KEYWORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"})


# START / UNSTOP undo an opt-out. "YES" is NOT one of them (LEGAL DECISION 28 (4)(c)): a "yes" to anything must
# never re-subscribe a person who said STOP.
_OPT_IN_KEYWORDS = frozenset({"START", "UNSTOP"})

# The opt-out record. A person who texted STOP to this number is never texted again from this channel until they
# text START: send_sms refuses, and their later messages get no reply (LEGAL DECISION 28 (4)(a)). The record holds
# keyed fingerprints, never numbers (LEGAL DECISION 28 (8)): see windyfly.sms_optout.

# Said to the owner ONCE when the fingerprint key is missing while the record has entries (LEGAL DECISION 28 (9)(2)):
# Windy Legal's sentence, kept with the record in windyfly.sms_optout so every texting path says the same words.
KEY_MISSING_NOTICE = sms_optout.KEY_MISSING_NOTICE
_TOLD_FILE = "sms-optout-key-missing.told"
_masked_once: set[str] = set()


def _keyword(body: str) -> str:
    """The message as one keyword: letters only, upper case. "Stop.", "STOP!", " s t o p " and "Opt-Out" all
    become STOP / OPTOUT (Boss 2026-11-09T11:23:26Z). The WHOLE message must be the keyword."""
    return "".join(ch for ch in (body or "") if ch.isalpha()).upper()


# LEGAL DECISION 28 (5) (Windy Legal, BOARD 2026-11-09): short phrases count too.
_STRONG_WORDS = frozenset({"STOP", "STOPALL", "UNSUBSCRIBE", "OPTOUT", "REVOKE"})
_STRONG_MAX_WORDS = 4
_OPT_OUT_PHRASES = tuple(p.split() for p in (
    "STOP TEXTING", "STOP MESSAGING", "STOP SENDING", "DONT TEXT", "DO NOT TEXT", "REMOVE ME", "TAKE ME OFF",
    "OPT ME OUT", "NO MORE TEXTS",
))


def _words(body: str) -> list[str]:
    """The message as letters-only words, upper case: "Don't text me!" -> DONT TEXT ME; "opt-out" -> OPTOUT."""
    out = []
    for token in (body or "").split():
        w = "".join(ch for ch in token if ch.isalpha()).upper()
        if w:
            out.append(w)
    return out


def _has_run(words: list[str], run: list[str]) -> bool:
    n = len(run)
    return any(words[i:i + n] == run for i in range(len(words) - n + 1))


def _is_opt_out(body: str, from_owner: bool = False) -> bool:
    """LEGAL DECISION 28 (5), on letters-only words:
    (1) the whole message is a keyword (STOP STOPALL UNSUBSCRIBE CANCEL END QUIT REVOKE OPTOUT / "OPT OUT");
    (2) a STRONG word (STOP STOPALL UNSUBSCRIBE OPTOUT/"OPT OUT" REVOKE) in a message of 4 words or fewer;
    (3) one of Legal's phrases anywhere, at any length ("stop texting", "remove me", ...);
    (4) END, QUIT and CANCEL count only as the whole message ("cancel my appointment" is a request);
    (5) anything else is an ordinary message.
    LEGAL DECISION 28 (6): from the OWNER's own number (owner_numbers(), explicit settings only) only (1) counts:
    "don't text Bob until Monday" is an order to the agent, and the owner can always send STOP."""
    if _keyword(body) in _OPT_OUT_KEYWORDS:
        return True
    if from_owner:
        return False
    words = _words(body)
    if len(words) <= _STRONG_MAX_WORDS and (_STRONG_WORDS & set(words) or _has_run(words, ["OPT", "OUT"])):
        return True
    return any(_has_run(words, p) for p in _OPT_OUT_PHRASES)


def _is_help(body: str) -> bool:
    return _keyword(body) == "HELP"


def help_reply() -> str:
    """LEGAL DECISION 28 (2), exact words (Legal BOARD 2026-11-09T11:21:40Z; Boss 11:21:06Z). Contract id sms.help
    lands in tool-phrases 1.1 under P2. HELP goes to strangers too, so the owner's FIRST name only, as the SMS
    sign-off gives (Legal, privacy); with no owner name set, Legal's confirmed fallback is "its owner"."""
    from windyfly.tools import outbound_identity as oi

    owner = oi.owner_first_name() or "its owner"
    return (f"This is {oi.agent_name()}, an AI assistant texting for {owner} through Windy. "
            "For help, email support@windyword.ai. Reply STOP to opt out.")


def _is_opt_in(body: str) -> bool:
    return _keyword(body) in _OPT_IN_KEYWORDS


_number_key = sms_optout.number_key


def non_owner_allowed() -> bool:
    """WINDY_SMS_ALLOW_NON_OWNER=1 lets this channel text numbers other than the owner's own. Default OFF (Boss's
    opt-out order, BOARD 2026-11-09T11:18:02Z (1)(b)): with it off, the only person this channel ever texts is the
    owner."""
    return os.environ.get("WINDY_SMS_ALLOW_NON_OWNER", "").strip() == "1"


owner_numbers = sms_optout.owner_numbers   # the owner rule lives with the record


def may_text(number: str, config: dict[str, Any] | None = None) -> bool:
    """Owner-only unless the allow switch is on."""
    return non_owner_allowed() or _number_key(number) in owner_numbers(config)


def optout_state(number: str, db_path: str | None = None, config: dict[str, Any] | None = None) -> str:
    """For a send path that has no channel object (the /sms command): "clear", "opted_out" or "key_missing". Reads
    the same sms_optouts table, in the SAME file the channel uses: resolve_db_path(config memory.db_path), as
    main.py does (Hub 2026-11-09T11:25:19Z). With neither a path nor a config it cannot know which file the channel
    uses, so it answers "unreadable" (refuse, and nobody is said to have replied STOP). "key_missing" = the record has fingerprints and the key is gone
    (LEGAL DECISION 28 (9)(2)): every number but the owner's is refused."""
    from windyfly.memory.db_location import resolve_db_path

    if db_path is None and config is None:
        return sms_optout.UNREADABLE
    path = db_path or resolve_db_path(((config or {}).get("memory") or {}).get("db_path"))
    return sms_optout.send_state(number, path, config)


def number_opted_out(number: str, db_path: str | None = None, config: dict[str, Any] | None = None) -> bool:
    """True when this number must not be texted: it opted out, or nobody can tell (see optout_state)."""
    return optout_state(number, db_path, config) != sms_optout.CLEAR


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

        self.optouts = sms_optout.OptOutStore(self.db)
        if str(self.db.db_path) not in _masked_once:      # once per process: lines 0.9.x wrote with whole numbers
            _masked_once.add(str(self.db.db_path))
            sms_optout.mask_old_events(self.db)

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
        self.optouts.record(number)

    def clear_opt_out(self, number: str) -> None:
        self.optouts.clear(number)

    def is_opted_out(self, number: str) -> bool:
        return self.optouts.state(number) == sms_optout.OPTED_OUT

    def _blocked(self, number: str) -> str | None:
        """Why this number may not be texted, or None. "optout_key_missing" = the record cannot be read (its key is
        gone), so only the owner is texted (LEGAL DECISION 28 (9)(2))."""
        state = self.optouts.state(number)
        if state == sms_optout.OPTED_OUT:
            return "recipient_opted_out"
        if state == sms_optout.KEY_MISSING and _number_key(number) not in owner_numbers(self.config):
            return "optout_key_missing"
        return None

    def _tell_owner_once(self) -> str | None:
        """The notice text the first time, None after. The marker goes away when the key is back."""
        from pathlib import Path

        if sms_optout.in_memory(self.db.db_path):
            return None
        marker = Path(str(self.db.db_path)).parent / _TOLD_FILE
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return None
        except OSError:
            return None
        os.close(fd)
        import threading

        from windyfly.agent import owner_mail

        threading.Thread(target=owner_mail.send_sync, daemon=True,
                         args=("Your agent can only text you for now", KEY_MISSING_NOTICE)).start()
        return KEY_MISSING_NOTICE

    def _forget_told(self) -> None:
        from pathlib import Path

        if sms_optout.in_memory(self.db.db_path):
            return
        try:
            os.unlink(Path(str(self.db.db_path)).parent / _TOLD_FILE)
        except OSError:
            pass

    def handle_inbound(self, from_number: str, body: str) -> str:
        """Handle an inbound SMS message.

        Args:
            from_number: Sender's phone number (e.g. +15551234567).
            body: SMS message body.

        Returns:
            Agent's response text.
        """
        # Handle STOP/opt-out: record it FIRST, then confirm (the one text a person gets after STOP).
        from_owner = _number_key(from_number) in owner_numbers(self.config)
        if _is_opt_out(body, from_owner=from_owner):
            self.record_opt_out(from_number)
            log_event(self.db, self.write_queue, "sms.optout", {"phone": mask(from_number)})
            return "You've been unsubscribed. Reply START to re-subscribe."

        # START/UNSTOP: clear the record. No reply of our own (no new sentence; Twilio may send its own).
        if _is_opt_in(body):
            self.clear_opt_out(from_number)
            log_event(self.db, self.write_queue, "sms.optin", {"phone": mask(from_number)})
            return ""

        # HELP: the required compliance reply, to ANYONE, an opted-out person or a non-owner too (they asked for
        # it; LEGAL DECISION 28, Legal 11:21:40Z). The agent is not asked.
        if _is_help(body):
            log_event(self.db, self.write_queue, "sms.help", {"phone": mask(from_number)})
            return help_reply()

        # Still opted out, or nobody can tell (the key is missing): no reply at all, and the agent is not asked.
        blocked = self._blocked(from_number)
        if blocked == "recipient_opted_out":
            log_event(self.db, self.write_queue, "sms.inbound_opted_out", {"phone": mask(from_number)})
            return ""
        if blocked:
            log_event(self.db, self.write_queue, "sms.inbound_key_missing", {"phone": mask(from_number)})
            return ""

        # Owner only unless the allow switch is on: a reply is a text too, so a non-owner gets none and the agent is
        # not asked (Boss's opt-out order (1)(b)). The STOP confirmation and the HELP reply above still go to anyone.
        if not may_text(from_number, self.config):
            log_event(self.db, self.write_queue, "sms.inbound_not_owner", {"phone": mask(from_number)})
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
            "from": mask(from_number),
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
        blocked = self._blocked(to_number)
        if blocked == "recipient_opted_out":
            # Never text a person who sent STOP (LEGAL DECISION 28). Same code as Windy Text's refusal.
            log_event(self.db, self.write_queue, "sms.outbound_refused", {"to": mask(to_number), "code": blocked})
            return {"status": "refused", "error": blocked}
        if blocked:
            # The record cannot be read (its key is gone): only the owner is texted, and the owner hears why once.
            log_event(self.db, self.write_queue, "sms.outbound_refused", {"to": mask(to_number), "code": blocked})
            out: dict[str, Any] = {"status": "refused", "error": blocked}
            notice = self._tell_owner_once()
            if notice:
                out["owner_notice"] = notice
            return out
        if not self.optouts.failed_closed:
            self._forget_told()

        if not may_text(to_number, self.config):
            # Boss's opt-out order (1)(b): owner only unless WINDY_SMS_ALLOW_NON_OWNER=1.
            log_event(self.db, self.write_queue, "sms.outbound_refused", {"to": mask(to_number), "code": "not_owner"})
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
                    "to": mask(to_number),
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
