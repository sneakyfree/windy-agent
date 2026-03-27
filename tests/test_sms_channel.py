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
