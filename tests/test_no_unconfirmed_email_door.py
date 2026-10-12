"""No door sends email around the owner's confirmation (Hub 10-12).

The gateway's POST /api/email/send called the bridge's email.send, which sent through
_send_email_now directly: WINDY_SEND_CONFIRM and the known-recipient rule never applied. No
dashboard page, sister repo or test client called it, so both were removed. The model's mail
tool (tools/mail.send_email) is the one way to send, and it holds a draft when confirm is on.
"""

from __future__ import annotations

from pathlib import Path

from tests.test_gap_closure import _make_bridge

ROOT = Path(__file__).resolve().parents[1]


def test_the_gateway_has_no_email_send_route():
    server = (ROOT / "gateway" / "src" / "server.ts").read_text(encoding="utf-8")
    assert "/api/email/send" not in server
    assert 'bridge.call("email.send"' not in server


def test_the_bridge_has_no_email_send_method():
    bridge, db, _ = _make_bridge()
    try:
        methods = bridge._handler_table()
    finally:
        db.close()
    assert "email.send" not in methods
    assert "email.inbound" in methods  # inbound mail still reaches the agent
