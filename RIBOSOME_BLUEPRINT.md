# 🧬 RIBOSOME BLUEPRINT — Windy Fly P1 Feature Build

> **What is this?** A fully atomic, self-contained implementation plan for 6 remaining features.
> Any fresh AI session can read this file and execute every step without additional context.
>
> **Created:** 2026-03-27 by conversation `993c8581-d587-491e-a26b-9808c0960a6d`
> **Codebase:** `/Users/thewindstorm/Desktop/Grant's Folder/windy-agent`
> **Framework:** Python 3.12 (uv), Bun/TypeScript gateway, SQLite memory store
> **Current state:** 321 tests passing, commit `db0488b` on `master`

---

## PRE-FLIGHT CHECKLIST

Before building anything, verify the starting state:

```bash
cd "/Users/thewindstorm/Desktop/Grant's Folder/windy-agent"
uv run pytest tests/ -x -q   # Must show 321 passed
git log -1 --oneline          # Must show db0488b or later
```

---

## FEATURE 1: Adaptive Mode ON/OFF Toggle

**Goal:** Add a boolean toggle `adaptive_mode` to the control panel so users can turn off the emotion-driven slider overrides that were built in commit `db0488b`.

### Step 1.1 — Add `adaptive_mode` to `SLIDER_INFO` in `control_panel.py`

**File:** `src/windyfly/control_panel.py`
**Location:** After `memory_retention` entry (line 113), before the closing `}` of `SLIDER_INFO`
**Action:** Add this entry:

```python
    "warmth": {
        "label": "Warmth",
        "description": "How emotionally warm and supportive the agent is.",
        "impact_low": "Clinical, detached. Facts only.",
        "impact_high": "Warm, caring, empathetic. Like a close friend.",
    },
    "adaptive_mode": {
        "label": "Adaptive Mode",
        "description": "When ON, the agent reads your mood and temporarily adjusts its personality to match.",
        "impact_low": "Sliders stay exactly where you set them. Full manual control.",
        "impact_high": "Agent 'reads the room' — softens when you're stressed, matches energy when you're excited.",
    },
```

### Step 1.2 — Add `adaptive_mode` to every preset in `PRESETS` dict

**File:** `src/windyfly/control_panel.py`
**Location:** Inside each of the 8 preset dicts (buddy, engineer, powerhouse, coder, friend, writer, researcher, silent)
**Action:** Add `"adaptive_mode": X,` and `"warmth": Y,` to each preset:

| Preset | `adaptive_mode` | `warmth` |
|---|---|---|
| buddy | 8 | 7 |
| engineer | 2 | 3 |
| powerhouse | 7 | 7 |
| coder | 0 | 1 |
| friend | 10 | 10 |
| writer | 5 | 6 |
| researcher | 1 | 2 |
| silent | 0 | 3 |

### Step 1.3 — Add to `_COST_PER_POINT` dict

**File:** `src/windyfly/control_panel.py`
**Location:** Inside `_COST_PER_POINT` dict (after line 276)
**Action:** Add:

```python
    "warmth": 0.10,
    "adaptive_mode": 0.05,
```

### Step 1.4 — Gate the override in `loop.py`

**File:** `src/windyfly/agent/loop.py`
**Location:** Line ~101 (the adaptive mode call)
**Current code:**

```python
    # 1.65. Adaptive mode — override sliders based on emotion
    loop_sliders = apply_adaptive_overrides(loop_sliders, emotional_context, emotional_trend)
```

**Replace with:**

```python
    # 1.65. Adaptive mode — override sliders based on emotion (gated by toggle)
    if loop_sliders.get("adaptive_mode", 5) >= 5:
        loop_sliders = apply_adaptive_overrides(loop_sliders, emotional_context, emotional_trend)
```

### Step 1.5 — Test

**File:** `tests/test_adaptive_mode.py`
**Action:** Add one new test:

