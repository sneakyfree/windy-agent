"""LEGAL DECISION 28 (Windy Legal, BOARD 2026-11-09T11:14:19Z, (4)(a)): a person who texts STOP to Windy Fly's direct
Twilio channel is never texted again from it until they text START. Every send path checks the record: the channel's
send_sms (UDS bridge "sms.send") and the /sms command."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from windyfly.channels import sms as sms_mod
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

PERSON = "+15559999999"


@pytest.fixture
def chan(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_SMS_ALLOW_NON_OWNER", "1")  # the opt-out record is tested on its own here
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "test_token")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")
    db = Database(str(tmp_path / "mem.db"))
    wq = WriteQueue()
    wq.start()
    config = {"agent": {"default_model": "gpt-4o-mini"}, "personality": {}, "costs": {"daily_budget_usd": 5.0}}
    yield sms_mod.WindyFlySMS(config, db, wq), db, tmp_path / "mem.db"
    wq.stop()
    db.close()


def _no_network():
    return patch("urllib.request.urlopen", side_effect=AssertionError("Twilio must not be called"))


class TestRecordAndRefuse:
    def test_stop_is_recorded_and_send_is_refused_without_calling_twilio(self, chan):
        sms, _, _ = chan
        assert "unsubscribed" in sms.handle_inbound(PERSON, "STOP").lower()
        assert sms.is_opted_out(PERSON)
        with _no_network():
            out = sms.send_sms(PERSON, "hello again")
        assert out == {"status": "refused", "error": "recipient_opted_out"}

    @pytest.mark.parametrize("spelling", ["5559999999", "15559999999", "+1 (555) 999-9999", "+1-555-999-9999"])
    def test_any_spelling_of_the_number_is_the_same_person(self, chan, spelling):
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "opt out")
        with _no_network():
            assert sms.send_sms(spelling, "x")["error"] == "recipient_opted_out"

    def test_an_opted_out_person_gets_no_reply_and_the_agent_is_not_asked(self, chan):
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        with patch("windyfly.channels.sms.agent_respond") as agent:
            assert sms.handle_inbound(PERSON, "are you still there?") == ""
        assert not agent.called

    def test_start_clears_it_with_no_reply_of_our_own(self, chan):
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        assert sms.handle_inbound(PERSON, " start ") == ""
        assert not sms.is_opted_out(PERSON)

    def test_unstop_clears_it(self, chan):
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        sms.handle_inbound(PERSON, "UNSTOP")
        assert not sms.is_opted_out(PERSON)

    def test_yes_never_re_subscribes(self, chan):
        # LEGAL DECISION 28 (4)(c): "yes" to anything must not undo a STOP.
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        with patch("windyfly.channels.sms.agent_respond") as agent:
            assert sms.handle_inbound(PERSON, "YES") == ""
        assert sms.is_opted_out(PERSON) and not agent.called

    def test_the_record_survives_a_new_channel_object(self, chan):
        sms, db, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        again = sms_mod.WindyFlySMS(sms.config, db, sms.write_queue)
        with _no_network():
            assert again.send_sms(PERSON, "x")["status"] == "refused"

    def test_someone_else_is_still_texted(self, chan):
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        assert not sms.is_opted_out("+15558888888")


class TestWebhookAndBridge:
    def test_webhook_sends_nothing_for_an_empty_reply(self, chan):
        sms, _, _ = chan
        sms.handle_inbound(PERSON, "STOP")
        handler = sms_mod.create_webhook_handler(sms)
        assert asyncio.run(handler({"From": PERSON, "Body": "hi"})) == "<Response></Response>"


class TestSlashCommandPath:
    def test_number_opted_out_reads_the_same_record(self, chan):
        sms, _, path = chan
        assert sms_mod.number_opted_out(PERSON, str(path)) is False
        sms.handle_inbound(PERSON, "STOP")
        assert sms_mod.number_opted_out("5559999999", str(path)) is True

    def test_no_database_yet_means_nothing_recorded(self, tmp_path):
        assert sms_mod.number_opted_out(PERSON, str(tmp_path / "none.db")) is False

    def test_an_unreadable_database_counts_as_opted_out(self, tmp_path):
        bad = tmp_path / "bad.db"
        bad.write_bytes(b"not a database" * 100)
        assert sms_mod.number_opted_out(PERSON, str(bad)) is True

    def _sms_cmd(self):
        from windyfly.commands import ecosystem
        from windyfly.commands.registry import registry

        ecosystem.init_ecosystem()
        return {c.name: c for c in registry.all()}["sms"].handler

    def test_slash_sms_refuses_an_opted_out_number(self, monkeypatch):
        monkeypatch.setenv("WINDY_SMS_ALLOW_NON_OWNER", "1")
        monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest123")
        monkeypatch.setenv("TWILIO_AUTH_TOKEN", "t")
        monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15551234567")
        with patch("windyfly.channels.sms.number_opted_out", return_value=True):
            out = asyncio.run(self._sms_cmd()({"_args": [PERSON, "hi"]}))
        assert out == "That number has opted out of texts (they replied STOP), so I didn't send it."

    def test_slash_sms_is_parked_like_every_other_sms_path(self, monkeypatch):
        monkeypatch.delenv("WINDY_ENABLE_SMS", raising=False)
        with patch("windyfly.channels.sms.number_opted_out") as check:
            out = asyncio.run(self._sms_cmd()({"_args": [PERSON, "hi"]}))
        assert out.startswith("Texting isn't available yet") and not check.called


class TestOwnerOnly:
    """Boss's opt-out order (BOARD 2026-11-09T11:18:02Z (1)(b)): owner only unless WINDY_SMS_ALLOW_NON_OWNER=1."""

    OWNER = "+15550001111"

    @pytest.fixture
    def owner_chan(self, chan, monkeypatch):
        monkeypatch.delenv("WINDY_SMS_ALLOW_NON_OWNER", raising=False)
        monkeypatch.setenv("WINDY_OWNER_IDS", f"sms:{self.OWNER}")
        return chan[0]

    def test_a_non_owner_is_refused_without_calling_twilio(self, owner_chan):
        with _no_network():
            assert owner_chan.send_sms(PERSON, "x") == {"status": "refused", "error": "not_owner"}

    def test_the_owner_is_texted(self, owner_chan):
        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b'{"sid": "SM1"}'

        with patch("urllib.request.urlopen", return_value=_Resp()):
            assert owner_chan.send_sms("5550001111", "x") == {"status": "sent", "message_sid": "SM1"}

    def test_a_non_owner_gets_no_reply_and_the_agent_is_not_asked(self, owner_chan):
        with patch("windyfly.channels.sms.agent_respond") as agent:
            assert owner_chan.handle_inbound(PERSON, "hello") == ""
        assert not agent.called

    def test_a_non_owner_still_gets_the_stop_confirmation(self, owner_chan):
        assert "unsubscribed" in owner_chan.handle_inbound(PERSON, "STOP").lower()

    def test_a_first_contact_binding_is_not_the_owner(self, owner_chan, monkeypatch):
        monkeypatch.setattr("windyfly.channels.identity._load_bindings", lambda: {"sms": {PERSON}})
        with _no_network():
            assert owner_chan.send_sms(PERSON, "x")["error"] == "not_owner"

    def test_config_owner_counts(self, owner_chan, monkeypatch):
        monkeypatch.delenv("WINDY_OWNER_IDS")
        assert sms_mod.may_text("+15550002222", {"trust": {"owner_ids": ["sms:+1 555 000 2222"]}})
        assert not sms_mod.may_text(PERSON, {"trust": {"owner_ids": ["sms:+1 555 000 2222"]}})

    def test_slash_sms_refuses_a_non_owner(self, monkeypatch):
        monkeypatch.delenv("WINDY_SMS_ALLOW_NON_OWNER", raising=False)
        monkeypatch.delenv("WINDY_OWNER_IDS", raising=False)
        from windyfly.commands import ecosystem
        from windyfly.commands.registry import registry

        ecosystem.init_ecosystem()
        cmd = {c.name: c for c in registry.all()}["sms"].handler
        with patch("windyfly.channels.sms.number_opted_out", return_value=False):
            out = asyncio.run(cmd({"_args": [PERSON, "hi"]}))
        assert out.startswith("Texting isn't available yet")
