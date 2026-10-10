"""Strand-to-green Wave 1: a command says what it actually did (principle 9).

- /preset under raw mode (the default) names the tone sliders it does NOT use, instead of a bare "applied".
- /presets is built from the preset table, so its numbers can never disagree with what /preset applies.
- The Matrix "online" log line states whether end-to-end encryption is really available.
"""

from __future__ import annotations

import inspect

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.control_panel import PRESETS, set_slider
from windyfly.memory.database import Database
from windyfly.personality.engine import RAW_MODE_TONE_SLIDERS, build_personality_block


@pytest.fixture
def db():
    from windyfly.commands.core import wire_runtime

    database = Database(":memory:")
    init_all_commands(db=database, config={})
    wire_runtime(db=database)
    yield database
    database.close()


async def _run(text: str) -> str:
    ok, out = await handle_incoming(text, {"platform": "telegram", "channel_id": "x"})
    assert ok is True
    return out


@pytest.mark.asyncio
async def test_preset_in_raw_mode_says_which_sliders_are_not_used(db):
    out = await _run("/preset buddy")
    assert "applied" not in out
    assert "Raw mode is on" in out
    for name in RAW_MODE_TONE_SLIDERS:
        assert name in out
    assert "/slider raw_mode 0" in out


@pytest.mark.asyncio
async def test_preset_with_raw_mode_off_says_applied(db):
    set_slider(db, "raw_mode", 0)
    out = await _run("/preset engineer")
    assert "applied" in out and "Raw mode" not in out


def test_raw_mode_really_ignores_exactly_those_tone_sliders():
    """The list we tell the owner matches the builder: in raw mode, changing any of them changes nothing."""
    soul = "You are Windy Fly.\nYou are witty and playful."
    base = {"autonomy": 5, **{k: 5 for k in RAW_MODE_TONE_SLIDERS}}
    same = build_personality_block(soul, base, raw=True)
    for name in RAW_MODE_TONE_SLIDERS:
        assert build_personality_block(soul, {**base, name: 0}, raw=True) == same
        assert build_personality_block(soul, {**base, name: 10}, raw=True) == same


@pytest.mark.asyncio
async def test_presets_list_matches_the_preset_table(db):
    out = await _run("/presets")
    for name, values in PRESETS.items():
        line = next(ln for ln in out.splitlines() if ln.strip().startswith(name))
        assert f"personality {values['personality']}" in line
        assert f"autonomy {values['autonomy']}" in line
    assert "Raw mode is on" in out


def test_matrix_online_line_does_not_claim_encryption_unconditionally():
    from windyfly.channels import matrix_bot

    src = inspect.getsource(matrix_bot.WindyFlyMatrixBot.start)
    assert "(E2E enabled)" not in src
    assert "ENCRYPTION_ENABLED" in src
