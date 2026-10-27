"""Mirror is NOT offered on windyfly (Boss decision 2026-10-27T20:59:29Z, MR28 option a, under Grant's 10-27 delegation).

A runtime that serves a Mirror result must first implement mirror-tools.v1 R23 1.2.0 (tag by refs + device_id, the
erase in code, catch-up, refuse-store). windyfly serves none, so it carries this guard instead: no windyfly tool or
channel can receive or store a Mirror result. MR28's owner is Windy Chat's agent-roster; windyfly gets Mirror (with
R23) only with the roster/Fly convergence after Build 138, and then this test must be replaced, not deleted.
Names from windy-contracts schema/chat/mirror-tools.v1.json x-model-tools and x-events (main 2ce1d2f).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"
MIRROR_TOOLS = ("mirror_status", "mirror_people_list", "mirror_person_get", "mirror_thread_read", "mirror_search",
                "mirror_draft_propose", "mirror_send", "mirror_read")
MIRROR_EVENTS = ("ai.windy.mirror_tools", "ai.windy.mirror_erased")


# Any Mirror tool or event name, underscore or dotted (mirror_search, mirror.search, ai.windy.mirror_erased).
MIRROR_NAME = re.compile(r"\bai\.windy\.mirror|\bmirror[._](status|people_list|person_get|thread_read|search|"
                         r"draft_propose|send|read|tools|erased)\b")


def test_no_windyfly_source_offers_or_handles_a_mirror_tool_or_event():
    hits = []
    for py in SRC.rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        hits += [f"{py.relative_to(SRC)}: {m.group(0)}" for m in MIRROR_NAME.finditer(text)]
    assert hits == [], "windyfly must not offer Mirror until it implements mirror-tools.v1 R23 (MR28): " + str(hits)


@pytest.mark.parametrize("etype", MIRROR_EVENTS)
def test_the_matrix_channel_drops_a_mirror_event_without_touching_anything(etype):
    """Every unknown Matrix event reaches _stop_event; a Mirror event must return before it reads any state
    (``self`` here has no attributes at all, so any access would raise)."""
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot as MatrixBot

    room = SimpleNamespace(room_id="!r:chat.windychat.ai")
    event = SimpleNamespace(type=etype, sender="@owner:chat.windychat.ai",
                            source={"content": {"device_id": "d1", "all": True, "rids": ["x"]}})
    assert asyncio.run(MatrixBot._stop_event(object(), room, event)) is None


def test_the_booted_registries_advertise_no_mirror_tool(tmp_path, monkeypatch):
    """Run the real boot registration (the one every channel uses) and check every name it advertises."""
    from windyfly.agent.boot import BootContext, BootSequence, default_capability_registration_sequence
    from windyfly.agent.capabilities.registry import CapabilityRegistry
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    db, wq = Database(str(tmp_path / "t.db")), WriteQueue()
    wq.start()
    try:
        tools, caps = ToolRegistry(), CapabilityRegistry()
        BootSequence(default_capability_registration_sequence()).run(
            BootContext(config={}, db=db, write_queue=wq, tool_registry=tools, capability_registry=caps))
        names = [c.id for c in caps.all()] + [t["function"]["name"] for t in tools.get_schemas()]
    finally:
        wq.stop()
    assert len(names) > 20, "the boot registered almost nothing: the check would prove nothing"
    assert [n for n in names if re.match(r"mirror[._]", n) or "mirror_" in n] == []
