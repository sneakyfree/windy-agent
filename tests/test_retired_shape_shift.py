"""Shape-shift (Phase 6) and the sub-agent module are retired (2026-10-10).

A smart model changes its approach when asked; it doesn't need a tool
that swaps slider presets in and out. These tests pin two things:

  1. The retired surface is really gone (modules, bridge methods,
     the ``shape_shift_bias`` slider).
  2. An existing database that still holds a ``slider_shape_shift_bias``
     soul row keeps working everywhere sliders are read: it is ignored
     quietly, never shown, never billed, never breaks preset detection.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

from windyfly.control_panel import (
    PRESETS,
    SLIDER_INFO,
    VALID_SLIDERS,
    apply_preset,
    estimate_monthly_cost,
    get_slider_info,
    get_sliders,
)
from windyfly.memory.database import Database
from windyfly.memory.soul import upsert_soul
from windyfly.memory.write_queue import WriteQueue

RETIRED = "shape_shift_bias"


def _leftover_row(db: Database, value: str = "8") -> None:
    upsert_soul(db, key=f"slider_{RETIRED}", value=value, source="control_panel")


# ── the retired surface is gone ────────────────────────────────────


@pytest.mark.parametrize("module", [
    "windyfly.agent.shape_shift",
    "windyfly.agent.sub_agents",
])
def test_retired_modules_are_gone(module):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(module)


def test_slider_is_gone_everywhere():
    assert RETIRED not in VALID_SLIDERS
    assert RETIRED not in SLIDER_INFO
    for name, values in PRESETS.items():
        assert RETIRED not in values, name


@pytest.mark.parametrize("method", ["shape_shift.execute", "shape_shift.restore"])
def test_bridge_no_longer_dispatches_shape_shift(method):
    from windyfly.bridge.uds_server import UDSBridge

    db = Database(":memory:")
    bridge = UDSBridge({"agent": {"default_model": "gpt-4o-mini"}}, db, WriteQueue())
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(ValueError, match="Unknown method"):
            loop.run_until_complete(bridge._dispatch(method, {}))
    finally:
        loop.close()
        db.close()


# ── a leftover soul row is ignored quietly ─────────────────────────


def test_get_sliders_ignores_leftover_row():
    db = Database(":memory:")
    _leftover_row(db)
    sliders = get_sliders(db)
    assert RETIRED not in sliders
    assert set(sliders) == VALID_SLIDERS
    db.close()


def test_slider_info_ignores_leftover_row():
    db = Database(":memory:")
    _leftover_row(db)
    info = get_slider_info(db)
    assert RETIRED not in info
    assert set(info) == VALID_SLIDERS
    db.close()


def test_cost_estimate_ignores_unknown_key():
    """Even if a caller passes the old key in, it costs nothing and
    doesn't raise."""
    base = estimate_monthly_cost({"humor": 5})
    with_old = estimate_monthly_cost({"humor": 5, RETIRED: 10})
    assert with_old["estimated_usd"] == base["estimated_usd"]


def test_preset_detection_survives_leftover_row():
    from windyfly.dashboard.data import _get_personality_stats

    db = Database(":memory:")
    apply_preset(db, "buddy")
    _leftover_row(db, "3")  # a value the old buddy preset never had
    stats = _get_personality_stats(db, "default")
    assert stats["preset"] == "buddy"
    assert RETIRED not in stats["sliders"]
    db.close()


def test_bridge_sliders_ignore_leftover_row():
    from windyfly.bridge.uds_server import UDSBridge

    db = Database(":memory:")
    _leftover_row(db)
    bridge = UDSBridge({"agent": {"default_model": "gpt-4o-mini"}}, db, WriteQueue())
    loop = asyncio.new_event_loop()
    try:
        got = loop.run_until_complete(bridge._dispatch("sliders.get", {}))
        info = loop.run_until_complete(bridge._dispatch("sliders.info", {}))
    finally:
        loop.close()
        db.close()
    assert RETIRED not in got["sliders"]
    assert RETIRED not in info["sliders"]


@pytest.mark.asyncio
async def test_sliders_slash_command_ignores_leftover_row():
    from windyfly.channels.base import handle_incoming
    from windyfly.commands.core import wire_runtime
    from windyfly.commands.setup import init_all_commands

    db = Database(":memory:")
    init_all_commands(db=db, config={})
    wire_runtime(db=db)
    _leftover_row(db)
    try:
        ok, out = await handle_incoming(
            "/sliders", {"platform": "telegram", "channel_id": "x"},
        )
    finally:
        db.close()
    assert ok is True
    assert "Personality Sliders" in out
    assert RETIRED not in out


def test_windy_soul_sliders_cli_hides_leftover_row(tmp_path, monkeypatch, capsys):
    from windyfly.commands import _legacy

    db_path = tmp_path / "windyfly.db"
    db = Database(str(db_path))
    upsert_soul(db, key="slider_humor", value="7", source="control_panel")
    _leftover_row(db)
    db.close()

    monkeypatch.setattr(_legacy, "_get_db_path", lambda: db_path)
    _legacy._soul_sliders()
    out = capsys.readouterr().out
    assert "Humor" in out
    assert "Shape" not in out