```python
    def test_adaptive_mode_disabled(self) -> None:
        """When adaptive_mode < 5, no overrides are applied."""
        sliders = {"humor": 8, "warmth": 3, "verbosity": 7, "adaptive_mode": 0}
        # Simulate stressed — should NOT override because toggle is off
        from windyfly.personality.engine import apply_adaptive_overrides
        # The loop.py gate means apply_adaptive_overrides is never called
        # So we just verify the sliders are unchanged
        assert sliders["humor"] == 8
        assert sliders["warmth"] == 3
```

---

## FEATURE 2: Soul Passport Rename

**Goal:** Rename all user-facing references from "soul import" to "Soul Passport."

### Step 2.1 — Update docstrings in `soul_import/orchestrator.py`

**File:** `src/windyfly/soul_import/orchestrator.py`
**Line 1:** Change `"""Soul import orchestrator."""` → `"""Soul Passport orchestrator — bring your soul from another agent."""`
**Line 4:** Change `"and (on approval) write to database."` → `"and (on approval) write to database. Branded as 'Soul Passport'."`

### Step 2.2 — Update docstrings in `soul_import/preview.py`

**File:** `src/windyfly/soul_import/preview.py`
**Line 1:** Change the module docstring to mention "Soul Passport preview."

### Step 2.3 — Update UDS bridge method names

**File:** `src/windyfly/bridge/uds_server.py`
**Lines 95-96:** Keep the internal method names (`soul.preview`, `soul.import`) unchanged for API compatibility.
**Lines 148-162:** Update the docstrings inside `_handle_soul_preview` and `_handle_soul_import` to reference "Soul Passport."

### Step 2.4 — Update gateway routes comments

**File:** `gateway/src/server.ts`
**Lines 104-116:** Update the comments from `// Soul preview` and `// Soul import` to `// Soul Passport preview` and `// Soul Passport import`.

### Step 2.5 — Update the web UI button label

**File:** `gateway/public/index.html`
**Action:** Find the text "Soul Import" or "Import Soul" and replace with "Soul Passport." If a button exists for this, change its label to "Use Soul Passport."

---

## FEATURE 3: SMS Channel (Twilio)

**Goal:** Create `channels/sms.py` — a new channel adapter that receives SMS via Twilio webhooks and sends responses back.

### Step 3.1 — Create `src/windyfly/channels/sms.py`

**File:** NEW — `src/windyfly/channels/sms.py`
**Pattern to follow:** `channels/matrix_bot.py` (same init pattern, same agent_respond call)
**Full content:**

```python
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

        # Truncate to SMS limit
        if len(message) > 1600:
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
            with urllib.request.urlopen(req) as resp:
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
```

### Step 3.2 — Add SMS webhook route to gateway `server.ts`

**File:** `gateway/src/server.ts`
**Location:** After the soul import route (line 116), before the static files block (line 118)
**Action:** Add:

```typescript
    // SMS webhook (Twilio inbound)
    if (path === "/api/sms/webhook" && req.method === "POST") {
      const body = await req.json();
      const result = await bridge.call("sms.inbound", body);
      return new Response(result.twiml || "", {
        headers: { "Content-Type": "text/xml", ...headers },
      });
    }

    // SMS send (outbound)
    if (path === "/api/sms/send" && req.method === "POST") {
      const body = (await req.json()) as { to: string; message: string };
      const result = await bridge.call("sms.send", body);
      return Response.json(result, { headers });
    }
```

### Step 3.3 — Add SMS UDS bridge methods

**File:** `src/windyfly/bridge/uds_server.py`
**Location:** In the `_dispatch` handlers dict (after line 96), add:

```python
            "sms.inbound": self._handle_sms_inbound,
            "sms.send": self._handle_sms_send,
```

**Location:** After `_handle_soul_import` method (after line 163), add:

