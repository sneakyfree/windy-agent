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

    def test_opt_out_keywords_ignore_case_and_spaces(self):
        # Windy Legal, LEGAL DECISION 28: the carrier-standard opt-out words, case- and space-insensitive.
        from windyfly.channels.sms import _is_opt_out
        for body in ("STOP", " stop ", "StopAll", "STOP ALL", "unsubscribe", "Cancel", "END", "quit", "Revoke",
                     "OPTOUT", "opt out", "Opt  Out", "\tOPT\nOUT "):
            assert _is_opt_out(body), body
        for body in ("Stop.", "STOP!", "stop!!", "Opt-Out", "OPT-OUT?", "'STOP'", "STOP 🙏"):
            assert _is_opt_out(body), body
        for body in ("", "stop texting me so late", "the end", "start", "help", "1234", "..."):
            assert not _is_opt_out(body), body

    def test_new_keyword_gets_the_unsubscribe_reply(self):
        sms, db, wq = self._make_sms()
        with patch("windyfly.channels.sms.agent_respond") as agent:
            response = sms.handle_inbound("+15559999999", "opt out")
        assert "unsubscribed" in response.lower() and not agent.called
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
        with patch("windyfly.channels.sms.agent_respond", return_value="Hi!"), \
             patch.dict(os.environ, {"WINDY_SMS_ALLOW_NON_OWNER": "1"}):
            sms.handle_inbound("+15559999999", "Hello")
        import time
        time.sleep(0.3)
        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'contact'")
        assert len(nodes) >= 1
        wq.stop()
        db.close()
