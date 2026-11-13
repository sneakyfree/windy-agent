"""No OWNER band with no owner in the turn (Boss BOARD 2026-11-07T19:26:03Z; Hub's conditions on design A).

- Background goals (goal_autorun, goal_pacing) run at TRUSTED. An owner-only, destructive or outward tool is refused at the
  unchanged gate; the refusal records the tool's NAME, every later call that turn is refused too, the goal pauses, and
  the owner gets ONE chat message (goal text + tool name, never the arguments). Only the owner's own /goal resume
  unpauses, and that grants nothing.
- The Matrix phone-result turn runs at USER.
- /benchmark runs at OWNER only when the in-process command gate says the owner's own command started it; the
  shell is Docker only there.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from windyfly.agent.capabilities import Band, Tier
from windyfly.memory import goals as goals_mod
from windyfly.memory.database import Database
from windyfly.tools import bands

PAUSED = json.dumps({"error": bands.PAUSED_TEXT})


# ── The goal-turn record ─────────────────────────────────────────────────────────────────────────────────────────────


def test_each_goal_turn_starts_with_an_empty_record() -> None:
    """Hub (c): set and read per turn, reset at turn start, so turns never see each other's flag."""
    with bands.goal_turn():
        bands.note_needs_owner("send_email")
        assert bands.needs_owner() == "send_email"
    with bands.goal_turn():
        assert bands.needs_owner() is None
    assert bands.needs_owner() is None and not bands.in_goal_turn()


def test_outside_a_goal_turn_nothing_is_recorded() -> None:
    bands.note_needs_owner("send_email")
    assert bands.needs_owner() is None


def test_the_record_survives_a_capability_running_in_its_own_task() -> None:
    """A capability run inside an asyncio task works on a COPY of the context; the holder is shared, a bare set is not."""
    async def tool() -> None:
        bands.note_needs_owner("shell.exec (host_rw)")

    with bands.goal_turn():
        asyncio.run(tool())
        assert bands.needs_owner() == "shell.exec (host_rw)"


def test_the_first_tool_is_kept() -> None:
    with bands.goal_turn():
        bands.note_needs_owner("send_email")
        bands.note_needs_owner("make_call")
        assert bands.needs_owner() == "send_email"


def test_tools_are_listed_at_owner_only_in_a_goal_turn() -> None:
    assert bands.listing_band(Band.TRUSTED) == Band.TRUSTED
    with bands.goal_turn():
        assert bands.listing_band(Band.TRUSTED) == Band.OWNER
        assert bands.listing_band(None) is None


# ── The gate in a goal turn ──────────────────────────────────────────────────────────────────────────────────────────


class _Caps:
    def __init__(self, **caps: SimpleNamespace) -> None:
        self.caps = caps
        self.invoked: list[str] = []

    def get(self, name: str) -> SimpleNamespace | None:
        return self.caps.get(name)

    def invoke_sync(self, name: str, args: dict, band: Band) -> str:
        self.invoked.append(name)
        return "ran"


def _cap(band: Band, tier: Tier) -> SimpleNamespace:
    return SimpleNamespace(band_required=band, tier=tier)


def _dispatch(name: str, caps: _Caps, legacy: MagicMock | None = None, band: Band = Band.TRUSTED) -> str:
    from windyfly.agent.capabilities import CapabilityDenied
    from windyfly.agent.loop import _dispatch_tool_call_inner

    return _dispatch_tool_call_inner(name, "{}", legacy, caps, band, CapabilityDenied)


def test_an_owner_only_capability_pauses_the_goal_and_never_runs() -> None:
    caps = _Caps(memory_search=_cap(Band.OWNER, Tier.READ_EXTERNAL), calc=_cap(Band.USER, Tier.PURE_COMPUTE))
    with bands.goal_turn():
        assert _dispatch("memory_search", caps) == PAUSED            # Hub (f): that text and nothing more
        assert bands.needs_owner() == "memory_search"
        assert _dispatch("calc", caps) == PAUSED                     # nothing else runs this turn
    assert caps.invoked == []


