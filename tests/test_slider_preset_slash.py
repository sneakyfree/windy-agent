"""``/sliders`` + ``/slider`` + ``/preset`` slash commands.

All three commands have been silently broken since the file
``commands/core.py`` shipped:

  - ``/sliders`` imported ``get_all_sliders`` from
    ``control_panel`` — that name has never existed; the actual
    function is ``get_sliders``. Every invocation raised
    ImportError caught by the bare ``except`` and surfaced
    "Error loading sliders: cannot import name 'get_all_sliders'"
  - ``/preset <name>`` called ``apply_preset(_db, None, args[0])``
    but the signature is ``apply_preset(db, preset_name,
    user_id="default")``. So ``preset_name=None`` and
    ``user_id=<the-actual-preset-name>`` — always raised
    "Unknown preset 'None'".
  - ``/slider set <name> <value>`` called ``set_slider(_db, None,
    name, float(value))`` but the signature is
    ``set_slider(db, slider_name, value, user_id="default")``. So
    ``slider_name=None, value=<name>, user_id=<value>`` — always
    raised "Unknown slider 'None'".

Surfaced 2026-05-20 when Grant asked "is there a slash command
to modify all the bot slider selections?" — yes, but they've
never worked.

These tests pin the contract end-to-end through the unified
dispatcher (``handle_incoming``) so a future regression that
breaks the wiring fails loudly instead of silently.
"""

from __future__ import annotations

import pytest

from windyfly.channels.base import handle_incoming
from windyfly.commands.setup import init_all_commands
from windyfly.memory.database import Database


@pytest.fixture
def bootstrapped_db():
    """Spin up a fresh DB + register all slash commands + wire
    runtime — same boot sequence the real bot uses."""
    from windyfly.commands.core import wire_runtime
    db = Database(":memory:")
    init_all_commands(db=db, config={})
    wire_runtime(db=db)
    yield db
    db.close()


@pytest.mark.asyncio
async def test_sliders_command_works(bootstrapped_db):
    """/sliders should render a bar chart of all sliders, not
    raise ImportError on the wrong function name."""
    ok, out = await handle_incoming(
        "/sliders", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "Personality Sliders" in out
    assert "█" in out  # bar character
    # Should NOT have the old import-error message
    assert "cannot import name" not in out
    assert "get_all_sliders" not in out
    # Default sliders are 5/10
    assert "5/10" in out


@pytest.mark.asyncio
async def test_preset_buddy_actually_applies(bootstrapped_db):
    """/preset buddy must call apply_preset with the right args,
    not pass None and stuff the preset name into user_id."""
    ok, out = await handle_incoming(
        "/preset buddy", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    # Should NOT be the "Unknown preset 'None'" old bug
    assert "Unknown preset 'None'" not in out
    assert "buddy" in out and ("applied" in out.lower() or "✅" in out)

    # Verify state actually changed: query sliders
    ok, sliders_out = await handle_incoming(
        "/sliders", {"platform": "telegram", "channel_id": "x"},
    )
    # buddy preset sets humor=7
    assert "humor" in sliders_out
    assert "7/10" in sliders_out  # at least one slider is 7 (humor)


@pytest.mark.asyncio
async def test_slider_set_individual_slider(bootstrapped_db):
    """/slider humor 8 should set humor to 8, not raise about a
    None slider name."""
    ok, out = await handle_incoming(
        "/slider humor 8", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "Unknown slider 'None'" not in out
    assert "humor" in out and "8" in out

    ok, sliders_out = await handle_incoming(
        "/sliders", {"platform": "telegram", "channel_id": "x"},
    )
    # humor row should show 8/10
    humor_line = [line for line in sliders_out.split("\n") if "humor" in line]
    assert humor_line
    assert "8/10" in humor_line[0]


@pytest.mark.asyncio
async def test_slider_set_accepts_legacy_set_syntax(bootstrapped_db):
    """Docstring shipped with `/slider set humor 8` — keep that
    working alongside the new shorter `/slider humor 8`."""
    ok, out = await handle_incoming(
        "/slider set verbosity 9", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "verbosity" in out and "9" in out


@pytest.mark.asyncio
async def test_slider_set_rejects_unknown_slider(bootstrapped_db):
    """A typo'd slider name should produce a sane error, not a
    silent set."""
    ok, out = await handle_incoming(
        "/slider banana 5", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "banana" in out.lower() or "unknown" in out.lower() or "must be one of" in out.lower()


@pytest.mark.asyncio
async def test_preset_rejects_unknown_preset(bootstrapped_db):
    ok, out = await handle_incoming(
        "/preset nonexistent", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "nonexistent" in out or "unknown" in out.lower()


@pytest.mark.asyncio
async def test_bare_slider_shows_usage(bootstrapped_db):
    ok, out = await handle_incoming(
        "/slider", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "Usage" in out or "usage" in out.lower()


@pytest.mark.asyncio
async def test_bare_preset_lists_available(bootstrapped_db):
    ok, out = await handle_incoming(
        "/preset", {"platform": "telegram", "channel_id": "x"},
    )
    assert ok is True
    assert "buddy" in out
    assert "engineer" in out
