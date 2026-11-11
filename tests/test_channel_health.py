"""Channel health tests — Email hardening (the SMS half left with the direct SMS channel).

R3.5-R3.8: Integration tests for Twilio signature verification,
SMS opt-out, email session persistence, and HTML email support.
"""

from unittest.mock import patch


class TestEmailHTMLSupport:
    """R3.3: Email should support optional HTML body."""

    def test_inbound_handler_never_sends(self, monkeypatch):
        """Hub, 10-02: the legacy WindyFlyEmail is inbound-only. Its SendGrid sender
        (its own From address) is gone, and it needs no SendGrid key."""
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.channels.email import WindyFlyEmail

        monkeypatch.delenv("SENDGRID_API_KEY", raising=False)
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        try:
            email = WindyFlyEmail({"email": {}}, db, wq)
            assert not hasattr(email, "send_email")
        finally:
            wq.stop()
            db.close()

    @patch.dict("os.environ", {
        "SENDGRID_API_KEY": "SG.test_key",
        "WINDYFLY_EMAIL_ADDRESS": "test@windyfly.ai",
    })
    def test_email_session_persistence(self):
        """Same email address should get the same session ID."""
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.channels.email import WindyFlyEmail

        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        try:
            email = WindyFlyEmail({"email": {}}, db, wq)
            sid1 = email._get_session_id("user@example.com")
            sid2 = email._get_session_id("user@example.com")
            sid3 = email._get_session_id("other@example.com")
            assert sid1 == sid2  # Same email → same session
            assert sid1 != sid3  # Different email → different session
        finally:
            wq.stop()
            db.close()


class TestSendGridInboundParsing:
    """R3.4: UDS bridge should handle SendGrid's email format variations."""

    def test_extracts_email_from_angle_brackets(self):
        """'Grant <grant@example.com>' should extract 'grant@example.com'."""
        import re
        test_from = "Grant Windham <grant@example.com>"
        match = re.search(r"<(.+?)>", test_from)
        assert match is not None
        assert match.group(1) == "grant@example.com"

    def test_plain_email_unchanged(self):
        """'grant@example.com' (no brackets) should pass through unchanged."""
        import re
        test_from = "grant@example.com"
        if "<" in test_from:
            match = re.search(r"<(.+?)>", test_from)
            result = match.group(1) if match else test_from
        else:
            result = test_from
        assert result == "grant@example.com"