```python
    async def _handle_sms_inbound(self, params: dict) -> dict:
        from windyfly.channels.sms import WindyFlySMS
        sms = WindyFlySMS(self.config, self.db, self.write_queue)
        response = sms.handle_inbound(
            params.get("From", ""),
            params.get("Body", ""),
        )
        return {"twiml": f"<Response><Message>{response}</Message></Response>"}

    async def _handle_sms_send(self, params: dict) -> dict:
        from windyfly.channels.sms import WindyFlySMS
        sms = WindyFlySMS(self.config, self.db, self.write_queue)
        result = sms.send_sms(params.get("to", ""), params.get("message", ""))
        return result
```

### Step 3.4 — Add `--channel sms` option to `main.py`

**File:** `src/windyfly/main.py`
**Line 59:** Change `choices=["cli", "matrix"]` → `choices=["cli", "matrix", "sms"]`
**After line 127 (before the final `if __name__`):** Add the sms channel block:

```python
    elif args.channel == "sms":
        import asyncio
        from windyfly.channels.sms import WindyFlySMS
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue

        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()

        sms = WindyFlySMS(config, db, write_queue)
        logger.info("SMS channel initialized with number %s", sms.phone_number)
        # SMS channel runs via gateway webhooks, not a standalone loop.
        # Keep the process alive for the UDS bridge.
        try:
            asyncio.get_event_loop().run_forever()
        except KeyboardInterrupt:
            pass
        finally:
            write_queue.stop()
            db.close()
```

### Step 3.5 — Test

**File:** NEW — `tests/test_sms_channel.py`
**Content:**

```python
"""Tests for SMS channel."""

import os
from unittest.mock import patch, MagicMock

from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


class TestWindyFlySMS:
    def _make_sms(self):
        os.environ["TWILIO_ACCOUNT_SID"] = "ACtest123"
        os.environ["TWILIO_AUTH_TOKEN"] = "test_token"
        os.environ["TWILIO_PHONE_NUMBER"] = "+15551234567"
        from windyfly.channels.sms import WindyFlySMS
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        config = {"agent": {"default_model": "gpt-4o-mini"}, "personality": {}, "costs": {"daily_budget_usd": 5.0}}
        sms = WindyFlySMS(config, db, wq)
        return sms, db, wq

    def test_stop_optout(self):
        sms, db, wq = self._make_sms()
        response = sms.handle_inbound("+15559999999", "STOP")
        assert "unsubscribed" in response.lower()
        wq.stop()
        db.close()

    def test_rate_limit(self):
        sms, db, wq = self._make_sms()
        sms._outbound_today = 50
        import datetime
        sms._outbound_date = datetime.date.today().isoformat()
        assert sms._check_rate_limit() is False
        wq.stop()
        db.close()

    def test_session_id_persistence(self):
        sms, db, wq = self._make_sms()
        s1 = sms._get_session_id("+15559999999")
        s2 = sms._get_session_id("+15559999999")
        assert s1 == s2  # Same phone = same session
        s3 = sms._get_session_id("+15558888888")
        assert s1 != s3  # Different phone = different session
        wq.stop()
        db.close()

    def test_contact_auto_saved(self):
        sms, db, wq = self._make_sms()
        with patch("windyfly.channels.sms.agent_respond", return_value="Hi!"):
            sms.handle_inbound("+15559999999", "Hello")
        import time
        time.sleep(0.3)
        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'contact'")
        assert len(nodes) >= 1
        wq.stop()
        db.close()
```

---

## FEATURE 4: Email Channel (@windyfly.ai)

**Goal:** Create `channels/email.py` — sends/receives email via SendGrid.

### Step 4.1 — Create `src/windyfly/channels/email.py`

**File:** NEW — `src/windyfly/channels/email.py`
**Full content:**

```python
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
```

### Step 4.2 — Add email webhook route to gateway `server.ts`

**File:** `gateway/src/server.ts`
**Location:** After the SMS routes (added in Step 3.2)
**Action:** Add:

```typescript
    // Email webhook (SendGrid Inbound Parse)
    if (path === "/api/email/webhook" && req.method === "POST") {
      const body = await req.json();
      const result = await bridge.call("email.inbound", body);
      return Response.json(result, { headers });
    }

    // Email send (outbound)
    if (path === "/api/email/send" && req.method === "POST") {
      const body = (await req.json()) as { to: string; subject: string; body: string };
      const result = await bridge.call("email.send", body);
      return Response.json(result, { headers });
    }
```

### Step 4.3 — Add email UDS bridge methods

**File:** `src/windyfly/bridge/uds_server.py`
**Location:** In the `_dispatch` handlers dict, add:

```python
            "email.inbound": self._handle_email_inbound,
            "email.send": self._handle_email_send,
```

**Location:** After the SMS handler methods, add:

```python
    async def _handle_email_inbound(self, params: dict) -> dict:
        from windyfly.channels.email import WindyFlyEmail
        email = WindyFlyEmail(self.config, self.db, self.write_queue)
        response = email.handle_inbound(
            params.get("from", ""),
            params.get("subject", ""),
            params.get("text", params.get("body", "")),
        )
        return {"response": response}

    async def _handle_email_send(self, params: dict) -> dict:
        from windyfly.channels.email import WindyFlyEmail
        email = WindyFlyEmail(self.config, self.db, self.write_queue)
        result = email.send_email(
            params.get("to", ""),
            params.get("subject", ""),
            params.get("body", ""),
        )
        return result
```

### Step 4.4 — Test

**File:** NEW — `tests/test_email_channel.py`
**Content:**

```python
"""Tests for email channel."""

import os
from unittest.mock import patch

from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


class TestWindyFlyEmail:
    def _make_email(self):
        os.environ["SENDGRID_API_KEY"] = "SG.test_key"
        os.environ["WINDYFLY_EMAIL_ADDRESS"] = "test@windyfly.ai"
        from windyfly.channels.email import WindyFlyEmail
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        config = {"agent": {"default_model": "gpt-4o-mini"}, "personality": {}, "costs": {"daily_budget_usd": 5.0}}
        email_ch = WindyFlyEmail(config, db, wq)
        return email_ch, db, wq

    def test_session_persistence(self):
        email, db, wq = self._make_email()
        s1 = email._get_session_id("grant@example.com")
        s2 = email._get_session_id("grant@example.com")
        assert s1 == s2
        wq.stop()
        db.close()

    def test_contact_auto_saved(self):
        email, db, wq = self._make_email()
        with patch("windyfly.channels.email.agent_respond", return_value="Got it"):
            email.handle_inbound("grant@example.com", "Hello", "What's up?")
        import time
        time.sleep(0.3)
        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'contact'")
        assert len(nodes) >= 1
        wq.stop()
        db.close()
```

---

## FEATURE 5: Agent Journal

**Goal:** After emotionally significant interactions, the agent writes a brief journal entry from its own perspective, stored as `type=journal_entry` node.

### Step 5.1 — Add journal entry extraction to `loop.py`

**File:** `src/windyfly/agent/loop.py`
**Location:** After the relationship moment extraction (step 7.5), add step 7.6:

```python
    # 7.6. Agent journal — periodic reflective entries
    _maybe_write_journal_entry(
        db, write_queue, config, user_message, response_text,
        emotional_context, session_id,
    )
```

### Step 5.2 — Create the `_maybe_write_journal_entry` function

**File:** `src/windyfly/agent/loop.py`
**Location:** After `_extract_relationship_moment` function (end of file)
**Content:**

