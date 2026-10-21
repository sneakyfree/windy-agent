"""The owner's stop and addressed-only agent turns (agent-teamwork.v1 P1 + P5; Grant's order 10-17).

On 10-17 two of the owner's agents looped in a group room and the owner's "stop" did not stop them. In
windyfly, #528's budget even reset on any human message, so a "stop" bought six more replies.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.test_matrix_bot import _make_config
from windyfly.channels import room_stop as rs
from windyfly.channels import turn_budget as tb
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

ROOM = "!trip:chat.windychat.ai"
OWNER = "@owner:example.test"
OTHER_AGENT = "@agent_et26-ab12-cd34:chat.windychat.ai"


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    rs.reset_for_tests()
    tb.reset_for_tests()
    yield
    rs.reset_for_tests()
    tb.reset_for_tests()


def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    bot.client.room_read_markers = AsyncMock()
    return bot


def _msg(sender, body="hi", content=None, members=3):
    room = MagicMock()
    room.room_id, room.display_name, room.joined_count = ROOM, "Virgin Islands trip", members
    room.user_name.return_value = "someone"
    ev = MagicMock()
    ev.sender, ev.body, ev.server_timestamp, ev.event_id = sender, body, time.time() * 1000, "$e"
    ev.source = {"content": {"msgtype": "m.text", "body": body, **(content or {})}}
    return room, ev


async def _run(bot, room, ev):
    calls = []

    def respond(*a, **k):
        calls.append(a[3])
        return "an answer"

    with patch("windyfly.channels.matrix_bot.agent_respond", side_effect=respond):
        await bot._on_message(room, ev)
    return calls


def _to_me(bot):
    return {"m.mentions": {"user_ids": [bot.bot_user_id]}}


@pytest.mark.asyncio
async def test_the_owners_stop_silences_agent_replies_without_a_model_call_or_a_reply():
    bot = _bot()
    assert await _run(bot, *_msg(OWNER, "Stop!")) == []                     # the stop itself: no turn
    bot.client.room_send.assert_not_awaited()                               # and no reply, so no push
    assert await _run(bot, *_msg(OTHER_AGENT, "more ideas", _to_me(bot))) == []   # silenced
    bot.client.room_send.assert_not_awaited()
    assert await _run(bot, *_msg(OWNER, "ok, carry on with the hotels")) == ["ok, carry on with the hotels"]
    assert await _run(bot, *_msg(OTHER_AGENT, "here are hotels", _to_me(bot))) != []   # resumed


@pytest.mark.asyncio
async def test_a_stop_never_resets_the_turn_budget():
    bot = _bot()
    for _ in range(tb.limit()):
        tb.record(ROOM)
    assert not tb.allow(ROOM)
    await _run(bot, *_msg(OWNER, "please stop"))
    assert not tb.allow(ROOM)                                               # still spent: no fresh replies


@pytest.mark.asyncio
async def test_the_stop_event_from_the_owner_silences_and_from_anyone_else_does_nothing():
    bot = _bot()
    room = MagicMock()
    room.room_id = ROOM
    ev = MagicMock()
    ev.type, ev.sender = rs.STOP_EVENT, OTHER_AGENT
    await bot._on_stop_event(room, ev)
    assert not rs.stopped(ROOM)
    ev.sender = OWNER
    await bot._on_stop_event(room, ev)
    assert rs.stopped(ROOM)


@pytest.mark.asyncio
async def test_the_owners_stop_works_in_guest_mode_too(monkeypatch, tmp_path):
    flag = tmp_path / ".guest"
    flag.write_text("{}")
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(flag))
    bot = _bot()
    await _run(bot, *_msg(OWNER, "stop"))
    assert rs.stopped(ROOM)


@pytest.mark.parametrize("text,stops", [
    # agent-teamwork.v1's one canonical list (windy-contracts #158, x-runtime.one_to_one_stop_phrases) + /stop
    ("stop", True), ("STOP.", True), ("  Stop it!! ", True), ("stop now", True), ("stop talking", True),
    ("please stop", True), ("enough!", True), ("that\u2019s enough", True), ("that's enough", True),
    ("thats enough", True), ("be quiet", True), ("quiet", True), ("silence", True), ("shut up", True),
    ("pause", True), ("halt", True), ("/stop", True), ("/STOP ", True),
    ("/stop now", True), ("/stop please", True), ("!stop", True), ("!STOP now", True),
    ("stop please", False), ("stop that", False), ("stop, it", False), ("/stopwatch", False), ("/pause", False),
    ("stop the timer", False), ("can you stop by the store", False), ("don't stop", False), ("", False),
])
def test_only_a_whole_stop_word_is_a_stop(text, stops):
    assert rs.is_stop_word(text) is stops


@pytest.mark.asyncio
async def test_another_agents_message_is_a_turn_only_when_it_addresses_this_agent():
    bot = _bot()
    assert await _run(bot, *_msg(OTHER_AGENT, "anyone?")) == []                       # group room, not addressed
    assert await _run(bot, *_msg(OTHER_AGENT, "you there?", _to_me(bot))) != []       # a mention
    task = {"ai.windy.task": {"type": "request", "task_id": "t1", "to": bot.bot_user_id, "ask": "find flights"}}
    assert await _run(bot, *_msg(OTHER_AGENT, "find flights", task)) != []            # a typed request to me
    other = {"ai.windy.task": {"type": "request", "task_id": "t2", "to": "@someone-else:x", "ask": "x"}}
    assert await _run(bot, *_msg(OTHER_AGENT, "x", other)) == []                      # a request to someone else
    assert await _run(bot, *_msg(OTHER_AGENT, "just us", members=2)) != []            # two of us alone


@pytest.mark.asyncio
async def test_a_person_who_is_not_the_owner_cannot_stop_the_agents():
    bot = _bot()
    await _run(bot, *_msg("@guest:example.test", "stop"))
    assert not rs.stopped(ROOM)


# ── Hub's #596 review: never raise, test gaps, a stop survives a restart ─────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("mentions", [{"user_ids": 5}, {"user_ids": "@windyfly:chat.windychat.ai"},
                                      {"user_ids": [5, None, {"a": 1}]}, "not a dict", {"user_ids": None}])
async def test_a_malformed_mention_is_dropped_never_raised(mentions):
    bot = _bot()
    assert await _run(bot, *_msg(OTHER_AGENT, "x", {"m.mentions": mentions})) == []


@pytest.mark.asyncio
async def test_any_fault_in_the_decision_drops_the_message(monkeypatch):
    bot = _bot()

    def boom(*a, **k):
        raise RuntimeError("bad event")

    monkeypatch.setattr(rs, "addressed_to", boom)
    assert await _run(bot, *_msg(OTHER_AGENT, "x", _to_me(bot))) == []      # no exception escapes


@pytest.mark.asyncio
async def test_with_no_owner_configured_nobody_can_stop(monkeypatch):
    monkeypatch.setenv("WINDY_OWNER_IDS", "")
    assert not rs.is_owner(OWNER, None)
    assert not rs.is_owner("", None) and not rs.is_owner(None, None)


@pytest.mark.asyncio
async def test_an_agent_saying_stop_is_not_a_stop():
    bot = _bot()
    await _run(bot, *_msg(OTHER_AGENT, "stop", _to_me(bot)))
    assert not rs.stopped(ROOM)


@pytest.mark.asyncio
async def test_two_agents_and_the_owner_a_loop_ends_at_the_owners_stop():
    bot = _bot()
    turns = 0
    for _ in range(3):
        turns += len(await _run(bot, *_msg(OTHER_AGENT, "and another idea", _to_me(bot))))
    assert turns == 3                                                       # teamwork while addressed
    await _run(bot, *_msg(OWNER, "stop"))
    for _ in range(10):
        assert await _run(bot, *_msg(OTHER_AGENT, "one more!", _to_me(bot))) == []
    assert await _run(bot, *_msg(OWNER, "ok go on")) == ["ok go on"]
    assert await _run(bot, *_msg(OTHER_AGENT, "back to work", _to_me(bot))) != []


def test_a_stop_survives_a_restart():
    rs.stop(ROOM)
    rs._stopped.clear()                       # the process restarts: memory is empty
    rs._loaded = False
    assert rs.stopped(ROOM)                   # read back from the state file
    rs.resume(ROOM)
    rs._stopped.clear()
    rs._loaded = False
    assert not rs.stopped(ROOM)



def test_the_stop_file_is_0600_and_never_world_readable(tmp_path):
    import os
    import stat

    rs.stop(ROOM)
    path = tmp_path / "state" / "room_stops.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]     # no leftovers


def test_a_corrupt_stop_file_is_no_stops_and_the_next_stop_rewrites_it(tmp_path):
    path = tmp_path / "state" / "room_stops.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    rs._loaded = False
    assert not rs.stopped(ROOM)
    rs.stop(ROOM)
    assert rs.stopped(ROOM) and ROOM in path.read_text(encoding="utf-8")


def _other_process_writes(tmp_path, rooms):
    import json
    import os
    import time as _t

    path = tmp_path / "state" / "room_stops.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    before = path.stat().st_mtime_ns if path.exists() else 0
    path.write_text(json.dumps(rooms), encoding="utf-8")
    os.utime(path, ns=(before + 10**9, before + 10**9 + int(_t.time()) % 7))    # a visibly newer file


def test_a_stop_written_by_another_process_is_seen_without_a_restart(tmp_path):
    """Hub on #599: two agent processes may share a state directory."""
    assert not rs.stopped(ROOM)                         # this process has read the (empty) file
    _other_process_writes(tmp_path, [ROOM])             # the other process stops the room
    assert rs.stopped(ROOM)
    _other_process_writes(tmp_path, [])                 # and resumes it
    assert not rs.stopped(ROOM)