def test_an_outward_capability_is_refused_in_a_goal_turn_even_at_its_band() -> None:
    """Teams send / an MCP call are EXTERNAL_EFFECT at TRUSTED: outward sends are refused for a goal (Boss)."""
    caps = _Caps(teams_send=_cap(Band.TRUSTED, Tier.EXTERNAL_EFFECT))
    with bands.goal_turn():
        assert _dispatch("teams_send", caps) == PAUSED
    assert caps.invoked == []
    assert _dispatch("teams_send", caps) == "ran"                     # a person's TRUSTED turn is unchanged


def test_an_allowed_capability_still_runs_in_a_goal_turn() -> None:
    caps = _Caps(calc=_cap(Band.USER, Tier.PURE_COMPUTE))
    with bands.goal_turn():
        assert _dispatch("calc", caps) == "ran"
        assert bands.needs_owner() is None


def test_an_owner_only_legacy_tool_pauses_the_goal() -> None:
    legacy = MagicMock()
    with bands.goal_turn():
        assert _dispatch("send_email", _Caps(), legacy) == PAUSED
        assert bands.needs_owner() == "send_email"
    legacy.execute.assert_not_called()


def test_shell_host_rw_in_a_goal_turn_is_recorded() -> None:
    from windyfly.agent.capabilities.shell import _shell_exec_handler

    with bands.goal_turn():
        with pytest.raises(PermissionError):
            _shell_exec_handler(command="ls", sandbox="host_rw", _dispatcher=MagicMock(), _band=Band.TRUSTED,
                                _host_rw_enabled=True)
        assert bands.needs_owner() == "shell.exec (host_rw)"


# ── The goal runners ─────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def goal_db():
    db = Database(":memory:")
    gid = goals_mod.create_goal(db, session_id="telegram:1:v1", text="plan the trip", user_id="42")
    return db, gid


def test_autorun_turns_are_trusted_and_pause_with_one_message(goal_db) -> None:
    from windyfly.agent import goal_autorun

    db, gid = goal_db
    goals_mod.start_autorun(db, gid, max_turns=5, chat_id="chat-x")
    seen: list[Band] = []

    def respond(**kwargs):
        seen.append(kwargs["band"])
        bands.note_needs_owner("send_email")           # the model tried an owner-only send
        return "drafted it"

    deliver = AsyncMock()
    out = asyncio.run(goal_autorun.run_autorun(goal_id=gid, db=db, deliver=deliver, agent_respond=respond,
                                               config={}, write_queue=MagicMock()))
    assert seen == [Band.TRUSTED]                        # one turn, at TRUSTED
    assert out["abort_reason"] == "paused: needs the owner"
    assert goals_mod.get_goal(db, gid)["status"] == goals_mod.STATUS_PAUSED
    deliver.assert_awaited_once()
    msg = deliver.await_args.args[1]
    assert "send_email" in msg and "plan the trip" in msg and "/goal resume" in msg


def test_pacing_turns_are_trusted_and_pause_once(goal_db) -> None:
    from windyfly.agent import goal_pacing

    db, gid = goal_db
    seen: list[Band] = []

    def respond(**kwargs):
        seen.append(kwargs["band"])
        bands.note_needs_owner("make_call")
        return "x"

    deliver = AsyncMock()
    goal = dict(goals_mod.get_goal(db, gid), chat_id="chat-x")
    for _ in range(2):                                   # a second tick on the same goal row: no second message
        asyncio.run(goal_pacing._fire_progress_check(goal=goal, db=db, deliver=deliver, agent_respond=respond,
                                                     config={}, write_queue=MagicMock()))
    assert seen[0] == Band.TRUSTED
    assert goals_mod.get_goal(db, gid)["status"] == goals_mod.STATUS_PAUSED
    deliver.assert_awaited_once()                        # Hub (e): one message per pause
    assert "make_call" in deliver.await_args.args[1]


def test_a_paused_goal_is_never_due_for_pacing(goal_db) -> None:
    db, gid = goal_db
    goals_mod.set_goal_pace(db, gid, pace_seconds=300)
    goals_mod.pause_goal(db, gid, tool_name="send_email")
    assert all(g["id"] != gid for g in goals_mod.goals_due_for_pacing(db))


def test_the_pause_message_never_carries_arguments() -> None:
    """Hub (d): the goal text and the tool NAME only."""
    msg = goals_mod.pause_message("plan the trip", "send_email")
    assert "send_email" in msg and "@" not in msg


