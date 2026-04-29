"""Regressions for health.weekly_brief — the agent's self-assessment.

Layer 1 of the proposing-but-not-applying self-improvement design.
Tests verify that:
  - Empty health dir returns a friendly "no data yet" report
  - All-green snapshots produce no recommendations + healthy headline
  - Yellow organs produce diagnosis + recommendation + user_action
  - Red organs always recommend /reset as the first response
  - Persistent decline across 3 snapshots adds a trend_note
  - The reset_hint and applying_changes_hint are ALWAYS included
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from windyfly.agent.capabilities import (
    Band,
    CapabilityRegistry,
)
from windyfly.agent.capabilities.health import register_health_capabilities


@pytest.fixture
def reg(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_HEALTH_DIR", str(tmp_path))
    r = CapabilityRegistry()
    register_health_capabilities(r, config={})
    return r, tmp_path


def _scorecard(run_id: str, organs: dict) -> dict:
    counts = {
        "green":  sum(1 for v in organs.values() if v["verdict"] == "green"),
        "yellow": sum(1 for v in organs.values() if v["verdict"] == "yellow"),
        "red":    sum(1 for v in organs.values() if v["verdict"] == "red"),
    }
    return {
        "run_id": run_id,
        "ts": f"2026-04-{run_id[:2]}T12:00:00Z",
        "real_llm": False,
        "model": "claude-haiku-4-5",
        "turns": 25,
        "verdict_counts": counts,
        "organs": organs,
    }


@pytest.mark.asyncio
async def test_empty_returns_no_data_recommendation(reg):
    r, _ = reg
    result = await r.invoke("health.weekly_brief", {}, Band.OWNER)
    assert result["ok"] is True
    assert result["snapshot_count"] == 0
    assert len(result["recommendations"]) == 1
    assert result["recommendations"][0]["verdict"] == "no_data"
    assert "reset_hint" in result
    assert "applying_changes_hint" in result


@pytest.mark.asyncio
async def test_all_green_no_recommendations(reg):
    r, health_dir = reg
    organs = {
        "brain":  {"verdict": "green", "detail": "ok"},
        "memory": {"verdict": "green", "detail": "ok"},
        "voice":  {"verdict": "green", "detail": "ok"},
    }
    (health_dir / "01.json").write_text(json.dumps(_scorecard("01", organs)))
    result = await r.invoke("health.weekly_brief", {}, Band.OWNER)
    assert result["ok"] is True
    assert result["recommendations"] == []
    assert "smoothly" in result["headline"].lower()


@pytest.mark.asyncio
async def test_yellow_organ_produces_recommendation(reg):
    r, health_dir = reg
    organs = {
        "brain":  {"verdict": "green", "detail": "ok"},
        "memory": {"verdict": "yellow", "detail": "recall 60%"},
    }
    (health_dir / "01.json").write_text(json.dumps(_scorecard("01", organs)))
    result = await r.invoke("health.weekly_brief", {}, Band.OWNER)
    assert len(result["recommendations"]) == 1
    rec = result["recommendations"][0]
    assert rec["organ"] == "memory"
    assert rec["verdict"] == "yellow"
    assert "diagnosis" in rec
    assert "recommendation" in rec
    assert "user_action" in rec
    assert rec["blast_radius"] == "low"
    assert "off this week" in result["headline"].lower()


@pytest.mark.asyncio
async def test_red_organ_recommends_reset_first(reg):
    """Red verdicts MUST mention /reset in the user_action — that's
    the user's universal escape hatch."""
    r, health_dir = reg
    organs = {
        "memory": {"verdict": "red", "detail": "recall 10%"},
        "immune": {"verdict": "red", "detail": "drift detected"},
    }
    (health_dir / "01.json").write_text(json.dumps(_scorecard("01", organs)))
    result = await r.invoke("health.weekly_brief", {}, Band.OWNER)
    assert len(result["recommendations"]) >= 1
    for rec in result["recommendations"]:
        assert "/reset" in rec["user_action"].lower() or "/reset" in rec["recommendation"].lower()
    assert "tough time" in result["headline"].lower()


@pytest.mark.asyncio
async def test_consistent_decline_adds_trend_note(reg):
    """3 snapshots showing green → yellow → red on the same organ
    must be flagged as a degrading trend in the recommendation."""
    r, health_dir = reg
    snapshots = [
        ("01", {"memory": {"verdict": "green",  "detail": "ok"}}),
        ("02", {"memory": {"verdict": "yellow", "detail": "60%"}}),
        ("03", {"memory": {"verdict": "red",    "detail": "20%"}}),
    ]
    for rid, organs in snapshots:
        (health_dir / f"{rid}.json").write_text(
            json.dumps(_scorecard(rid, organs))
        )
    result = await r.invoke("health.weekly_brief", {}, Band.OWNER)
    rec = next(
        r for r in result["recommendations"] if r["organ"] == "memory"
    )
    assert "trend_note" in rec
    assert "degrad" in rec["trend_note"].lower()


@pytest.mark.asyncio
async def test_reset_hint_always_present(reg):
    """The reset hint is the user's safety net — must appear on
    EVERY brief, regardless of state."""
    r, _ = reg
    result = await r.invoke("health.weekly_brief", {}, Band.OWNER)
    assert "reset" in result["reset_hint"].lower()
    assert "memory" in result["reset_hint"].lower() or "safe" in result["reset_hint"].lower()


@pytest.mark.asyncio
async def test_brief_does_not_mutate_anything(reg, monkeypatch):
    """The weekly brief is observe-only. Calling it must not modify
    any health snapshot file."""
    r, health_dir = reg
    organs = {"memory": {"verdict": "yellow", "detail": "60%"}}
    snapshot_path = health_dir / "01.json"
    snapshot_path.write_text(json.dumps(_scorecard("01", organs)))
    before = snapshot_path.read_text()
    files_before = sorted(p.name for p in health_dir.iterdir())

    await r.invoke("health.weekly_brief", {}, Band.OWNER)

    after = snapshot_path.read_text()
    files_after = sorted(p.name for p in health_dir.iterdir())
    assert before == after, "weekly_brief must not mutate snapshots"
    assert files_before == files_after, "weekly_brief must not create files"
