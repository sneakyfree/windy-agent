"""ONE ledger for outward tools (lean R4's safety half, built ahead of the cut; plan file section 4d).

Capability calls wrote agent_actions rows; the older ToolRegistry's outward tools (send_email, send_sms,
send_chat_message, make_call, upload_to_cloud, the builder, domains) wrote none, and neither did the owner's own
"send" of a held mail. Now each writes ONE row, ``tool.<name>``, with argument NAMES only: a send's values are a
recipient and a message, a real person's data, and never enter the ledger. Error text never enters either.
"""

from __future__ import annotations

import inspect
import json
import tempfile
import time
from pathlib import Path

import pytest

from windyfly.agent.capabilities import Band
from windyfly.agent.capabilities import audit
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.bands import turn_band
from windyfly.tools.registry import ToolRegistry

SECRET_TO = "someone@example.com"
SECRET_BODY = "the words of a private message"


@pytest.fixture
def ledger(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        db = Database(str(Path(tmpdir) / "t.db"))
        wq = WriteQueue()
        wq.start()
        monkeypatch.setattr(audit, "_TOOL_LEDGER", None)
        audit.install_tool_ledger(db, wq)
        try:
            yield db, wq
        finally:
            wq.stop()
            db.close()


def _rows(db, wq):
    start = time.time()
    while not wq._queue.empty():
        assert time.time() - start < 30, "write queue did not drain"
        time.sleep(0.05)
    time.sleep(0.1)
    return [dict(r) for r in db.fetchall("SELECT * FROM agent_actions ORDER BY started_at")]


def _registry(**fns):
    reg = ToolRegistry()
    for name, fn in fns.items():
        reg.register(name, name, {"type": "object", "properties": {}}, fn)
    return reg


def _no_values_anywhere(row):
    text = json.dumps(row, default=str)
    assert SECRET_TO not in text and SECRET_BODY not in text


def test_an_outward_tool_writes_one_row_with_argument_names_only(ledger):
    db, wq = ledger
    reg = _registry(send_email=lambda to, subject, body: {"status": "pending_owner_approval"})
    with turn_band(Band.OWNER):
        reg.execute("send_email", {"to": SECRET_TO, "subject": "hi", "body": SECRET_BODY})
    rows = _rows(db, wq)
    assert len(rows) == 1
    row = rows[0]
    assert row["capability_id"] == "tool.send_email"
    assert row["tier"] == 4 and row["band"] == "OWNER" and row["success"] == 1
    assert json.loads(row["args_json"]) == {"arg_names": ["body", "subject", "to"]}
    assert row["ended_at"] and row["error_class"] is None and row["error_message"] is None
    _no_values_anywhere(row)


def test_a_tool_that_says_it_did_not_act_is_a_failure_with_a_short_id_and_no_text(ledger):
    db, wq = ledger
    reg = _registry(send_sms=lambda to, body: {"status": "failed", "error": f"could not text {SECRET_TO}: {SECRET_BODY}"})
    with turn_band(Band.OWNER):
        reg.execute("send_sms", {"to": SECRET_TO, "body": SECRET_BODY})
    (row,) = _rows(db, wq)
    assert row["success"] == 0 and row["error_class"] == "failed" and row["error_message"] is None
    _no_values_anywhere(row)


def test_a_tool_that_raises_is_a_failure_by_class_name_only(ledger):
    db, wq = ledger

    def boom(**_):
        raise RuntimeError(f"socket said {SECRET_TO}")

    reg = _registry(make_call=boom)
    with turn_band(Band.OWNER):
        out = json.loads(reg.execute("make_call", {"to": SECRET_TO}))
    assert "error" in out                                   # the loop still gets its error, as before
    (row,) = _rows(db, wq)
    assert row["success"] == 0 and row["error_class"] == "RuntimeError" and row["error_message"] is None
    _no_values_anywhere(row)


def test_a_read_tool_writes_no_row(ledger):
    db, wq = ledger
    reg = _registry(web_search=lambda query: {"results": []})
    with turn_band(Band.OWNER):
        reg.execute("web_search", {"query": "weather"})
    assert _rows(db, wq) == []


def test_a_refused_call_writes_no_row_and_does_not_run(ledger):
    db, wq = ledger
    ran = []
    reg = _registry(send_chat_message=lambda **k: ran.append(k))
    with turn_band(Band.USER):
        out = json.loads(reg.execute("send_chat_message", {"to": "x", "text": "y"}))
    assert out["error"].startswith("refused") and ran == []
    assert _rows(db, wq) == []


def test_with_no_ledger_installed_the_tool_still_runs(monkeypatch):
    monkeypatch.setattr(audit, "_TOOL_LEDGER", None)
    reg = _registry(upload_to_cloud=lambda path: {"status": "uploaded"})
    with turn_band(Band.OWNER):
        assert json.loads(reg.execute("upload_to_cloud", {"path": "a.txt"})) == {"status": "uploaded"}


def test_the_owner_s_send_of_a_held_mail_writes_its_own_row(ledger):
    db, wq = ledger
    res = audit.audited_call("send_email", {}, lambda: {"status": "sent", "to": SECRET_TO},
                             band=Band.OWNER, via="owner_send")
    assert res["status"] == "sent"
    (row,) = _rows(db, wq)
    assert row["capability_id"] == "tool.send_email" and row["success"] == 1
    assert json.loads(row["args_json"]) == {"arg_names": [], "via": "owner_send"}
    _no_values_anywhere(row)


def test_the_channel_routes_the_owner_s_send_through_the_ledger():
    from windyfly.channels import base

    src = inspect.getsource(base)
    assert 'audited_call("send_email", {}, lambda: _mail.approve_latest(who), band=band, via="owner_send")' in src


def test_boot_installs_the_tool_ledger_with_the_audit_hooks():
    from windyfly.agent import boot

    src = inspect.getsource(boot._step_install_audit_hooks)
    assert "install_tool_ledger(ctx.db, ctx.write_queue)" in src


def test_every_outward_tool_class_is_routed_through_the_ledger():
    from windyfly.tools import bands

    outward = {n for n, c in bands.TOOL_CLASS.items() if c == "outward"}
    assert {"send_email", "send_sms", "confirm_sms", "send_chat_message", "make_call", "upload_to_cloud",
            "buy_domain", "confirm_purchase", "windycodeweb_publish"} <= outward
    assert 'TOOL_CLASS.get(name) == "outward"' in inspect.getsource(ToolRegistry.execute)


def test_the_owner_s_yes_to_a_held_first_text_writes_one_row(ledger, monkeypatch):
    from windyfly.tools import sms

    db, wq = ledger
    monkeypatch.setattr(sms, "byo_enabled", lambda: True)
    monkeypatch.setattr(sms, "_pending", {"tok": {"expires": time.time() + 60}})
    monkeypatch.setattr(sms, "confirm_sms", lambda token: {"status": "sent", "to": SECRET_TO})
    assert sms.owner_reply("yes").startswith("Texted")
    (row,) = _rows(db, wq)
    assert row["capability_id"] == "tool.confirm_sms" and row["band"] == "OWNER" and row["success"] == 1
    assert json.loads(row["args_json"]) == {"arg_names": [], "via": "owner_yes"}
    _no_values_anywhere(row)


def test_the_owner_s_yes_to_a_held_publish_writes_one_row(ledger, monkeypatch):
    from windyfly.tools import windycode_web as wcw

    db, wq = ledger
    monkeypatch.setattr(wcw, "_HELD", {"publish": {"at": time.time(), "fly_tool": "windycodeweb_publish",
                                                    "args": {"project_id": "p1"}, "token": "t"}})
    monkeypatch.setattr(wcw, "_publish_gate", lambda: None)
    monkeypatch.setattr(wcw, "_call", lambda tool, args: {"status": "ok", "speak": "Published."})
    assert wcw.owner_confirm("yes, publish") == "Published."
    (row,) = _rows(db, wq)
    assert row["capability_id"] == "tool.windycodeweb_publish" and row["success"] == 1
    assert json.loads(row["args_json"]) == {"arg_names": ["project_id"], "via": "owner_yes"}