def test_a_write_never_drops_a_stop_another_process_added_meanwhile(tmp_path):
    """Read, change one room, write, under the lock: never this process's whole memory."""
    other = "!other:chat.windychat.ai"
    assert not rs.stopped(ROOM)                         # this process read the file: no stops
    _other_process_writes(tmp_path, [ROOM])             # another process stops ROOM after that read
    rs._change(other, True)                             # this process stops another room
    assert set(rs._read()) == {ROOM, other}
    rs._change(other, False)
    assert rs._read() == {ROOM}


@pytest.mark.asyncio
async def test_a_fault_in_the_stop_event_is_dropped_never_raised(monkeypatch):
    bot = _bot()
    room = MagicMock()
    room.room_id = ROOM
    ev = MagicMock()
    ev.type, ev.sender = rs.STOP_EVENT, OWNER
    monkeypatch.setattr(rs, "stop", lambda r: (_ for _ in ()).throw(OSError("disk")))
    await bot._on_stop_event(room, ev)                 # no exception escapes



@pytest.mark.asyncio
async def test_slash_stop_in_a_room_silences_the_agents_and_never_runs_the_process_stop(monkeypatch):
    """windyfly's old /stop command stopped the agent's own service (systemctl stop); in a chat room /stop
    is now the contract's stop and never reaches the command layer."""
    bot = _bot()
    called = []
    monkeypatch.setattr("windyfly.channels.base.handle_incoming",
                        lambda *a, **k: called.append(a) or (True, "Stopped via systemctl"))
    assert await _run(bot, *_msg(OWNER, "/stop")) == []
    assert rs.stopped(ROOM) and called == []
    bot.client.room_send.assert_not_awaited()



