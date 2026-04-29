"""Cadence regressions for the mid-week red-alarm.

The alarm fires twice a week (Wed + Fri 09:00) by default. To
guarantee it doesn't spam grandma, the firing logic depends on
snapshot comparisons:

  - All green / no change  → silent
  - Improvement            → silent
  - Sustained red          → fires (deliberate — grandma needs the
                             reminder if she missed Wednesday's
                             alarm by Friday)
  - New red appears        → fires
  - Yellow → red trans     → fires (degradation continues)
  - Green → yellow trans   → fires (early warning)

These tests pin every cell of that decision matrix so a future
refactor can't accidentally start spamming grandma OR start hiding
real degradations.
"""

from __future__ import annotations

import pytest

from windyfly.agent.capabilities.health import should_fire_alarm


def _snap(verdicts: dict[str, str], run_id: str = "x") -> dict:
    return {
        "run_id": run_id,
        "ts": "2026-04-29T09:00:00Z",
        "real_llm": False,
        "verdict_counts": {
            "green":  sum(1 for v in verdicts.values() if v == "green"),
            "yellow": sum(1 for v in verdicts.values() if v == "yellow"),
            "red":    sum(1 for v in verdicts.values() if v == "red"),
        },
        "organs": {organ: {"verdict": v, "detail": ""} for organ, v in verdicts.items()},
    }


# ── Silent paths ───────────────────────────────────────────────────


class TestSilent:
    def test_no_snapshots_silent(self):
        """No data yet → silent. Don't alarm grandma about nothing."""
        assert should_fire_alarm([]) == {}

    def test_first_snapshot_all_green_silent(self):
        snaps = [_snap({"brain": "green", "memory": "green"})]
        assert should_fire_alarm(snaps) == {}

    def test_unchanged_all_green_silent(self):
        prev = _snap({"brain": "green", "memory": "green"}, "prev")
        cur = _snap({"brain": "green", "memory": "green"}, "cur")
        assert should_fire_alarm([prev, cur]) == {}

    def test_red_to_green_recovery_silent(self):
        """User /reset'd between snapshots and recovered. SILENT —
        we don't pat ourselves on the back; the Sunday brief covers
        improvement."""
        prev = _snap({"memory": "red"}, "prev")
        cur = _snap({"memory": "green"}, "cur")
        assert should_fire_alarm([prev, cur]) == {}

    def test_yellow_to_green_silent(self):
        prev = _snap({"heart": "yellow"}, "prev")
        cur = _snap({"heart": "green"}, "cur")
        assert should_fire_alarm([prev, cur]) == {}

    def test_sustained_yellow_silent(self):
        """Yellow on yellow with no change → silent. The Sunday
        brief catches sustained yellow; the alarm is for ESCALATION
        and currently-red. Avoids spamming grandma every Wed and Fri
        for the same yellow she already knows about."""
        prev = _snap({"voice": "yellow"}, "prev")
        cur = _snap({"voice": "yellow"}, "cur")
        assert should_fire_alarm([prev, cur]) == {}


# ── Firing paths ───────────────────────────────────────────────────


class TestFiring:
    def test_first_snapshot_with_red_fires(self):
        """First snapshot, no prior to compare → fire if red present.
        Grandma needs to know even on day 1 if something is wrong."""
        snaps = [_snap({"memory": "red"})]
        decision = should_fire_alarm(snaps)
        assert decision.get("fire") is True
        assert "memory" in decision["current_red"]

    def test_new_red_appears_fires(self):
        prev = _snap({"memory": "green"}, "prev")
        cur = _snap({"memory": "red"}, "cur")
        decision = should_fire_alarm([prev, cur])
        assert decision["fire"] is True
        assert decision["current_red"] == ["memory"]

    def test_green_to_yellow_fires_early_warning(self):
        """Yellow transition is an early warning — alarm fires once
        on the transition. Subsequent sustained-yellow runs go silent
        (test_sustained_yellow_silent)."""
        prev = _snap({"heart": "green"}, "prev")
        cur = _snap({"heart": "yellow"}, "cur")
        decision = should_fire_alarm([prev, cur])
        assert decision["fire"] is True
        assert any(t["organ"] == "heart" for t in decision["transitions"])

    def test_yellow_to_red_fires(self):
        """Degradation continues — escalate alarm."""
        prev = _snap({"memory": "yellow"}, "prev")
        cur = _snap({"memory": "red"}, "cur")
        decision = should_fire_alarm([prev, cur])
        assert decision["fire"] is True
        assert decision["current_red"] == ["memory"]

    def test_sustained_red_fires_again(self):
        """User missed Wednesday's alarm → Friday repeats. By design.
        If she's not /resetting, she might not have seen the first
        alarm. Better to nudge twice than miss her entirely."""
        prev = _snap({"memory": "red"}, "prev")
        cur = _snap({"memory": "red"}, "cur")
        decision = should_fire_alarm([prev, cur])
        assert decision["fire"] is True
        assert decision["current_red"] == ["memory"]
        # No new transition — but currently-red rule fires anyway.
        assert decision["transitions"] == []

    def test_multiple_organs_degrading_all_listed(self):
        prev = _snap(
            {"memory": "green", "heart": "green", "voice": "green"},
            "prev",
        )
        cur = _snap(
            {"memory": "red", "heart": "yellow", "voice": "yellow"},
            "cur",
        )
        decision = should_fire_alarm([prev, cur])
        assert decision["fire"] is True
        assert decision["current_red"] == ["memory"]
        transition_organs = {t["organ"] for t in decision["transitions"]}
        # heart and voice are transitions; memory is also a
        # green→red transition (so it appears in BOTH current_red
        # AND transitions). The format script dedups via
        # reported_organs.
        assert {"heart", "voice"}.issubset(transition_organs)


# ── Edge cases ────────────────────────────────────────────────────


class TestEdges:
    def test_missing_organ_in_prev_treated_as_green(self):
        """Prev snapshot doesn't have an organ that cur does. Treat
        as if it were green — so a NEW yellow on a NEW organ counts
        as degradation."""
        prev = _snap({"memory": "green"}, "prev")
        cur = _snap({"memory": "green", "voice": "yellow"}, "cur")
        decision = should_fire_alarm([prev, cur])
        assert decision["fire"] is True
        assert any(t["organ"] == "voice" for t in decision["transitions"])

    def test_missing_organ_in_cur_does_not_fire(self):
        """Cur snapshot is missing an organ that prev had. Cur is
        the source of truth — we don't fire on absence."""
        prev = _snap({"memory": "red", "voice": "green"}, "prev")
        cur = _snap({"voice": "green"}, "cur")  # memory is gone
        # No red in cur, no degradation — silent.
        assert should_fire_alarm([prev, cur]) == {}

    def test_unknown_verdict_in_cur_ignored(self):
        """A verdict outside green/yellow/red shouldn't crash the
        comparator. Silent treatment is the safe default."""
        prev = _snap({"memory": "green"}, "prev")
        cur = {
            "run_id": "cur",
            "organs": {"memory": {"verdict": "purple"}},
        }
        # No crash. May or may not fire — just verify shape.
        result = should_fire_alarm([prev, cur])
        assert isinstance(result, dict)

    def test_explicit_empty_organs_silent(self):
        """A snapshot with an empty organs dict is silent — no data
        to compare."""
        prev = _snap({"memory": "green"}, "prev")
        cur = {"run_id": "cur", "organs": {}}
        assert should_fire_alarm([prev, cur]) == {}
