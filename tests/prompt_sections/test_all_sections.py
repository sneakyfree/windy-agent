"""Per-section contract tests for the rest of prompt_sections/.

Phase 2.3.3 completion — one paired test file would be ideal, but
batching them in a single file is fine for the smaller sections.
bias_to_action gets its own file (test_bias_to_action.py) because
its 7-rule structure deserves the per-rule pin granularity.

Each section here gets:
  - structural identity (key phrase present)
  - non-empty
  - length sanity bounds
"""

from __future__ import annotations

import pytest

from windyfly.agent.prompt_sections import (
    EPISTEMIC_TEXT,
    FIRST_CONTACT_TEXT,
    GRANDMA_MODE_TEXT,
    LOW_WORKING_MEMORY_TEXT,
    RUNTIME_GUARDRAIL_TEXT,
    render_active_goal,
    render_runtime_context,
)


class TestEpistemic:
    def test_mentions_confidence(self):
        assert "confidence" in EPISTEMIC_TEXT.lower()

    def test_mentions_inferred(self):
        assert "INFERRED" in EPISTEMIC_TEXT

    def test_length_one_liner(self):
        assert 50 < len(EPISTEMIC_TEXT) < 250


class TestFirstContact:
    def test_starts_with_header(self):
        assert FIRST_CONTACT_TEXT.startswith("FIRST CONTACT")

    def test_bans_welcome_back(self):
        assert "welcome back" in FIRST_CONTACT_TEXT.lower()
        # "DO NOT use" framing — not just a mention
        assert "DO NOT" in FIRST_CONTACT_TEXT

    def test_length_in_range(self):
        assert 200 < len(FIRST_CONTACT_TEXT) < 600


class TestLowWorkingMemory:
    def test_starts_with_header(self):
        assert LOW_WORKING_MEMORY_TEXT.startswith("LOW WORKING MEMORY")

    def test_recommends_new(self):
        assert "/new" in LOW_WORKING_MEMORY_TEXT

    def test_bans_jargon(self):
        # Phase 8 grandma-readability: should NOT mention these jargon
        # phrases in the user-facing instruction (they're talking
        # about themselves but framed as "don't say to user")
        assert "Do not say 'context window'" in LOW_WORKING_MEMORY_TEXT
        # Should prefer plain English
        assert "working memory" in LOW_WORKING_MEMORY_TEXT


class TestRuntimeGuardrail:
    def test_starts_with_header(self):
        assert RUNTIME_GUARDRAIL_TEXT.startswith("RUNTIME GUARDRAIL")

    def test_has_four_pillars(self):
        # Numbered list 1-4 covering NETWORK, TOOLS, IDENTITY, HOST
        for n in range(1, 5):
            assert f"{n}. " in RUNTIME_GUARDRAIL_TEXT, f"missing pillar {n}"
        for pillar in ("NETWORK", "TOOLS", "IDENTITY", "HOST"):
            assert f"{pillar}:" in RUNTIME_GUARDRAIL_TEXT, f"missing pillar {pillar}"

    def test_bans_docker_sandbox_claim(self):
        # Per PR #162 — model used to say "I'm in a Docker sandbox"
        assert "Docker sandbox" in RUNTIME_GUARDRAIL_TEXT
        # ...and the rule explicitly DISALLOWS the claim
        assert "Do NOT say" in RUNTIME_GUARDRAIL_TEXT

    def test_kit0_identity_protection(self):
        # IDENTITY pillar must explicitly mention NOT being Kit 0
        assert "Kit 0" in RUNTIME_GUARDRAIL_TEXT

    def test_host_pillar_bans_fake_ssh(self):
        # PR #188 added this — bot used to invent ssh root@... commands
        assert "ssh root@" in RUNTIME_GUARDRAIL_TEXT
        assert "NEVER" in RUNTIME_GUARDRAIL_TEXT


class TestGrandmaMode:
    def test_starts_with_header(self):
        assert GRANDMA_MODE_TEXT.startswith("GRANDMA MODE")

    def test_has_banned_vocab_list(self):
        assert "BANNED VOCABULARY" in GRANDMA_MODE_TEXT
        # A representative sample of the 25+ banned terms
        for term in ("SSH", "Docker", "systemd", "API key", "OAuth"):
            assert term in GRANDMA_MODE_TEXT, f"banned-vocab list missing {term}"

    def test_has_plain_english_substitutions(self):
        assert "PLAIN-ENGLISH SUBSTITUTIONS" in GRANDMA_MODE_TEXT

    def test_addresses_tool_output_translation(self):
        # Tool outputs may contain jargon; instructs translation
        assert "WHEN USING TOOLS" in GRANDMA_MODE_TEXT
        assert "translate to plain English" in GRANDMA_MODE_TEXT

    def test_length_within_sanity(self):
        assert 1500 < len(GRANDMA_MODE_TEXT) < 4000


class TestRenderActiveGoal:
    def test_includes_goal_text(self):
        out = render_active_goal("research apartments under $2200")
        assert "research apartments under $2200" in out

    def test_starts_with_target_emoji(self):
        out = render_active_goal("test goal")
        assert out.startswith("🎯 ACTIVE GOAL")

    def test_has_four_rules(self):
        out = render_active_goal("test")
        for n in range(1, 5):
            assert f"{n}. " in out

    def test_mentions_slash_goal_commands(self):
        out = render_active_goal("test")
        for cmd in ("/goal status", "/goal done", "/goal clear"):
            assert cmd in out

    def test_with_multiline_goal(self):
        out = render_active_goal("line 1\nline 2")
        assert "line 1" in out and "line 2" in out


class TestRenderRuntimeContext:
    def _config(self):
        return {"agent": {"default_model": "claude-haiku-4-5-20251001"}}

    def test_starts_with_header(self):
        out = render_runtime_context(self._config())
        assert out.startswith("RUNTIME CONTEXT")

    def test_includes_model(self):
        out = render_runtime_context(self._config())
        assert "claude-haiku-4-5-20251001" in out

    def test_includes_cwd_line(self):
        out = render_runtime_context(self._config())
        assert "CWD:" in out

    def test_includes_process_line(self):
        out = render_runtime_context(self._config())
        assert "Process:" in out

    def test_closes_with_quote_instruction(self):
        out = render_runtime_context(self._config())
        # Trailing instruction — model is told to QUOTE the lines above
        assert "QUOTE" in out

    def test_handles_missing_default_model(self):
        out = render_runtime_context({})
        assert "unknown" in out


@pytest.mark.parametrize("text_const", [
    EPISTEMIC_TEXT, FIRST_CONTACT_TEXT, LOW_WORKING_MEMORY_TEXT,
    RUNTIME_GUARDRAIL_TEXT, GRANDMA_MODE_TEXT,
])
def test_all_constants_are_strings(text_const):
    assert isinstance(text_const, str)
    assert len(text_const) > 0