# ── /goal resume ─────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("band", [None, Band.SANDBOX, Band.USER, Band.TRUSTED])
def test_resume_from_anyone_but_the_owner_does_nothing(goal_db, band) -> None:
    """Hub (a): only a turn whose band is OWNER resumes, never a text match from any sender."""
    from windyfly.channels.slash_commands import goal_resume_reply

    db, gid = goal_db
    goals_mod.pause_goal(db, gid, tool_name="send_email")
    assert goal_resume_reply(db, "telegram:1:v1", "42", band) == "Only my owner can resume a paused goal."
    assert goals_mod.get_goal(db, gid)["status"] == goals_mod.STATUS_PAUSED


def test_the_owners_resume_only_unpauses(goal_db) -> None:
    """Hub (b): active again, and its next turn is still TRUSTED (no grant)."""
    from windyfly.agent.goal_autorun import goal_band
    from windyfly.channels.slash_commands import goal_resume_reply

    db, gid = goal_db
    goals_mod.pause_goal(db, gid, tool_name="send_email")
    assert "resumed" in goal_resume_reply(db, "telegram:1:v1", "42", Band.OWNER)
    assert goals_mod.get_goal(db, gid)["status"] == goals_mod.STATUS_ACTIVE
    assert goal_band() == Band.TRUSTED
    assert goal_resume_reply(db, "telegram:1:v1", "42", Band.OWNER) == "No paused goal to resume."


def test_the_parser_knows_resume() -> None:
    from windyfly.channels.slash_commands import parse_goal_command

    assert parse_goal_command("/goal resume") == (True, "resume", None)


# ── The phone-result turn ────────────────────────────────────────────────────────────────────────────────────────────


def test_the_phone_result_turn_runs_at_user(monkeypatch) -> None:
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot

    bot = MagicMock()
    bot._room_sessions = {}
    bot.client.room_send = AsyncMock()
    run_turn = AsyncMock(return_value="")
    monkeypatch.setattr("windyfly.agent.executor.run_turn", run_turn)
    asyncio.run(WindyFlyMatrixBot._run_result_turn(bot, "!dm:x", "[stub]", "data"))
    assert run_turn.await_args.kwargs["band"] == Band.USER


# ── /benchmark ───────────────────────────────────────────────────────────────────────────────────────────────────────


def _benchmark(monkeypatch, sender_band):
    import windyfly.agent.loop as loop
    import windyfly.commands.core as core
    from windyfly.commands.registry import registry

    seen: dict = {}

    def respond(cfg, db, wq, msg, sid, band=None):
        seen["band"] = band
        seen["docker_only"] = bands.docker_only_active()
        return "4"

    monkeypatch.setattr(loop, "agent_respond", respond)
    monkeypatch.setattr("windyfly.config.load_config", lambda *a, **k: {})
    core._register_all()
    monkeypatch.setattr(core, "_db", MagicMock())
    cmd = registry.get("benchmark")
    assert cmd is not None

    async def run():
        if sender_band is None:
            return await cmd.handler({})
        with bands.command_sender(sender_band):
            return await cmd.handler({"band": "OWNER"})       # a band in the ctx dict is never trusted

    asyncio.run(run())
    return seen


def test_benchmark_is_owner_only_from_the_owners_own_command(monkeypatch) -> None:
    seen = _benchmark(monkeypatch, Band.OWNER)
    assert seen == {"band": Band.OWNER, "docker_only": True}


@pytest.mark.parametrize("sender_band", [None, Band.TRUSTED])
def test_benchmark_from_anything_else_runs_at_user(monkeypatch, sender_band) -> None:
    seen = _benchmark(monkeypatch, sender_band)
    assert seen == {"band": Band.USER, "docker_only": True}


def test_docker_only_refuses_host_rw_even_at_owner() -> None:
    from windyfly.agent.capabilities.shell import _shell_exec_handler

    with bands.docker_only(), pytest.raises(PermissionError, match="Docker only"):
        _shell_exec_handler(command="ls", sandbox="host_rw", _dispatcher=MagicMock(), _band=Band.OWNER,
                            _host_rw_enabled=True)


