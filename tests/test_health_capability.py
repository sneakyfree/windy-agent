"""Regressions for health.recent / health.trend.

Ring 1 of the recursive-self-improvement architecture. The bot can
read its own organ scorecards but cannot mutate them — these tests
lock that boundary and verify the read path is robust to missing /
malformed snapshot files.
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
def registry_with_health(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_HEALTH_DIR", str(tmp_path))
    reg = CapabilityRegistry()
    register_health_capabilities(reg, config={})
    return reg, tmp_path


def _scorecard(run_id: str, organs: dict, real_llm: bool = False) -> dict:
    counts = {
        "green":  sum(1 for v in organs.values() if v["verdict"] == "green"),
        "yellow": sum(1 for v in organs.values() if v["verdict"] == "yellow"),
        "red":    sum(1 for v in organs.values() if v["verdict"] == "red"),
    }
    return {
        "run_id": run_id,
        "ts": "2026-04-29T16:00:00Z",
        "real_llm": real_llm,
        "model": "claude-haiku-4-5",
        "turns": 25,
        "elapsed_total_s": 120.0,
        "verdict_counts": counts,
        "organs": organs,
    }


@pytest.mark.asyncio
async def test_recent_with_no_snapshots_returns_ok_false(registry_with_health):
    reg, _ = registry_with_health
    result = await reg.invoke("health.recent", {}, Band.OWNER)
    assert result["ok"] is False
    assert "no scorecards" in result["reason"].lower()


@pytest.mark.asyncio
async def test_recent_returns_latest_snapshot(registry_with_health):
    reg, health_dir = registry_with_health
    organs = {
        "brain": {"verdict": "green", "detail": "ok"},
        "memory": {"verdict": "green", "detail": "48 eps"},
        "immune": {"verdict": "green", "detail": "0 drift"},
    }
    snap = _scorecard("20260429T161315Z", organs)
    (health_dir / "20260429T161315Z.json").write_text(json.dumps(snap))

    result = await reg.invoke("health.recent", {}, Band.OWNER)
    assert result["ok"] is True
    assert result["latest"]["run_id"] == "20260429T161315Z"
    assert result["latest"]["verdict_counts"]["green"] == 3
    assert result["regression_count"] == 0  # only 1 snapshot, no comparison


@pytest.mark.asyncio
async def test_recent_detects_regression(registry_with_health):
    reg, health_dir = registry_with_health
    prev_organs = {
        "brain": {"verdict": "green", "detail": "ok"},
        "memory": {"verdict": "green", "detail": "ok"},
    }
    cur_organs = {
        "brain": {"verdict": "green", "detail": "ok"},
        "memory": {"verdict": "yellow", "detail": "recall 60%"},  # ← regression
    }
    (health_dir / "20260420T120000Z.json").write_text(
        json.dumps(_scorecard("20260420T120000Z", prev_organs))
    )
    (health_dir / "20260427T120000Z.json").write_text(
        json.dumps(_scorecard("20260427T120000Z", cur_organs))
    )

    result = await reg.invoke("health.recent", {}, Band.OWNER)
    assert result["ok"] is True
    assert result["regression_count"] == 1
    regr = result["regressions_since_previous"][0]
    assert regr["organ"] == "memory"
    assert regr["previous"] == "green"
    assert regr["current"] == "yellow"


@pytest.mark.asyncio
async def test_trend_returns_chronological_list(registry_with_health):
    reg, health_dir = registry_with_health
    for i, run_id in enumerate(["a", "b", "c"]):
        (health_dir / f"{run_id}.json").write_text(
            json.dumps(_scorecard(run_id, {
                "brain": {"verdict": "green", "detail": "ok"},
            }))
        )
    result = await reg.invoke("health.trend", {"limit": 5}, Band.OWNER)
    assert result["ok"] is True
    assert result["count"] == 3
    # Sorted by filename → chronological
    assert [t["run_id"] for t in result["trend"]] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_trend_with_no_snapshots(registry_with_health):
    reg, _ = registry_with_health
    result = await reg.invoke("health.trend", {"limit": 5}, Band.OWNER)
    assert result["ok"] is False


@pytest.mark.asyncio
async def test_malformed_snapshot_skipped_not_raised(registry_with_health):
    """A torn / hand-edited snapshot file must not crash the read."""
    reg, health_dir = registry_with_health
    (health_dir / "broken.json").write_text("not valid json {{{ ")
    (health_dir / "good.json").write_text(json.dumps(
        _scorecard("good", {"brain": {"verdict": "green", "detail": "ok"}})
    ))
    result = await reg.invoke("health.recent", {}, Band.OWNER)
    assert result["ok"] is True
    assert result["latest"]["run_id"] == "good"


@pytest.mark.asyncio
async def test_trend_limit_respected(registry_with_health):
    reg, health_dir = registry_with_health
    for i in range(20):
        (health_dir / f"r{i:02d}.json").write_text(json.dumps(
            _scorecard(f"r{i:02d}", {"brain": {"verdict": "green", "detail": "ok"}})
        ))
    result = await reg.invoke("health.trend", {"limit": 5}, Band.OWNER)
    assert result["count"] == 5
    # Returns the LAST 5 (most recent)
    assert [t["run_id"] for t in result["trend"]] == [
        "r15", "r16", "r17", "r18", "r19",
    ]