@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["/stop now", "/stop please", "!stop", "!stop now", "/stop"])
async def test_no_form_of_slash_stop_from_the_owner_reaches_the_process_stop(monkeypatch, text):
    """Hub on #604: through the REAL command layer, "/stop now" and "!stop" ran systemctl stop on the agent."""
    from windyfly.commands import core as _core
    from windyfly import platform as _platform

    stopped_process = []
    monkeypatch.setattr(_platform, "find_systemd_unit_for_pattern", lambda *a: stopped_process.append(a) or None)
    monkeypatch.setattr(_platform, "systemctl_stop", lambda *a: stopped_process.append(a) or (True, ""))
    monkeypatch.setattr(_core.os, "kill", lambda *a: stopped_process.append(a))
    bot = _bot()
    assert await _run(bot, *_msg(OWNER, text)) == []
    assert rs.stopped(ROOM) and stopped_process == []


def _process_control(monkeypatch, name, platform):
    import asyncio

    from windyfly.commands.registry import registry
    from windyfly.commands.setup import init_all_commands
    from windyfly.memory.database import Database
    from windyfly.commands import core as _core
    from windyfly import platform as _platform

    init_all_commands(db=Database(":memory:"), config={})
    ran = []
    monkeypatch.setattr(_platform, "find_systemd_unit_for_pattern", lambda *a: ran.append(a) or None)
    monkeypatch.setattr(_platform, "systemctl_stop", lambda *a: ran.append(a) or (True, ""))
    monkeypatch.setattr(_core.os, "kill", lambda *a: ran.append(a))
    monkeypatch.setattr(_core.os, "system", lambda *a: ran.append(a) or 0)
    out = asyncio.run(registry.execute(f"/{name}", {"platform": platform, "sender_id": OWNER}))
    return out, ran


