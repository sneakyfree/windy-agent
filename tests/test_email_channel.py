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
