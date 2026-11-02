"""ONE owner-confirm gate for outward actions (windyfly.agent.owner_confirm, the lean Windy Fly, 2026-11-02).

1. Registry walk: every registered tool and every EXTERNAL_EFFECT capability is classified in owner_confirm, so a
   new outward tool that skips the gate fails here.
2. The gate tools really go through the gate, with Boss's rules unchanged: ON by default (only the exact lab switch
   "0" turns it off); one approved recipient goes straight out; a new recipient, or cc/bcc, asks; only the OWNER's
   own yes approves.
"""

from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

import pytest

from windyfly.agent import owner_confirm
from windyfly.agent.capabilities import Band, Tier

MOBILE_RIG = "rig@probe.windyword.ai"   # a NEW recipient, like Mobile's lab rig address


# ── 1. registry walk ────────────────────────────────────────────


def _everything_registered(monkeypatch):
    """Boot the real registration sequence with every dark feature switched on and every boot probe answering."""
    for k, v in {"WINDY_PHONE_TOOLS": "1", "WINDY_ENABLE_SMS": "1", "WINDY_TEAMS": "1",
                 "WINDY_CALENDAR_ADD_EVENT": "1"}.items():
        monkeypatch.setenv(k, v)
    from windyfly.agent import windy_calendar as wc
    from windyfly.agent.capabilities import vault as vcap
    from windyfly.agent.capabilities import windy_calendar as wcap

    monkeypatch.setattr(wc, "enabled", lambda: True)
    monkeypatch.setattr(wc, "invoke", lambda *a, **k: {"ok": True})
    monkeypatch.setattr(wcap, "add_event_enabled", lambda: True, raising=False)
    monkeypatch.setattr(vcap, "enabled", lambda: True)
    monkeypatch.setattr(vcap, "call_api_enabled", lambda: True)
    monkeypatch.setattr(vcap, "_client", lambda: MagicMock(VaultError=Exception))

    from windyfly.agent.boot import BootContext, BootSequence, default_capability_registration_sequence
    from windyfly.agent.capabilities.registry import CapabilityRegistry
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

    tools, caps = ToolRegistry(), CapabilityRegistry()
    with tempfile.TemporaryDirectory() as td:
        BootSequence(default_capability_registration_sequence()).run(BootContext(
            config={}, db=Database(td + "/t.db"), write_queue=WriteQueue(),
            tool_registry=tools, capability_registry=caps))
    return set(tools._tools), list(caps._capabilities.values())


def test_every_registered_tool_is_classified_outward_or_not(monkeypatch):
    tools, _caps = _everything_registered(monkeypatch)
    assert "send_email" in tools and "send_sms" in tools   # the walk really saw the outward tools
    unknown = tools - set(owner_confirm.OUTWARD) - owner_confirm.NOT_OUTWARD
    assert not unknown, f"classify these in windyfly.agent.owner_confirm (OUTWARD or NOT_OUTWARD): {sorted(unknown)}"


def test_every_external_effect_capability_is_in_outward(monkeypatch):
    _tools, caps = _everything_registered(monkeypatch)
    external = {c.id for c in caps if c.tier == Tier.EXTERNAL_EFFECT}
    assert {"phone_sms_compose", "vault.offer_save", "windy_calendar.book"} <= external
    missing = external - set(owner_confirm.OUTWARD)
    assert not missing, f"outward capabilities with no owner-confirm rule: {sorted(missing)}"


def test_every_outward_rule_is_a_known_kind():
    kinds = {owner_confirm.GATE, owner_confirm.ON_PHONE, owner_confirm.IN_VAULT, owner_confirm.OWNER_ASKED,
             owner_confirm.NOT_YET}
    assert set(owner_confirm.OUTWARD.values()) <= kinds
    assert not set(owner_confirm.OUTWARD) & owner_confirm.NOT_OUTWARD


def test_owner_asked_capabilities_are_owner_band_only(monkeypatch):
    _tools, caps = _everything_registered(monkeypatch)
    for c in caps:
        if owner_confirm.OUTWARD.get(c.id) in (owner_confirm.OWNER_ASKED, owner_confirm.ON_PHONE,
                                               owner_confirm.IN_VAULT):
            assert c.band_required == Band.OWNER, f"{c.id} must be OWNER band only"


# ── 2. the rule ─────────────────────────────────────────────────


def test_on_by_default_and_only_the_exact_lab_switch_turns_it_off(monkeypatch):
    monkeypatch.delenv(owner_confirm.LAB_SWITCH, raising=False)
    assert owner_confirm.enabled()
    for v in ("", "1", "false", "off", "no", " 0x"):
        monkeypatch.setenv(owner_confirm.LAB_SWITCH, v)
        assert owner_confirm.enabled(), repr(v)
    for v in ("0", " 0 "):
        monkeypatch.setenv(owner_confirm.LAB_SWITCH, v)
        assert not owner_confirm.enabled()


