"""Strand v2, Part 4 §11 ecosystem map, §12 master file index and checklist C2 (docs/strand/STRAND_V2.md).
Repo files are read from the checkout; no network, no home."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from windyfly.control_panel import PRESETS, VALID_SLIDERS, apply_preset, estimate_monthly_cost, get_sliders
from windyfly.memory.database import Database

ROOT = Path(__file__).resolve().parents[2]
S = "src/windyfly/"


def tracked(path: str) -> bool:
    out = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                         capture_output=True, text=True)
    return out.returncode == 0


# ── §11 map: the brain's parts (their behaviour is graded in Parts 1-3) ─────────────────────────

def test_p4_11_the_brain_parts_in_the_map_exist():
    parts = [S + f for f in ("agent/loop.py", "memory/database.py", "skills/manager.py",
                             "control_panel.py", "channels/matrix_bot.py", "bridge/uds_server.py")]
    missing = [p for p in parts if not tracked(p)]
    assert not missing, missing


# ── §12 master file index (files removed on purpose are retired rows, not listed here) ──────────

INDEX = {
    "phase0": ["pyproject.toml", "windyfly.toml", "SOUL.md"] + [S + f for f in (
        "config.py", "main.py", "memory/database.py", "memory/episodes.py", "memory/nodes.py",
        "memory/soul.py", "memory/skills.py", "memory/failures.py", "memory/cost_ledger.py",
        "memory/write_queue.py", "personality/engine.py", "agent/models.py", "agent/prompt.py",
        "agent/loop.py", "channels/cli.py")],
    "phase1": [S + f for f in ("channels/matrix_bot.py", "tools/registry.py", "tools/windy_api.py")],
    "phase2": [S + f for f in (
        "soul_import/openclaw.py", "soul_import/hermes.py", "soul_import/chatgpt.py",
        "soul_import/orchestrator.py", "soul_import/preview.py", "control_panel.py",
        "agent/failure_detector.py")],
    "phase3": [S + f for f in (
        "skills/manager.py", "memory/cost_tracker.py", "memory/intents.py",
        "agent/intent_detector.py", "agent/emotion_detector.py")],
    "phase4": [S + f for f in (
        "bridge/uds_server.py", "memory/decay.py", "memory/conflict_detector.py", "agent/offline.py")]
              + ["gateway/src/bridge.ts", "gateway/src/server.ts", "gateway/src/websocket.ts"],
    "phase5": [S + f for f in (
        "dashboard/data.py", "personality/versioning.py", "observability/events.py")],
}


@pytest.mark.parametrize("phase", sorted(INDEX))
def test_p4_12_index(phase):
    missing = [p for p in INDEX[phase] if not tracked(p)]
    assert not missing, missing


# ── checklist C2.9: the monthly estimate is broken down by slider ───────────────────────────────

def test_p4_c2_9_estimate_breaks_down_by_every_slider_and_adds_up():
    for name, values in PRESETS.items():
        cost = estimate_monthly_cost(values)
        assert set(cost["breakdown"]) == set(values) <= set(VALID_SLIDERS), name
        assert cost["estimated_usd"] > 0, name
        assert abs(sum(cost["breakdown"].values()) - cost["estimated_usd"]) < 0.05, name


# ── checklist C2.7: the strand's three presets (and the newer ones) apply all their values ───────────────────────────────

def test_p4_c2_7_each_preset_applies_all_its_values():
    assert {"buddy", "engineer", "powerhouse"} <= set(PRESETS)          # the strand's three, and more
    for name, values in PRESETS.items():
        db = Database(":memory:")
        try:
            apply_preset(db, name)
            now = get_sliders(db)
        finally:
            db.close()
        assert {k: now[k] for k in values} == values, name


# ── footer: the strand's Part 5 is kept ─────────────────────────────────────────────────────────

def test_p4_f_part5_of_the_strand_is_kept():
    assert tracked("docs/WINDY_FLY_DNA_STRAND_PART5.md")
