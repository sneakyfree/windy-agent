"""Correction skills must be APPLIED, not just created.

PR #211 fixed the creation half of the self-improvement pipeline.
This PR fixes the application half: auto-promote correction
skills on creation + inject the promoted ones into the system
prompt under "## Lessons learned from past corrections" so the
bot proactively avoids past mistake patterns on future turns.

Without this PR, correction skills accumulated in the DB but the
agent loop never read them — the "evolves over time" claim was
silently no-op'd from launch.

Tests pin:
  - ``get_active_correction_skills`` returns ONLY promoted +
    correction-named skills, capped at ``limit``
  - ``extract_correction_text`` parses the ``CORRECTION = (...)``
    block from auto-generated skill code (without exec)
  - ``handle_friction`` auto-promotes correction skills on
    recurring detection (so they're immediately applicable)
  - ``assemble_prompt`` injects a "Lessons learned" system block
    when promoted correction skills exist
  - The block is suppressed when no skills exist (no noise)
"""

from __future__ import annotations

import time

import pytest

from windyfly.agent.failure_detector import handle_friction
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.skills import (
    extract_correction_text,
    get_active_correction_skills,
    save_skill,
)
from windyfly.memory.write_queue import WriteQueue
from windyfly.skills.manager import promote_skill


# ── extract_correction_text ──────────────────────────────────────


class TestExtractCorrectionText:

    def test_extracts_from_generated_code(self):
        code = (
            "# Auto-generated correction skill for: factual_error\n"
            "FAULT_TYPE = 'factual_error'\n"
            "CORRECTION = (\n"
            "    'When handling factual_error situations, '\n"
            "    'double-check facts and acknowledge user corrections promptly.'\n"
            ")\n"
        )
        out = extract_correction_text(code)
        assert out == (
            "When handling factual_error situations, "
            "double-check facts and acknowledge user corrections promptly."
        )

    def test_returns_none_on_malformed(self):
        assert extract_correction_text("not a skill") is None
        assert extract_correction_text("") is None
        assert extract_correction_text("CORRECTION = 'bare string'") is None

    def test_does_not_exec_code(self):
        """Even if the skill body contains import os; os.system(...),
        we must not execute it — extract_correction_text is text-
        parsing only."""
        malicious = (
            "CORRECTION = (\n"
            "    'legitimate'\n"
            ")\n"
            "import os\n"
            "os.system('echo PWNED')\n"
        )
        out = extract_correction_text(malicious)
        # Should extract the string AND not execute the os.system
        assert out == "legitimate"


# ── get_active_correction_skills ─────────────────────────────────


@pytest.fixture
def db():
    d = Database(":memory:")
    yield d
    d.close()


def test_filters_to_promoted_correction_skills(db):
    # Promoted correction → included
    sid1 = save_skill(db, "correction-factual_error", "code", "python")
    promote_skill(db, sid1)
    # Unpromoted correction → excluded
    save_skill(db, "correction-preference_miss", "code", "python")
    # Non-correction promoted skill → excluded
    sid3 = save_skill(db, "other-skill", "code", "python")
    promote_skill(db, sid3)

    out = get_active_correction_skills(db, limit=10)
    names = {s["name"] for s in out}
    assert names == {"correction-factual_error"}


def test_limit_enforced(db):
    for i in range(10):
        sid = save_skill(db, f"correction-type{i}", "code", "python")
        promote_skill(db, sid)
    out = get_active_correction_skills(db, limit=3)
    assert len(out) == 3


# ── handle_friction auto-promotes ────────────────────────────────


def test_handle_friction_auto_promotes_recurring(db):
    wq = WriteQueue()
    wq.start()
    try:
        # 1st correction — no skill yet
        handle_friction(db, wq, {
            "fault_type": "factual_error",
            "user_message": "No, that's wrong about A",
            "agent_message": "A is X",
            "pattern_matched": "p",
        })
        time.sleep(0.3)
        # 2nd correction same fault_type → recurring → skill created
        handle_friction(db, wq, {
            "fault_type": "factual_error",
            "user_message": "No, that's wrong about B",
            "agent_message": "B is Y",
            "pattern_matched": "p",
        })
        time.sleep(0.3)

        # The created correction skill should be PROMOTED so the
        # injection path picks it up on the next turn.
        promoted = get_active_correction_skills(db)
        assert len(promoted) == 1
        assert promoted[0]["name"] == "correction-factual_error"
        assert promoted[0]["promoted"] == 1  # SQLite bool stored as int
    finally:
        wq.stop()


# ── Prompt injection integration ─────────────────────────────────


def _make_config():
    return {
        "agent": {"default_model": "claude-haiku-4-5-20251001",
                  "max_context_tokens": 8000, "max_response_tokens": 1024,
                  "temperature": 0.5},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 5,
                   "max_nodes_per_context": 5},
        "personality": {"soul_path": "SOUL.md", "humor_level": 5,
                        "formality": 5, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 5, "autonomy": 5,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


def _capture_system_text(db, user_message, session_id="t"):
    from unittest.mock import patch
    from windyfly.agent.loop import agent_respond
    from windyfly.memory.write_queue import WriteQueue
    wq = WriteQueue()
    wq.start()
    try:
        captured: dict = {}

        def fake(messages, **kw):
            captured["m"] = messages
            return {"content": "ok", "input_tokens": 5, "output_tokens": 2,
                    "cost": 0.0, "tool_calls": None}

        with patch("windyfly.agent.loop.call_llm", side_effect=fake), \
             patch("windyfly.agent.loop.is_online", return_value=True):
            agent_respond(_make_config(), db, wq, user_message, session_id)
        msgs = captured.get("m", [])
        return "\n".join(
            m.get("content", "") for m in msgs if m.get("role") == "system"
        )
    finally:
        wq.stop()


def test_lessons_block_appears_when_skills_present(db):
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    # Seed a promoted correction skill
    code = (
        "FAULT_TYPE = 'factual_error'\n"
        "CORRECTION = (\n"
        "    'Double-check facts before stating them.'\n"
        ")\n"
    )
    sid = save_skill(db, "correction-factual_error", code, "python")
    promote_skill(db, sid)

    sys_text = _capture_system_text(db, "hi", session_id="lesson-1")
    assert "Lessons learned from past corrections" in sys_text
    assert "Double-check facts" in sys_text
    assert "(correction-factual_error)" in sys_text


def test_lessons_block_absent_when_no_skills(db):
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    sys_text = _capture_system_text(db, "hi", session_id="no-lesson")
    assert "Lessons learned from past corrections" not in sys_text


def test_lessons_block_skips_unparseable_skills(db):
    """A correction skill with malformed CORRECTION text should be
    silently skipped, not produce a broken empty bullet."""
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    sid = save_skill(db, "correction-garbage", "not python at all", "python")
    promote_skill(db, sid)
    sys_text = _capture_system_text(db, "hi", session_id="malformed")
    # Block should NOT appear at all (no parseable lessons)
    assert "Lessons learned" not in sys_text