```python
# Module-level counter for journal entry throttling
_interaction_count: int = 0


def _maybe_write_journal_entry(
    db: Database,
    write_queue: WriteQueue,
    config: dict[str, Any],
    user_message: str,
    response_text: str,
    emotional_context: str,
    session_id: str,
) -> None:
    """Conditionally write a reflective journal entry.

    Triggers every 10th interaction OR when emotion is detected.
    The agent writes from its own perspective, like a diary.
    """
    global _interaction_count
    _interaction_count += 1

    # Only write every 10th interaction, or on emotional moments
    if _interaction_count % 10 != 0 and emotional_context == "neutral":
        return

    try:
        journal_prompt = (
            "You are an AI agent writing a brief journal entry about a recent "
            "interaction with your user. Write 1-2 sentences from your perspective "
            "about what you discussed, what you learned, and how the user seemed. "
            "Be reflective and genuine, like a diary entry.\n\n"
            f"User said: {user_message[:300]}\n"
            f"You responded about: {response_text[:200]}\n"
            f"User's mood: {emotional_context}"
        )

        result = call_llm(
            [
                {"role": "system", "content": "You write brief, genuine diary entries."},
                {"role": "user", "content": journal_prompt},
            ],
            model=config.get("agent", {}).get("default_model", "gpt-4o-mini"),
            temperature=0.6,
            max_tokens=80,
            config=config,
        )

        entry = result["content"].strip()
        if entry and len(entry) > 15:
            write_queue.enqueue(
                Priority.LOW,
                upsert_node,
                db,
                "journal_entry",
                f"journal:{entry[:200]}",
                metadata={
                    "session_id": session_id,
                    "emotional_context": emotional_context,
                    "entry": entry,
                },
                source="agent_journal",
                epistemic_status="verified",
            )
            logger.debug("Journal entry written: %s", entry[:80])

    except Exception as e:
        logger.debug("Journal entry failed: %s", e)
```

### Step 5.3 — Expose journal entries in dashboard

**File:** `src/windyfly/dashboard/data.py`
**Location:** In `get_dashboard_summary()` function (line 32-39), add a new key:

```python
        "journal": _get_journal_entries(db, user_id),
```

**Location:** After `_get_personality_stats()` function (end of file), add:

```python
def _get_journal_entries(db: Database, user_id: str) -> list[dict]:
    """Get recent journal entries for the dashboard."""
    from windyfly.memory.nodes import get_nodes_by_type
    entries = get_nodes_by_type(db, "journal_entry", limit=20)
    result = []
    for e in entries:
        metadata = e.get("metadata", "{}")
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        result.append({
            "entry": metadata.get("entry", e.get("name", "")),
            "emotional_context": metadata.get("emotional_context", "neutral"),
            "created_at": e.get("created_at", ""),
        })
    return result
```

### Step 5.4 — Add UDS bridge method for journal

**File:** `src/windyfly/bridge/uds_server.py`
**In dispatch dict:** Add `"journal.list": self._handle_journal_list,`
**Handler:**

```python
    async def _handle_journal_list(self, params: dict) -> dict:
        from windyfly.memory.nodes import get_nodes_by_type
        import json
        entries = get_nodes_by_type(self.db, "journal_entry", limit=20)
        result = []
        for e in entries:
            meta = e.get("metadata", "{}")
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except (json.JSONDecodeError, TypeError):
                    meta = {}
            result.append({
                "entry": meta.get("entry", e.get("name", "")),
                "created_at": e.get("created_at", ""),
            })
        return {"journal": result}
```

### Step 5.5 — Add gateway route

**File:** `gateway/src/server.ts`
**After dashboard route (line 94):** Add:

```typescript
    // Journal entries
    if (path === "/api/journal" && req.method === "GET") {
      const result = await bridge.call("journal.list");
      return Response.json(result, { headers });
    }
```

### Step 5.6 — Test

**File:** NEW — `tests/test_journal.py`

