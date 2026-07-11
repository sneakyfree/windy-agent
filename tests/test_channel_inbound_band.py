"""[C1] Inbound SMS + email must RESOLVE the sender's trust band, not default to
Band.OWNER. An unknown sender maps to SANDBOX (no owner toolset: shell/fs/ssh/
fleet/send-as-owner), same as the matrix/telegram channels already do.

These assert the WIRING — the channel calls resolve_band(platform, sender,
config) and threads the result into agent_respond as band= — without depending
on resolve_band's TOFU/binding internals (covered by test_sender_identity).
"""

import os
from unittest.mock import patch

from windyfly.agent.capabilities import Band
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue


def _cfg():
    return {
        "agent": {"default_model": "gpt-4o-mini"},
        "personality": {},
        "costs": {"daily_budget_usd": 5.0},
    }


def test_sms_inbound_threads_resolved_band():
    os.environ["TWILIO_ACCOUNT_SID"] = "ACtest123"
    os.environ["TWILIO_AUTH_TOKEN"] = "test_token"
    os.environ["TWILIO_PHONE_NUMBER"] = "+15551234567"
    from windyfly.channels.sms import WindyFlySMS

    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    sms = WindyFlySMS(_cfg(), db, wq)
    with patch("windyfly.channels.sms.resolve_band", return_value=Band.SANDBOX) as rb, \
         patch("windyfly.channels.sms.agent_respond", return_value="ok") as ar:
        sms.handle_inbound("+15559999999", "hello")
    rb.assert_called_once_with("sms", "+15559999999", config=sms.config)
    assert ar.call_args.kwargs.get("band") == Band.SANDBOX, "sms must thread the resolved band"
    wq.stop()
    db.close()


def test_email_inbound_threads_resolved_band():
    os.environ["SENDGRID_API_KEY"] = "SG.test"
    from windyfly.channels.email import WindyFlyEmail

    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    email = WindyFlyEmail(_cfg(), db, wq)
    with patch("windyfly.channels.email.resolve_band", return_value=Band.SANDBOX) as rb, \
         patch("windyfly.channels.email.agent_respond", return_value="ok") as ar:
        email.handle_inbound("mallory@evil.com", "hi", "body")
    rb.assert_called_once_with("email", "mallory@evil.com", config=email.config)
    assert ar.call_args.kwargs.get("band") == Band.SANDBOX, "email must thread the resolved band"
    wq.stop()
    db.close()