# ── Destructive steps pause a goal too (Boss BOARD 2026-11-07T19:39:14Z) ─────────────────────────────────────────────


@pytest.mark.parametrize("tier,pauses", [
    (Tier.PURE_COMPUTE, False), (Tier.READ_EXTERNAL, False), (Tier.WRITE_LOCAL_SAFE, False),
    (Tier.WRITE_DESTRUCTIVE, True), (Tier.EXTERNAL_EFFECT, True),
])
def test_a_goal_pauses_from_write_destructive_up(tier, pauses) -> None:
    """Delete, move, git commit: an undo log is not the owner's say, and no human is in a goal's turn."""
    caps = _Caps(step=_cap(Band.TRUSTED, tier))
    with bands.goal_turn():
        assert (_dispatch("step", caps) == PAUSED) is pauses
        assert (bands.needs_owner() == "step") is pauses
    assert caps.invoked == ([] if pauses else ["step"])
    assert _dispatch("step", caps) == "ran"                          # a person's TRUSTED turn is unchanged


def _real_fs(tmp_path):
    from windyfly.agent.capabilities.filesystem import register_filesystem_capabilities
    from windyfly.agent.capabilities.registry import CapabilityRegistry

    root = tmp_path / "files"
    root.mkdir()
    r = CapabilityRegistry()
    register_filesystem_capabilities(r, config={"capabilities": {"filesystem": {
        "allowed_roots": [str(root)], "undo_journal_path": str(tmp_path / "undo.jsonl")}}})
    return r, root


def _dispatch_real(name: str, args: dict, registry, band: Band = Band.TRUSTED) -> str:
    from windyfly.agent.capabilities import CapabilityDenied
    from windyfly.agent.loop import _dispatch_tool_call_inner

    return _dispatch_tool_call_inner(name, json.dumps(args), None, registry, band, CapabilityDenied)


@pytest.mark.parametrize("name,args", [
    ("fs.delete_file", {"path": "{f}"}),
    ("fs.move_file", {"source": "{f}", "destination": "{root}/moved.txt"}),
    ("fs.write_file", {"path": "{f}", "content": "new", "overwrite": True}),    # WRITE_DESTRUCTIVE per call
    ("fs.write_file", {"path": "{f}", "content": "new", "overwrite": "true"}),  # not a boolean: still counts (Hub)
    ("fs.write_file", {"path": "{f}", "content": "new", "overwrite": 1}),
    ("fs.write_file", {"path": "{f}", "content": "new", "overwrite": "yes"}),
])
def test_a_goal_never_deletes_moves_or_overwrites_a_real_file(tmp_path, name, args) -> None:
    registry, root = _real_fs(tmp_path)
    f = root / "keep.txt"
    f.write_text("mine")
    call = {k: (v.format(f=f, root=root) if isinstance(v, str) else v) for k, v in args.items()}
    with bands.goal_turn():
        assert _dispatch_real(name, call, registry) == PAUSED
        assert bands.needs_owner() == name
    assert f.read_text() == "mine" and not (root / "moved.txt").exists()


def test_a_goal_still_writes_a_new_file(tmp_path) -> None:
    """fs.write_file without overwrite stays WRITE_LOCAL_SAFE: a goal may still make a new file."""
    registry, root = _real_fs(tmp_path)
    with bands.goal_turn():
        out = _dispatch_real("fs.write_file", {"path": str(root / "new.txt"), "content": "hi"}, registry)
        assert out != PAUSED and bands.needs_owner() is None
    assert (root / "new.txt").read_text() == "hi"


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]"])
def test_args_that_do_not_parse_pause_a_tool_with_a_per_call_check(raw) -> None:
    def check(args):
        return Tier.WRITE_DESTRUCTIVE if args.get("overwrite") is True else None

    caps = _Caps(write=SimpleNamespace(band_required=Band.USER, tier=Tier.WRITE_LOCAL_SAFE, runtime_tier_check=check))
    from windyfly.agent.capabilities import CapabilityDenied
    from windyfly.agent.loop import _dispatch_tool_call_inner

    with bands.goal_turn():
        assert _dispatch_tool_call_inner("write", raw, None, caps, Band.TRUSTED, CapabilityDenied) == PAUSED
    assert caps.invoked == []
