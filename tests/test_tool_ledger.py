"""ONE ledger (lean Windy Fly, 2026-11-02): the legacy ToolRegistry tools (mail, chat, texting, cloud, search,
plans) land in agent_actions exactly like capabilities, with argument NAMES only, never values."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path

import pytest

from windyfly.agent.capabilities import CapabilityRegistry, install_audit_hooks
from windyfly.agent.capabilities.audit import audited_tool_call
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry


@pytest.fixture(autouse=True)
def _owner_turn():
    from windyfly.agent.capabilities import Band
    from windyfly.tools.bands import turn_band

    with turn_band(Band.OWNER):
        yield


@pytest.fixture
def ledger():
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "t.db"))
        wq = WriteQueue()
        wq.start()
        install_audit_hooks(CapabilityRegistry(), db, wq, session_id_provider=lambda: "matrix:!r:v1")
        try:
            yield db, wq
        finally:
            wq.stop()
            db.close()


def _rows(db, wq):
    start = time.time()
    while not wq._queue.empty() and time.time() - start < 30:
        time.sleep(0.05)
    time.sleep(0.2)
    return db.fetchall("SELECT * FROM agent_actions ORDER BY started_at")


def _registry():
    reg = ToolRegistry()
    reg.register("send_email", "send", {"type": "object", "properties": {}},
                 lambda to, body: json.dumps({"sent": True}))
    reg.register("broken", "x", {"type": "object", "properties": {}},
                 lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    return reg


def test_a_legacy_tool_call_lands_in_the_ledger_with_arg_names_only(ledger):
    db, wq = ledger
    reg = _registry()
    out = audited_tool_call("send_email", {"to": "x@windymail.ai", "body": "my secret plans"}, None,
                            lambda: reg.execute("send_email", {"to": "x@windymail.ai", "body": "my secret plans"}))
    assert json.loads(out) == {"sent": True}
    (row,) = _rows(db, wq)
    assert row["capability_id"] == "tool.send_email"
    assert row["success"] == 1 and row["session_id"] == "matrix:!r:v1"
    assert json.loads(row["args_json"]) == {"arg_names": ["body", "to"]}
    assert "secret" not in row["args_json"] and "windymail" not in row["args_json"]


def test_a_tool_that_answers_an_error_is_recorded_as_failed(ledger):
    db, wq = ledger
    reg = _registry()
    out = audited_tool_call("broken", {}, None, lambda: reg.execute("broken", {}))
    assert "boom" in out
    (row,) = _rows(db, wq)
    assert row["success"] == 0 and row["error_class"] == "ToolError"


def test_a_tool_that_raises_is_recorded_and_raised_again(ledger):
    db, wq = ledger

    def run():
        raise KeyError("Unknown tool: nope")

    with pytest.raises(KeyError):
        audited_tool_call("nope", "{}", None, run)
    (row,) = _rows(db, wq)
    assert row["success"] == 0 and row["error_class"] == "KeyError"


def test_without_a_ledger_the_tool_just_runs():
    assert audited_tool_call("x", {}, None, lambda: "ok") == "ok"


def test_the_loop_dispatch_records_legacy_tools(ledger):
    from windyfly.agent.capabilities import CapabilityDenied
    from windyfly.agent.loop import _dispatch_tool_call_inner

    db, wq = ledger
    from windyfly.agent.capabilities import Band

    out = _dispatch_tool_call_inner("send_email", json.dumps({"to": "a@b.c", "body": "hi"}), _registry(),
                                    CapabilityRegistry(), Band.OWNER, CapabilityDenied)
    assert json.loads(out) == {"sent": True}
    (row,) = _rows(db, wq)
    assert row["capability_id"] == "tool.send_email"
