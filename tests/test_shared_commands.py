"""Conformance: windyfly implements the shared owner command set (windy-contracts commands.v1) as FIXED CODE.

Each shared name, typed by the verified owner on Matrix, is answered by the runtime with no model call,
never echoes a secret, and a command either does its thing or says plainly it cannot. A non-owner is refused.
"""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, patch

import pytest

from windyfly.commands import core, shared
from windyfly.commands.registry import registry

OWNER = "@owner:chat.windychat.ai"
STRANGER = "@stranger:chat.windychat.ai"
SECRETS = {
    "ETERNITAS_PASSPORT_TOKEN": "eyJhbGciOiJFUzI1NiJ9.SECRET-PAYLOAD-123.SECRET-SIG-456",
    "MATRIX_BOT_TOKEN": "syt_SECRETMATRIXTOKEN_789",
    "ANTHROPIC_API_KEY": "sk-ant-SECRETKEY-000",
    "WINDYMAIL_JMAP_TOKEN": "SECRETJMAPTOKEN-111",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-CONF")
    for k, v in SECRETS.items():
        monkeypatch.setenv(k, v)
    core.init_core()
    from windyfly.memory.database import Database
    monkeypatch.setattr(core, "_db", Database(":memory:"))  # restored after the test


async def _say(text: str, sender: str = OWNER) -> tuple[bool, str]:
    from windyfly.channels.base import handle_incoming
    return await handle_incoming(text, {"platform": "matrix", "channel_id": "!conf:x", "sender_id": sender})


WINDYFLY_SHARED = [n for n, _a, _h in shared.mine()]
RUNNABLE = [n for n in WINDYFLY_SHARED if n != "agents"]  # /agents lands with the peer gate (its own tests)


def test_the_contract_mirror_is_well_formed():
    names = [n for n, _a, _h, _w in shared.SHARED]
    assert len(names) == len(set(names)) <= 20
    for n, a, h, who in shared.SHARED:
        assert re.fullmatch(r"[a-z]{2,16}", n) and len(a) <= 80 and len(h) <= 120
        assert who and set(who) <= {"roster", "windyfly"}


@pytest.mark.parametrize("name", RUNNABLE)
@pytest.mark.asyncio
async def test_each_shared_command_is_answered_by_code_for_the_owner(name):
    arg = {"forget": " pizza --confirm"}.get(name, "")
    with patch("windyfly.agent.executor.run_turn", new_callable=AsyncMock) as model:
        was_command, reply = await _say(f"/{name}{arg}")
    assert was_command is True, f"/{name} fell through to the model"
    model.assert_not_called()
    assert isinstance(reply, str) and reply.strip()
    for secret in SECRETS.values():
        assert secret not in reply, f"/{name} echoed a secret"
    assert "Unknown command" not in reply


@pytest.mark.parametrize("name", ["status", "whoami", "help", "model"])
@pytest.mark.asyncio
async def test_slash_bang_and_a_capitalised_name_all_run_the_same_command(name):
    _, slash = await _say(f"/{name}")
    _, bang = await _say(f"!{name}")
    _, caps = await _say(f"/{name.capitalize()}")
    assert slash == bang == caps


@pytest.mark.parametrize("name", RUNNABLE)
@pytest.mark.asyncio
async def test_a_non_owner_is_refused_every_one_of_them(name):
    was_command, reply = await _say(f"/{name}", sender=STRANGER)
    assert was_command is True
    assert "owner" in reply.lower() and "only" in reply.lower()  # "Commands are owner-only" / "Only my owner can use recovery"


@pytest.mark.asyncio
async def test_help_is_the_shared_list_in_the_contracts_shape():
    _, reply = await _say("/help")
    lines = reply.splitlines()
    assert lines[-1] == "/commands: Every command I have."
    for line in lines[:-1]:
        assert re.fullmatch(r"/[a-z]{2,16}( \S.*?)?: .+", line), line
    listed = [ln.split()[0].lstrip("/").rstrip(":") for ln in lines[:-1]]
    for n in listed:  # every listed command really exists (no pretending)
        assert registry.get(n) is not None or n in ("pause", "resume"), n
    assert "136" not in reply and len(lines) < 20  # the full list is /commands, not the default


@pytest.mark.asyncio
async def test_commands_lists_everything_with_the_slash_prefix_on_matrix():
    _, reply = await _say("/commands")
    assert len(reply.splitlines()) > 50 and "/doctor" in reply and "!doctor" not in reply


@pytest.mark.asyncio
async def test_undo_says_plainly_nothing_can_be_undone_and_never_a_sentinel():
    _, reply = await _say("/undo")
    assert "Nothing can be undone" in reply
    for sentinel in ("UNDO_LAST", "RESET_SESSION", "NEW_SESSION"):
        assert sentinel not in reply
    _, reset = await _say("/reset")
    assert "RESET_SESSION" not in reset


@pytest.mark.asyncio
async def test_forget_keeps_the_what_in_the_confirm_text_and_says_the_truth():
    _, ask = await _say("/forget pizza")
    assert "/forget pizza --confirm" in ask
    _, done = await _say("/forget pizza --confirm")
    assert "can't forget" in done and "pizza" in done and "nothing was removed" in done


@pytest.mark.asyncio
async def test_memory_means_what_i_remember_and_the_cap_setting_moved():
    _, old = await _say("/memory 1M")
    assert "/contextcap" in old and "moved" in old
    _, cap = await _say("/contextcap")
    assert "context cap" in cap.lower()
    _, mem = await _say("/memory")
    assert "about you" in mem and "cap" not in mem.lower()
    _, search = await _say("/memory pizza")
    assert "pizza" in search


@pytest.mark.asyncio
async def test_usage_says_it_is_counted_here_not_windy_minds_numbers():
    reply = await registry.execute("usage", {"platform": "matrix"})
    assert reply.startswith("Database not available") or "not Windy Mind's numbers" in reply
