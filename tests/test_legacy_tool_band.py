"""Legacy (ToolRegistry) tools carry a band; outward-acting ones are the owner's only (Hub ruling 10-17).

Before: agent/loop.py dropped legacy tools only for strangers (band <= SANDBOX). A USER-band sender (an
allowlisted non-owner, or the owner's own messages while guest mode is on) was offered every legacy tool
and could run it: send_email, send_sms, make_call, upload_to_cloud, the site and code writers.
"""

from __future__ import annotations

from unittest.mock import patch

from windyfly.agent.capabilities import Band
from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry

from tests.test_agent_loop import _make_config


def _answer(tool_calls=None):
    return {"content": "" if tool_calls else "done", "model": "m", "input_tokens": 1, "output_tokens": 1,
            "tool_calls": tool_calls}


def _turn(band, tool_name: str):
    """One turn where the model calls ``tool_name``. Returns (offered tool names, calls that ran)."""
    ran: list[str] = []
    registry = ToolRegistry()
    registry.register(tool_name, "acts as the owner", {"type": "object", "properties": {}},
                      lambda **_: ran.append(tool_name) or '{"ok": true}')
    offered: list[set[str]] = []

    def llm(*a, **k):
        offered.append({t["function"]["name"] for t in (k.get("tools") or [])})
        if len(offered) == 1:
            return _answer([{"id": "c1", "function": {"name": tool_name, "arguments": {}}}])
        return _answer()

    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=llm):
            agent_respond(_make_config(), db, wq, "please do it", "s-band", tool_registry=registry, band=band)
    finally:
        wq.stop()
        db.close()
    return (offered[0] if offered else set()), ran


def test_a_user_band_sender_is_neither_offered_nor_able_to_run_send_email():
    offered, ran = _turn(Band.USER, "send_email")
    assert "send_email" not in offered
    assert ran == []


def test_the_owner_still_gets_send_email():
    offered, ran = _turn(Band.OWNER, "send_email")
    assert "send_email" in offered and ran == ["send_email"]


import json  # noqa: E402

import pytest  # noqa: E402

from windyfly.tools import bands  # noqa: E402

_OWNER_ONLY = sorted(n for n, b in bands.TOOL_BANDS.items() if b == Band.OWNER)
_PUBLIC = sorted(n for n, b in bands.TOOL_BANDS.items() if b == Band.USER)


def _registry(names):
    ran: list[str] = []
    r = ToolRegistry()
    for n in names:
        r.register(n, "x", {"type": "object", "properties": {}}, lambda _n=n, **_: ran.append(_n) or "{}")
    return r, ran


@pytest.mark.parametrize("name", _OWNER_ONLY)
def test_every_owner_tool_is_refused_to_user_and_trusted_at_execution(name):
    r, ran = _registry([name])
    for band in (Band.SANDBOX, Band.USER, Band.TRUSTED):
        with bands.turn_band(band):
            out = json.loads(r.execute(name, {}))
        assert "refused" in out["error"], (name, band)
    with bands.turn_band(Band.OWNER):
        r.execute(name, {})
    assert ran == [name]


@pytest.mark.parametrize("name", _PUBLIC)
def test_public_reads_stay_with_user(name):
    r, ran = _registry([name])
    with bands.turn_band(Band.USER):
        r.execute(name, {})
    assert ran == [name]


def test_a_tool_not_in_the_table_is_owner_only():
    r, ran = _registry(["brand_new_tool"])
    with bands.turn_band(Band.TRUSTED):
        assert "refused" in r.execute("brand_new_tool", {})
    assert ran == []


def test_with_no_turn_band_only_public_reads_run():
    r, ran = _registry(["send_email", "get_weather"])
    assert "refused" in r.execute("send_email", {})
    r.execute("get_weather", {})
    assert ran == ["get_weather"]


def test_the_offer_shows_user_only_public_reads():
    r, _ran = _registry(list(bands.TOOL_BANDS))
    offered = {s["function"]["name"] for s in r.get_schemas(Band.USER)}
    assert offered == set(_PUBLIC)
    assert {s["function"]["name"] for s in r.get_schemas(Band.OWNER)} == set(bands.TOOL_BANDS)


def test_a_nested_band_can_only_lower_the_turn():
    with bands.turn_band(Band.USER):
        with bands.turn_band(Band.OWNER):
            assert bands.current_band() == Band.USER


def test_guest_mode_turns_the_owner_into_user_and_send_email_does_not_run(monkeypatch, tmp_path):
    from windyfly.channels.identity import resolve_band

    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    monkeypatch.setenv("WINDY_OWNER_IDS", "matrix:@owner:example.test")
    band = resolve_band("matrix", "@owner:example.test")
    assert band == Band.USER
    offered, ran = _turn(band, "send_email")
    assert "send_email" not in offered and ran == []


def test_a_text_message_turn_runs_no_outward_tool():
    """matrix_bot's Windy Text relay runs the owner's text as a USER turn: its comment is now true."""
    for tool in ("send_email", "make_call", "upload_to_cloud", "windycodeweb_publish"):
        offered, ran = _turn(Band.USER, tool)
        assert tool not in offered and ran == [], tool


def test_a_stranger_cannot_run_a_tool_the_model_names_anyway():
    offered, ran = _turn(Band.SANDBOX, "send_email")
    assert offered == set() and ran == []


def test_the_dispatch_can_lower_but_never_set_the_turn_band():
    with bands.cap_turn_band(Band.OWNER):
        assert bands.current_band() is None                          # outside a turn: still none
    with bands.turn_band(Band.OWNER), bands.cap_turn_band(Band.USER):
        assert bands.current_band() == Band.USER