@pytest.mark.parametrize("name", ["stop", "restart", "shutdown", "kill"])
@pytest.mark.parametrize("platform", ["matrix", "telegram", "discord", "slack", "whatsapp", "signal", "teams", "irc"])
def test_process_control_never_runs_from_a_chat(monkeypatch, name, platform):
    out, ran = _process_control(monkeypatch, name, platform)
    assert "doesn't run from a chat" in out and f"`windy {name}`" in out and ran == []


def test_the_refusal_names_the_quiet_switch_that_exists_there(monkeypatch):
    assert "say stop" in _process_control(monkeypatch, "stop", "matrix")[0]
    assert "/pause" in _process_control(monkeypatch, "stop", "telegram")[0]
    out = _process_control(monkeypatch, "stop", "discord")[0]
    assert "/pause" not in out and "say stop" not in out


@pytest.mark.parametrize("ctx", [None, {}, {"platform": None}, {"platform": ""}, {"platform": "unknown"},
                                 {"platform": "Terminal"}, {"platform": "web"}, {"platform": "email"},
                                 {"platform": "sms"}])
def test_process_control_is_refused_unless_the_platform_is_terminal_or_cli(monkeypatch, ctx):
    import asyncio

    from windyfly.commands.registry import registry
    from windyfly.commands.setup import init_all_commands
    from windyfly.memory.database import Database
    from windyfly.commands import core as _core
    from windyfly import platform as _platform

    init_all_commands(db=Database(":memory:"), config={})
    ran = []
    monkeypatch.setattr(_platform, "find_systemd_unit_for_pattern", lambda *a: ran.append(a) or None)
    monkeypatch.setattr(_platform, "systemctl_stop", lambda *a: ran.append(a) or (True, ""))
    monkeypatch.setattr(_core.os, "kill", lambda *a: ran.append(a))
    monkeypatch.setattr(_core.os, "system", lambda *a: ran.append(a) or 0)
    out = asyncio.run(registry.execute("/stop", ctx))
    assert "doesn't run from a chat" in out and ran == []


def test_process_control_still_runs_from_the_terminal(monkeypatch):
    out, ran = _process_control(monkeypatch, "stop", "terminal")
    assert "doesn't run from a chat" not in out and ran