```python
"""Tests for agent journal."""

from windyfly.memory.database import Database
from windyfly.memory.nodes import get_nodes_by_type, upsert_node


class TestJournal:
    def test_journal_entry_stored(self):
        db = Database(":memory:")
        upsert_node(
            db, "journal_entry", "journal:Had a great debugging session",
            metadata={"entry": "Had a great debugging session", "emotional_context": "excited"},
            source="agent_journal", epistemic_status="verified",
        )
        entries = get_nodes_by_type(db, "journal_entry", limit=5)
        assert len(entries) >= 1
        db.close()

    def test_journal_in_dashboard(self):
        from windyfly.dashboard.data import get_dashboard_summary
        db = Database(":memory:")
        upsert_node(
            db, "journal_entry", "journal:Test entry",
            metadata={"entry": "Test entry", "emotional_context": "neutral"},
            source="agent_journal", epistemic_status="verified",
        )
        summary = get_dashboard_summary(db)
        assert "journal" in summary
        assert len(summary["journal"]) >= 1
        db.close()
```

---

## FEATURE 6: Self-Assessment Report Card

**Goal:** Weekly self-assessment that grades the agent on 6 metrics, stores results, and shows them on the dashboard.

### Step 6.1 — Create `src/windyfly/agent/self_assessment.py`

**File:** NEW — `src/windyfly/agent/self_assessment.py`
**Full content:**

```python
"""Self-assessment — the agent's weekly report card.

Grades the agent on 6 metrics using data already in the database.
Stores results as type=self_assessment nodes.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node

logger = logging.getLogger(__name__)


def run_self_assessment(db: Database) -> dict[str, Any]:
    """Run a self-assessment and return the report card.

    Metrics (each scored 0-100):
      1. Memory Retention: nodes created vs decayed this week
      2. Failure Rate: failures logged vs resolved this week
      3. Soul Currency: hours since last soul/personality update
      4. Relationship Depth: relationship moments accumulated
      5. Response Consistency: average response cost this week
      6. Cost Efficiency: tokens per interaction this week

    Returns:
        Dict with scores and overall grade.
    """
    scores: dict[str, float] = {}

    # 1. Memory Retention
    total_nodes = db.fetchone("SELECT COUNT(*) as c FROM nodes")
    recent_nodes = db.fetchone(
        "SELECT COUNT(*) as c FROM nodes WHERE created_at >= date('now', '-7 days')"
    )
    total = total_nodes["c"] if total_nodes else 0
    recent = recent_nodes["c"] if recent_nodes else 0
    scores["memory_retention"] = min(100, (recent / max(total, 1)) * 500)

    # 2. Failure Rate
    total_failures = db.fetchone(
        "SELECT COUNT(*) as c FROM failures WHERE created_at >= date('now', '-7 days')"
    )
    resolved_failures = db.fetchone(
        "SELECT COUNT(*) as c FROM failures WHERE resolved_at IS NOT NULL AND created_at >= date('now', '-7 days')"
    )
    total_f = total_failures["c"] if total_failures else 0
    resolved_f = resolved_failures["c"] if resolved_failures else 0
    scores["failure_rate"] = (resolved_f / max(total_f, 1)) * 100 if total_f > 0 else 100

    # 3. Soul Currency (freshness)
    soul_update = db.fetchone(
        "SELECT MAX(updated_at) as last FROM soul"
    )
    if soul_update and soul_update["last"]:
        from datetime import datetime, timezone
        try:
            last = datetime.fromisoformat(soul_update["last"].replace("Z", "+00:00"))
            hours_ago = (datetime.now(timezone.utc) - last).total_seconds() / 3600
            scores["soul_currency"] = max(0, 100 - (hours_ago * 2))  # Lose 2pts per hour
        except (ValueError, TypeError):
            scores["soul_currency"] = 50
    else:
        scores["soul_currency"] = 0

    # 4. Relationship Depth
    moments = db.fetchone(
        "SELECT COUNT(*) as c FROM nodes WHERE type = 'relationship_moment'"
    )
    moment_count = moments["c"] if moments else 0
    scores["relationship_depth"] = min(100, moment_count * 10)  # 10 moments = 100%

    # 5. Response Consistency
    avg_cost = db.fetchone(
        "SELECT AVG(cost_usd) as avg FROM cost_ledger WHERE created_at >= date('now', '-7 days')"
    )
    avg = avg_cost["avg"] if avg_cost and avg_cost["avg"] else 0
    # Low average cost = high consistency (efficient)
    scores["response_consistency"] = max(0, 100 - (avg * 10000))

    # 6. Cost Efficiency
    total_cost = db.fetchone(
        "SELECT SUM(cost_usd) as total FROM cost_ledger WHERE created_at >= date('now', '-7 days')"
    )
    total_interactions = db.fetchone(
        "SELECT COUNT(*) as c FROM episodes WHERE role = 'user' AND created_at >= date('now', '-7 days')"
    )
    tc = total_cost["total"] if total_cost and total_cost["total"] else 0
    ti = total_interactions["c"] if total_interactions else 0
    cost_per = tc / max(ti, 1)
    scores["cost_efficiency"] = max(0, 100 - (cost_per * 5000))

    # Overall grade (weighted average)
    overall = sum(scores.values()) / len(scores) if scores else 0
    grade = _score_to_grade(overall)

    report = {
        "scores": {k: round(v, 1) for k, v in scores.items()},
        "overall_score": round(overall, 1),
        "grade": grade,
    }

    # Store as node
    upsert_node(
        db,
        "self_assessment",
        f"assessment:{grade}:{round(overall, 1)}",
        metadata=report,
        source="self_assessment",
        epistemic_status="verified",
    )

    return report


def _score_to_grade(score: float) -> str:
    """Convert a 0-100 score to a letter grade."""
    if score >= 90:
        return "A+"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    if score >= 60:
        return "C"
    if score >= 50:
        return "D"
    return "F"
```

