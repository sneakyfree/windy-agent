"""Contract tests for the BIAS TO ACTION prompt section.

Phase 2.3.3 (partial — first section). One test per rule pinning the
key phrase, plus structural assertions on the section header and
override-framing.

When the remaining 9 sections migrate to prompt_sections/, each gets
its own paired test file here following the same shape.
"""

from __future__ import annotations

import pytest

from windyfly.agent.prompt_sections import BIAS_TO_ACTION_TEXT


class TestStructure:
    def test_starts_with_section_header(self):
        assert BIAS_TO_ACTION_TEXT.startswith("BIAS TO ACTION")

    def test_declares_override_of_guardrails(self):
        # The block explicitly OVERRIDES the cautious framing of the
        # RUNTIME GUARDRAIL section. Tested separately in
        # test_self_env_confabulation_guard.py too — this is the
        # per-section pin.
        assert "OVERRIDES" in BIAS_TO_ACTION_TEXT

    def test_safety_carveout_present(self):
        # The block must always reserve destructive actions for
        # confirmation. Tested in confab_guard.py too.
        assert "DESTRUCTIVE" in BIAS_TO_ACTION_TEXT
        assert "rm -rf" in BIAS_TO_ACTION_TEXT


class TestRules:
    """One test per rule from the docstring."""

    def test_rule_1_try_first(self):
        assert "TRY FIRST" in BIAS_TO_ACTION_TEXT
        # Specifically the "USE your available tools immediately"
        # phrasing that PR #200 introduced.
        assert "USE your available tools" in BIAS_TO_ACTION_TEXT

    def test_rule_2_investigate_with_tools(self):
        assert "INVESTIGATE WITH TOOLS" in BIAS_TO_ACTION_TEXT
        # Concrete investigation moves the rule lists
        assert "which X" in BIAS_TO_ACTION_TEXT
        assert "systemctl status" in BIAS_TO_ACTION_TEXT

    def test_rule_3_sister_agents_not_gatekeepers(self):
        assert "SISTER AGENTS ARE NOT GATEKEEPERS" in BIAS_TO_ACTION_TEXT
        # Kit 0 delegation is the negative example
        assert "Kit 0" in BIAS_TO_ACTION_TEXT

    def test_rule_4_recovery_over_refusal(self):
        assert "RECOVERY > REFUSAL" in BIAS_TO_ACTION_TEXT
        # Banned phrases from PR #200 + v15 surface-discovery
        for banned in (
            "let me pause here",
            "rather than keep poking blindly",
            "two ways forward — your call",
        ):
            assert banned in BIAS_TO_ACTION_TEXT, (
                f"banned phrase missing: {banned}"
            )

    def test_rule_5_safety_carveout_keeps_destructive_gated(self):
        assert "SAFETY CARVE-OUT" in BIAS_TO_ACTION_TEXT
        # rm -rf, force-pushing, db drop are all listed
        for action in (
            "rm -rf",
            "force-",
            "dropping database",
        ):
            assert action in BIAS_TO_ACTION_TEXT, f"missing: {action}"

    def test_rule_6_when_pushed_back(self):
        assert "WHEN PUSHED BACK" in BIAS_TO_ACTION_TEXT
        # Direct: drop caution, try
        assert "DROP the caution" in BIAS_TO_ACTION_TEXT

    def test_rule_7_ask_at_most_one_question(self):
        assert "ASK AT MOST ONE QUESTION" in BIAS_TO_ACTION_TEXT
        # Self-count + collapse instruction (PR #202)
        assert "Counting the question marks" in BIAS_TO_ACTION_TEXT
        # SOUL anchor
        assert "Ask one good question" in BIAS_TO_ACTION_TEXT


@pytest.mark.parametrize("rule_num", range(1, 8))
def test_rule_number_present_and_terminated(rule_num):
    """Each rule must have its numbered marker. Catches typos like
    duplicated numbers or skipped numbers when a future PR edits the
    block."""
    marker = f"{rule_num}. "
    assert marker in BIAS_TO_ACTION_TEXT, f"rule {rule_num} marker missing"


def test_length_within_expected_range():
    """If the section grows >5KB, that's a smell — flag it for human
    review before merging.
    """
    n = len(BIAS_TO_ACTION_TEXT)
    assert 2500 < n < 5000, (
        f"BIAS_TO_ACTION_TEXT length {n} outside expected 2500-5000 "
        f"range — section may have drifted; review the diff"
    )