def test_boss_rule_4(monkeypatch):
    monkeypatch.delenv(owner_confirm.LAB_SWITCH, raising=False)
    known = {"ann@x.com"}
    assert not owner_confirm.needs_owner_yes(["ANN@x.com"], known)
    assert owner_confirm.needs_owner_yes([MOBILE_RIG], known)
    assert owner_confirm.needs_owner_yes(["ann@x.com", "bob@x.com"], known | {"bob@x.com"})
    assert owner_confirm.needs_owner_yes(["ann@x.com"], known, bulk=True)
    assert owner_confirm.needs_owner_yes([], known)


def test_the_lab_switch_skips_the_ask_only_where_it_always_did(monkeypatch):
    monkeypatch.setenv(owner_confirm.LAB_SWITCH, "0")
    assert not owner_confirm.needs_owner_yes([MOBILE_RIG], set())
    assert owner_confirm.needs_owner_yes(["+15551234567"], set(), switchable=False)


@pytest.mark.parametrize("band,ok", [(Band.OWNER, True), (Band.TRUSTED, False), (Band.USER, False),
                                     (Band.SANDBOX, False), (None, False), ("x", False)])
def test_only_the_owner_may_approve(band, ok):
    assert owner_confirm.owner_may_approve(band) is ok


# ── 3. the gate tools go through the gate ───────────────────────


@pytest.fixture
def mailbox(monkeypatch, tmp_path):
    from windyfly.tools import mail

    monkeypatch.delenv(owner_confirm.LAB_SWITCH, raising=False)
    monkeypatch.delenv("WINDY_OWNER_EMAILS", raising=False)
    monkeypatch.setattr(mail, "_known_path", lambda: tmp_path / "known_recipients.json")
    adapter = MagicMock()
    adapter.send_email.return_value = {"status": "sent", "message_id": "m"}
    monkeypatch.setattr(mail, "_adapter", lambda: adapter)
    mail.cancel_pending()
    yield mail, adapter
    mail.cancel_pending()


def test_mail_to_a_new_recipient_is_held_for_the_owner(mailbox):
    mail, adapter = mailbox
    r = mail.send_email(MOBILE_RIG, "lab", "hello")
    assert r["status"] == "pending_owner_approval"
    adapter.send_email.assert_not_called()


def test_mail_to_an_approved_recipient_goes_straight_out(mailbox):
    mail, adapter = mailbox
    mail._remember_recipients(["ann@x.com"])
    assert mail.send_email("ann@x.com", "s", "b")["status"] == "sent"
    assert mail.send_email("ann@x.com", "s", "b", cc="ann2@x.com")["status"] == "pending_owner_approval"


def test_mail_asks_through_the_one_gate(mailbox, monkeypatch):
    mail, adapter = mailbox
    seen = []
    monkeypatch.setattr(owner_confirm, "needs_owner_yes", lambda *a, **k: seen.append((a, k)) or True)
    mail._remember_recipients(["ann@x.com"])
    assert mail.send_email("ann@x.com", "s", "b")["status"] == "pending_owner_approval"
    assert seen and seen[0][0][0] == ["ann@x.com"]


def test_sms_first_text_asks_through_the_one_gate_even_with_the_lab_switch(monkeypatch):
    from windyfly.tools import sms

    monkeypatch.setenv(owner_confirm.LAB_SWITCH, "0")
    monkeypatch.delenv("WINDY_TEXT_BYO", raising=False)
    monkeypatch.setattr(sms, "byo_enabled", lambda: False)
    monkeypatch.setattr(sms, "_windy_text_env", lambda: ("https://text.test", "ept"))
    monkeypatch.setattr(sms, "_db", None)
    monkeypatch.setattr(sms, "_approved_mem", set())
    delivered = []
    monkeypatch.setattr(sms, "_deliver", lambda *a: delivered.append(a) or {"status": "sent"})
    r = sms.send_sms("+15551234567", "hi")
    assert r["status"] == "confirm_required" and not delivered
    sms._approve("+15551234567")
    assert sms.send_sms("+15551234567", "again")["status"] == "sent"
    seen = []
    monkeypatch.setattr(owner_confirm, "needs_owner_yes", lambda *a, **k: seen.append(k) or True)
    assert sms.send_sms("+15551234567", "x")["status"] == "confirm_required"
    assert seen == [{"switchable": False}]


@pytest.mark.asyncio
async def test_a_strangers_send_never_approves_the_owners_draft(mailbox, monkeypatch):
    from windyfly.channels import base

    mail, adapter = mailbox
    mail.send_email(MOBILE_RIG, "lab", "hello")
    monkeypatch.setattr("windyfly.channels.identity.resolve_band", lambda *a, **k: Band.USER)
    handled, _reply = await base.handle_incoming("send", {"platform": "matrix", "sender_id": "@x:y"})
    assert not handled
    adapter.send_email.assert_not_called()
    assert mail.pending_drafts()

    monkeypatch.setattr("windyfly.channels.identity.resolve_band", lambda *a, **k: Band.OWNER)
    handled, reply = await base.handle_incoming("send", {"platform": "matrix", "sender_id": "@owner:y"})
    assert handled and reply == f"Sent to {MOBILE_RIG}, with your approval."
    adapter.send_email.assert_called_once()