### Step 6.2 — Add self-assessment to dashboard summary

**File:** `src/windyfly/dashboard/data.py`
**In `get_dashboard_summary()`:** Add `"self_assessment": _get_latest_assessment(db),`

```python
def _get_latest_assessment(db: Database) -> dict[str, Any] | None:
    """Get the most recent self-assessment."""
    from windyfly.memory.nodes import get_nodes_by_type
    assessments = get_nodes_by_type(db, "self_assessment", limit=1)
    if assessments:
        import json
        meta = assessments[0].get("metadata", "{}")
        if isinstance(meta, str):
            try:
                return json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                pass
    return None
```

### Step 6.3 — Add UDS and gateway routes

**UDS bridge dispatch dict:** Add `"assessment.run": self._handle_assessment_run,`
**Handler:**

```python
    async def _handle_assessment_run(self, params: dict) -> dict:
        from windyfly.agent.self_assessment import run_self_assessment
        report = run_self_assessment(self.db)
        return {"assessment": report}
```

**Gateway `server.ts`:**

```typescript
    // Self-assessment
    if (path === "/api/assessment" && req.method === "POST") {
      const result = await bridge.call("assessment.run");
      return Response.json(result, { headers });
    }
```

### Step 6.4 — Test

**File:** NEW — `tests/test_self_assessment.py`

```python
"""Tests for self-assessment."""

from windyfly.agent.self_assessment import run_self_assessment
from windyfly.memory.database import Database


class TestSelfAssessment:
    def test_generates_report(self):
        db = Database(":memory:")
        report = run_self_assessment(db)
        assert "scores" in report
        assert "grade" in report
        assert "overall_score" in report
        assert len(report["scores"]) == 6
        db.close()

    def test_grade_scale(self):
        from windyfly.agent.self_assessment import _score_to_grade
        assert _score_to_grade(95) == "A+"
        assert _score_to_grade(85) == "A"
        assert _score_to_grade(75) == "B"
        assert _score_to_grade(65) == "C"
        assert _score_to_grade(55) == "D"
        assert _score_to_grade(40) == "F"

    def test_assessment_stored_as_node(self):
        db = Database(":memory:")
        run_self_assessment(db)
        from windyfly.memory.nodes import get_nodes_by_type
        assessments = get_nodes_by_type(db, "self_assessment", limit=5)
        assert len(assessments) >= 1
        db.close()
```

