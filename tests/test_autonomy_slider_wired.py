"""Wire-up tests for the autonomy + epistemic_strictness sliders.

Both sliders have been *defined* in control_panel.py since the agent
shipped — with documented semantics ("Always asks before doing
anything" at low end of autonomy, "Only cites verified facts" at
high end of epistemic) — but ``personality/engine.py:_apply_sliders``
never read them. They sat purely cosmetic.

The 2026-05-20 Telegram screenshot (bot opening with "Heads up
before I draft anything — a few honesty checks: ...") made this
concrete: PR #200's BIAS TO ACTION block kept losing to the
conservative default because the slider that should have anchored
the user's preference was a no-op.

These tests pin the contract:
  - autonomy ≥7 → action-bias modifier present in the personality block
  - autonomy ≤3 → ask-permission modifier present
  - 4-6 (default) → balanced modifier
  - epistemic_strictness ≥7 → "Only state facts you are confident
    about" modifier
  - epistemic_strictness ≤3 → "Use everything you remember"
  - CWD line appears in RUNTIME CONTEXT block (separate concern but
    same PR, covers v15's path-guessing finding)
  - BIAS TO ACTION block contains the "ASK AT MOST ONE QUESTION"
    rule (closes the 0/3 ambiguous_ok finding from v15)
"""

from __future__ import annotations

import os

import pytest

from windyfly.personality.engine import build_personality_block


_SOUL = "You are Windy Fly, a personal AI companion."


def _modifiers_section(sliders: dict) -> str:
    """Return only the Behavioral Modifiers chunk for easy substring
    assertions."""
    out = build_personality_block(_SOUL, sliders)
    if "## Behavioral Modifiers" not in out:
        return ""
    return out.split("## Behavioral Modifiers", 1)[1]


# ── autonomy ─────────────────────────────────────────────────────


def test_high_autonomy_emits_action_bias_modifier():
    mods = _modifiers_section({"autonomy": 8})
    # action-bias signal
    assert "ACT FIRST" in mods or "act first" in mods.lower()
    assert "Don't ask permission" in mods or "questions are a last resort" in mods.lower()


def test_low_autonomy_emits_ask_permission_modifier():
    mods = _modifiers_section({"autonomy": 2})
    assert "Ask before acting" in mods or "ask before acting" in mods.lower()
    assert "Confirm" in mods or "permission" in mods.lower()


def test_median_autonomy_emits_balanced_modifier():
    """Crucially, the median (default) is NOT silent — it should
    emit a 'try, then ask at most once' modifier. Without this the
    median user gets pure SOUL.md with no slider-anchored guidance."""
    mods = _modifiers_section({"autonomy": 5})
    assert "attempt it" in mods.lower() or "proceed" in mods.lower()
    assert "one clarifying" in mods.lower() or "at most one" in mods.lower()


def test_missing_autonomy_defaults_to_median():
    """If the slider isn't provided at all, treat as 5 (median band)."""
    mods = _modifiers_section({})
    assert "attempt it" in mods.lower() or "proceed" in mods.lower()


# ── epistemic_strictness ─────────────────────────────────────────


def test_high_epistemic_emits_confidence_modifier():
    mods = _modifiers_section({"epistemic_strictness": 9})
    assert "confident" in mods.lower()
    assert "fuzzy" in mods.lower() or "infer" in mods.lower() or "guess" in mods.lower()


def test_low_epistemic_emits_hunches_ok_modifier():
    mods = _modifiers_section({"epistemic_strictness": 2})
    assert "hunches" in mods.lower() or "remember" in mods.lower()


def test_median_epistemic_emits_no_modifier():
    """Median epistemic produces no modifier — the existing prompt
    body handles default behavior. Don't add noise just to add noise."""
    mods = _modifiers_section({"epistemic_strictness": 5, "autonomy": 5})
    # Only the autonomy median modifier should be present; no
    # epistemic-specific line.
    assert "confident about" not in mods
    assert "hunches" not in mods.lower()


# ── interaction ──────────────────────────────────────────────────


def test_autonomy_and_epistemic_can_coexist():
    """Both can fire in the same modifiers block without overwriting
    each other."""
    mods = _modifiers_section({"autonomy": 8, "epistemic_strictness": 8})
    assert "ACT FIRST" in mods or "act first" in mods.lower()
    assert "confident" in mods.lower()


# ── CWD injection + BIAS TO ACTION (assemble_prompt integration) ──


@pytest.fixture
def _assembled_system():
    """Assemble the real prompt against an in-memory DB and return
    the concatenated system message content. Avoids per-test setup
    duplication."""
    from windyfly.memory.database import Database
    from windyfly.agent.prompt import assemble_prompt

    db = Database(":memory:")
    config = {
        "agent": {
            "default_model": "claude-haiku-4-5-20251001",
            "max_context_tokens": 8000,
            "max_response_tokens": 1024,
            "temperature": 0.5,
        },
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 5,
                   "max_nodes_per_context": 5},
        "personality": {"soul_path": "SOUL.md", "humor_level": 5,
                        "formality": 5, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 5, "autonomy": 5,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }
    messages = assemble_prompt(
        config=config, db=db,
        user_message="hi", session_id="test-assemble",
    )
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    db.close()
    return system


def test_cwd_appears_in_runtime_context(_assembled_system):
    """RUNTIME CONTEXT should include the current working directory
    so the LLM can resolve "this repo" / "here" against a real
    path instead of guessing ~/<name>."""
    cwd = os.getcwd()
    assert f"CWD: {cwd}" in _assembled_system, (
        "expected CWD line in RUNTIME CONTEXT — got first 1500 chars:\n"
        + _assembled_system[:1500]
    )
    assert "this repo" in _assembled_system or "this folder" in _assembled_system


def test_bias_to_action_block_has_max_one_question_rule(_assembled_system):
    """Closes v15's 0/3 ambiguous_ok finding: bot asks 3-7 ?s on
    ambiguous prompts. The fix is an explicit rule in the system
    prompt — verify the rule is actually there."""
    assert "ASK AT MOST ONE QUESTION" in _assembled_system
    assert "Ask one good question" in _assembled_system


def test_bias_to_action_block_bans_give_up_phrases(_assembled_system):
    """Closes the screenshot's 'let me pause here rather than keep
    poking blindly' pattern: after a single SSH banner timeout the
    bot bailed and bounced the user with more questions, instead of
    retrying as RECOVERY > REFUSAL says. Pin the banned phrases so
    a future prompt refactor can't drop them."""
    for phrase in (
        "let me pause here",
        "rather than keep poking blindly",
        "two ways forward",
    ):
        assert phrase in _assembled_system, (
            f"BIAS TO ACTION must explicitly ban {phrase!r} — see "
            "the 2026-05-20 screenshot. Re-add it to rule 4."
        )
