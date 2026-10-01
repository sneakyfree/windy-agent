"""SMS is parked: nothing sends unless WINDY_ENABLE_SMS=1.

(Birth SMS and phone-number provisioning were removed in 0.7.5 with the
terminal hatch — ADR-059, one hallway.)
"""
import pytest


@pytest.fixture(autouse=True)
def _parked(monkeypatch):
    monkeypatch.delenv("WINDY_ENABLE_SMS", raising=False)
    # Live-looking Twilio creds must still do nothing while parked.
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACx")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "x")
    monkeypatch.setenv("TWILIO_PHONE_NUMBER", "+15550000000")


def test_sms_channel_refuses_to_start():
    from windyfly.channels.sms import WindyFlySMS

    with pytest.raises(RuntimeError, match="parked"):
        WindyFlySMS({}, None, None)