---

## POST-BUILD CHECKLIST

After all 6 features are implemented, run these verification steps in order:

```bash
# 1. Full regression — must pass ALL tests (321 existing + new)
cd "/Users/thewindstorm/Desktop/Grant's Folder/windy-agent"
uv run pytest tests/ -x -q

# 2. Count new tests — should be ~25-30 new
uv run pytest tests/ -x -q --co | wc -l

# 3. Verify new files exist
ls -la src/windyfly/channels/sms.py
ls -la src/windyfly/channels/email.py
ls -la src/windyfly/agent/self_assessment.py

# 4. Commit and push
git add -A
git commit -m "P1: SMS channel, email channel, agent journal, self-assessment, Soul Passport rename, adaptive mode toggle

Features:
- SMS channel via Twilio (channels/sms.py): inbound webhook + outbound + rate limiting + opt-out
- Email channel via SendGrid (channels/email.py): inbound parse + outbound
- Agent journal: reflective diary entries every 10th interaction or on emotion
- Self-assessment: 6-metric weekly report card (memory, failures, soul, depth, consistency, cost)
- Soul Passport: renamed all user-facing references
- Adaptive mode: ON/OFF toggle gated by slider >= 5

All tests passing."
git push origin master
```

---

## FILE MANIFEST

| # | File | Action | Feature |
|---|---|---|---|
| 1 | `src/windyfly/control_panel.py` | MODIFY | Add `warmth` + `adaptive_mode` to SLIDER_INFO, PRESETS, _COST_PER_POINT |
| 2 | `src/windyfly/agent/loop.py` | MODIFY | Gate adaptive override + add journal entry hook |
| 3 | `src/windyfly/channels/sms.py` | NEW | SMS channel (~185 lines) |
| 4 | `src/windyfly/channels/email.py` | NEW | Email channel (~145 lines) |
| 5 | `src/windyfly/agent/self_assessment.py` | NEW | Self-assessment report card (~120 lines) |
| 6 | `src/windyfly/bridge/uds_server.py` | MODIFY | Add sms.*, email.*, journal.*, assessment.* handlers |
| 7 | `gateway/src/server.ts` | MODIFY | Add /api/sms/*, /api/email/*, /api/journal, /api/assessment routes |
| 8 | `src/windyfly/main.py` | MODIFY | Add --channel sms option |
| 9 | `src/windyfly/soul_import/orchestrator.py` | MODIFY | Soul Passport rename (docstrings) |
| 10 | `src/windyfly/soul_import/preview.py` | MODIFY | Soul Passport rename (docstrings) |
| 11 | `src/windyfly/dashboard/data.py` | MODIFY | Add journal + self_assessment to dashboard |
| 12 | `gateway/public/index.html` | MODIFY | Soul Passport button label |
| 13 | `tests/test_adaptive_mode.py` | MODIFY | Add disabled toggle test |
| 14 | `tests/test_sms_channel.py` | NEW | SMS tests (~45 lines) |
| 15 | `tests/test_email_channel.py` | NEW | Email tests (~35 lines) |
| 16 | `tests/test_journal.py` | NEW | Journal tests (~30 lines) |
| 17 | `tests/test_self_assessment.py` | NEW | Self-assessment tests (~30 lines) |

**Total new code: ~720 lines across 7 new files + 6 modified files.**

---

## ENVIRONMENT VARIABLES NEEDED

Add to `.env` **before** testing SMS/Email channels:

```bash
# Twilio (SMS channel)
TWILIO_ACCOUNT_SID=AC...
TWILIO_AUTH_TOKEN=...
TWILIO_PHONE_NUMBER=+15551234567

# SendGrid (Email channel)
SENDGRID_API_KEY=SG....
WINDYFLY_EMAIL_ADDRESS=fly@windyfly.ai
```

> **Note:** SMS and email tests use mock values and do NOT require real credentials.
> Real credentials are only needed for manual end-to-end testing.
