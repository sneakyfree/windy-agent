"""Learning loop (Sprint 3): nudge + curator + distilled corrections."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.memory.database import Database
from windyfly.skills import nudge
from windyfly.skills.curator import (
    MAX_PROMOTED_PLAYBOOKS,
    run_curation,
)


@pytest.fixture(autouse=True)
def _clean_nudge_state():
    nudge._reset_for_tests()
    yield
    nudge._reset_for_tests()


@pytest.fixture()
def db():
    d = Database(":memory:")
    yield d
    d.close()


class TestNudge:
    def test_heavy_turn_primes_one_shot_nudge(self):
        nudge.record_turn("s1", 7)
        text = nudge.pending_nudge("s1")
        assert text and "skill.save" in text and "7 tool calls" in text
        assert nudge.pending_nudge("s1") is None  # one-shot

    def test_light_turn_does_not_nudge(self):
        nudge.record_turn("s1", 2)
        assert nudge.pending_nudge("s1") is None

    def test_sessions_are_independent(self):
        nudge.record_turn("s1", 9)
        assert nudge.pending_nudge("s2") is None
        assert nudge.pending_nudge("s1") is not None

    def test_tracking_is_bounded(self):
        for i in range(nudge._MAX_TRACKED_SESSIONS + 50):
            nudge.record_turn(f"s{i}", 1)
        assert len(nudge._last_turn_tool_calls) <= nudge._MAX_TRACKED_SESSIONS


class TestCurator:
    def _add_skill(self, db, name, *, promoted=True, language="playbook",
                   uses=0, successes=0, failures=0):
        from windyfly.memory.skills import save_skill
        skill_id = save_skill(db, name, "1. do the thing\n", language)
        db.execute(
            "UPDATE skills SET promoted = ?, usage_count = ?, "
            "success_count = ?, failure_count = ?, "
            "last_used = datetime('now', ?) WHERE id = ?",
            (promoted, uses, successes, failures,
             f"-{uses} minutes", skill_id),
        )
        db.commit()
        return skill_id

    def test_failing_skill_gets_demoted(self, db):
        bad = self._add_skill(db, "bad-advice", uses=6, successes=1, failures=5)
        good = self._add_skill(db, "good-advice", uses=6, successes=5, failures=1)
        stats = run_curation(db)
        assert stats["demoted_failing"] == 1
        rows = {r["name"]: r for r in db.fetchall("SELECT name, promoted FROM skills")}
        assert not rows["bad-advice"]["promoted"]
        assert rows["good-advice"]["promoted"]

    def test_unjudged_skills_are_left_alone(self, db):
        self._add_skill(db, "new-skill", uses=1, successes=0, failures=1)
        stats = run_curation(db)
        assert stats["demoted_failing"] == 0

    def test_playbook_cap_evicts_lru(self, db):
        for i in range(MAX_PROMOTED_PLAYBOOKS + 3):
            self._add_skill(db, f"pb-{i:03d}", uses=i)
        stats = run_curation(db)
        assert stats["demoted_over_cap"] == 3
        promoted = db.fetchall(
            "SELECT name FROM skills WHERE promoted = TRUE AND language='playbook'"
        )
        assert len(promoted) == MAX_PROMOTED_PLAYBOOKS


class TestDistilledCorrections:
    FRICTION = {
        "fault_type": "wrong_date",
        "user_message": "no, the meeting is TUESDAY not monday",
        "agent_message": "Your meeting is on Monday at 3pm.",
        "pattern_matched": "no,",
    }

    def test_kill_switch_returns_none(self, monkeypatch):
        from windyfly.agent.failure_detector import _distill_correction_code
        monkeypatch.setenv("WINDY_LLM_CORRECTIONS", "0")
        assert _distill_correction_code("wrong_date", self.FRICTION) is None

    def test_distilled_lesson_round_trips_through_extractor(self, monkeypatch):
        from windyfly.agent.failure_detector import _distill_correction_code
        from windyfly.memory.skills import extract_correction_text

        monkeypatch.setenv("WINDY_LLM_CORRECTIONS", "1")
        with patch(
            "windyfly.agent.models.call_llm",
            return_value={"content":
                          "Re-read the user's stated weekday and confirm "
                          "it before repeating any date."},
        ):
            code = _distill_correction_code("wrong_date", self.FRICTION)
        assert code is not None
        lesson = extract_correction_text(code)
        assert lesson and "weekday" in lesson

    def test_llm_failure_falls_back_to_template(self, monkeypatch, db):
        from windyfly.agent import failure_detector as fd

        monkeypatch.setenv("WINDY_LLM_CORRECTIONS", "1")
        with patch(
            "windyfly.agent.models.call_llm",
            side_effect=RuntimeError("provider down"),
        ):
            code = fd._distill_correction_code("wrong_date", self.FRICTION)
        assert code is None  # caller falls back to _build_correction_code
        template = fd._build_correction_code("wrong_date", self.FRICTION)
        from windyfly.memory.skills import extract_correction_text
        assert extract_correction_text(template)

    def test_garbage_llm_output_rejected(self, monkeypatch):
        from windyfly.agent.failure_detector import _distill_correction_code
        monkeypatch.setenv("WINDY_LLM_CORRECTIONS", "1")
        with patch(
            "windyfly.agent.models.call_llm",
            return_value={"content": "ok"},
        ):
            assert _distill_correction_code("x", self.FRICTION) is None
