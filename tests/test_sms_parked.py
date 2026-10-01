"""SMS / phone numbers are parked: nothing sends or buys unless WINDY_ENABLE_SMS=1."""
import asyncio

import pytest

from windyfly import hatch_actions, phone_provision


@pytest.fixture(autouse=True)
def _parked(monkeypatch):
    monkeypatch.delenv("WINDY_ENABLE_SMS", raising=False)
    # Live-looking Twilio creds must still do nothing while parked.
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "x")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")


def test_birth_sms_is_parked_even_with_twilio_creds():
    out = asyncio.run(hatch_actions.send_hatch_sms(owner_phone="+15551230000", agent_name="Bug"))
    assert out["status"] == "parked"


def test_phone_provision_never_buys_or_mocks():
    out = asyncio.run(phone_provision.provision_phone("ET26-TEST-AAAA", "Bug"))
    assert out.success is False and "parked" in out.error


def test_sms_channel_refuses_to_start():
    from windyfly.channels.sms import WindyFlySMS

    with pytest.raises(RuntimeError, match="parked"):
        WindyFlySMS({}, None, None)


def test_enabled_flag_restores_old_behavior(monkeypatch):
    monkeypatch.setenv("WINDY_ENABLE_SMS", "1")
    monkeypatch.delenv("TWILIO_ACCOUNT_SID")
    monkeypatch.delenv("TWILIO_AUTH_TOKEN")
    out = asyncio.run(hatch_actions.send_hatch_sms(owner_phone="+15551230000", agent_name="Bug"))
    assert out["status"] == "mock_sent"
