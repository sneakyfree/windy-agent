"""Self-environment confabulation guard regression suite.

Surfaced 2026-05-10 by a Telegram screenshot where the LLM, asked to
act on lockbox creds (which it had successfully fetched via
github.fetch_file), said: "I cannot open outbound SSH connections
from this Docker sandbox — network is `--network=none`. The lockbox
credentials are 100% correct and ready. I just need a path out."

That claim is FALSE for Windy 0:
  - Runs as a native systemd user service (not Docker).
  - Has full outbound HTTP/HTTPS (proven by the same turn's
    successful api.anthropic.com + api.github.com calls).
  - The actual limitation was "no SSH tool registered" — the LLM
    fabricated a sandboxed-network framing to justify refusal.

Two-layer defense:

  Layer 1 (prompt) — assemble_prompt() unconditionally appends a
  RUNTIME GUARDRAIL system message that tells the LLM the truth
  about its network + tool surface + identity, framed as guardrails
  ("do not claim X") rather than runtime facts so the prompt stays
  correct if deployment changes.

  Layer 2 (tripwire) — agent_respond() runs
  _looks_self_env_confabulated() on the final response_text. On hit,
  re-prompts the LLM with _SELF_ENV_RETRY_SYSTEM. If the retry STILL
  confabulates, replaces with _SELF_ENV_TRUTH_FALLBACK so the false
  claim never reaches the user. Logs agent.confabulation_detected
  with stage="self_env_initial" / "self_env_retry".
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


# ─── Layer 1: RUNTIME GUARDRAIL system prompt block ──────────────


def _make_config():
    return {
        "agent": {"default_model": "claude-haiku-4-5-20251001",
                  "max_context_tokens": 8000, "max_response_tokens": 2000,
                  "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20,
                   "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "humor_level": 7,
                        "formality": 4, "proactivity": 5, "verbosity": 5,
                        "reasoning_depth": 6, "autonomy": 3,
                        "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


@pytest.fixture
def db():
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    yield db
    db.close()


class TestRuntimeGuardrailInPrompt:

    def test_guardrail_appears_in_system_message(self, db):
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-1")
        # The system message is the first one
        sys_text = msgs[0]["content"]
        # Three pillars: NETWORK + TOOLS + IDENTITY
        assert "RUNTIME GUARDRAIL" in sys_text
        assert "NETWORK" in sys_text
        assert "TOOLS" in sys_text
        assert "IDENTITY" in sys_text

    def test_guardrail_explicitly_bans_docker_sandbox_claim(self, db):
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-2")
        sys_text = msgs[0]["content"]
        # The exact phrase from the screenshot bug must be banned.
        assert "Docker sandbox" in sys_text
        assert "--network=none" in sys_text

    def test_guardrail_directs_tool_refusals_correctly(self, db):
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-3")
        sys_text = msgs[0]["content"]
        # The corrective phrasing must be spelled out so the LLM
        # has a copy-paste template for legitimate refusals.
        assert "I don't have a" in sys_text and "capability" in sys_text

    def test_guardrail_prevents_identity_confabulation(self, db):
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-4")
        sys_text = msgs[0]["content"]
        # Bot must know it is NOT Kit 0 / OC1-OC7.
        assert "Kit 0" in sys_text
        assert "OC1" in sys_text or "OC7" in sys_text

    def test_guardrail_present_in_grandma_band_too(self, db):
        """The grandma-mode block goes AFTER runtime guardrail, so
        guardrail must still be present even when band=USER triggers
        the grandma-mode jargon ban."""
        from windyfly.agent.capabilities import Band
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-5",
                               band=Band.USER)
        sys_text = msgs[0]["content"]
        assert "RUNTIME GUARDRAIL" in sys_text


# ─── Layer 2: tripwire detection ─────────────────────────────────


class TestTripwireDetection:

    def test_screenshot_phrase_detected(self):
        """The exact phrase from the 2026-05-10 screenshot."""
        from windyfly.agent.loop import _looks_self_env_confabulated
        text = (
            "OK, I have the lockbox info. However, I cannot open "
            "outbound SSH connections from this Docker sandbox — "
            "network is `--network=none`. The lockbox credentials "
            "are 100% correct."
        )
        assert _looks_self_env_confabulated(text) is True

    def test_alternate_phrasings_detected(self):
        from windyfly.agent.loop import _looks_self_env_confabulated
        cases = [
            "I'm in a sandbox so I can't reach the internet.",
            "I am sandboxed and cannot make outbound calls.",
            "I'm in a Docker container with no network.",
            "My environment doesn't allow that.",
            "I'm rate-limited by my environment.",
            "I can't help — this is a browser sandbox.",
            "Running in this Docker container, I can't SSH out.",
        ]
        for text in cases:
            assert _looks_self_env_confabulated(text) is True, \
                f"missed: {text!r}"

    def test_legitimate_user_docker_discussion_passes(self):
        """User asks about Docker without bot self-locating in it —
        no tripwire fire."""
        from windyfly.agent.loop import _looks_self_env_confabulated
        cases = [
            "Sure, I can help you write a Dockerfile for your app.",
            "Docker is a containerization platform you can use.",
            "If you want to deploy this in Docker, here's how.",
            "The sandbox you mentioned is a security boundary.",
        ]
        for text in cases:
            assert _looks_self_env_confabulated(text) is False, \
                f"false positive: {text!r}"

    def test_empty_response_returns_false(self):
        from windyfly.agent.loop import _looks_self_env_confabulated
        assert _looks_self_env_confabulated("") is False
        assert _looks_self_env_confabulated(None) is False  # type: ignore


# ─── Layer 2: end-to-end retry → truth-fallback flow ─────────────


@pytest.fixture
def stack():
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue(); wq.start()
    yield _make_config(), db, wq
    try: wq.stop()
    except Exception: pass
    db.close()


class TestTripwireRetryFlow:

    def test_clean_reply_passes_through_unchanged(self, stack):
        """A reply that does NOT trip the tripwire goes out as-is."""
        config, db, wq = stack
        from windyfly.agent.loop import agent_respond
        clean = {
            "content": "Sure, here's how that works in plain English.",
            "input_tokens": 10, "output_tokens": 5,
            "tool_calls": None, "model": "claude-haiku-4-5-20251001",
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=clean):
            reply = agent_respond(config, db, wq, "explain Docker", "s1")
        assert "plain English" in reply
        # Truth fallback must NOT have leaked in
        assert "I almost gave you a misleading reason" not in reply

    def test_first_offense_triggers_retry_with_clean_followup(self, stack):
        """When initial reply confabulates, the retry's clean output
        is what reaches the user."""
        config, db, wq = stack
        from windyfly.agent.loop import agent_respond
        confab = {
            "content": "I cannot do that — I'm in a Docker sandbox.",
            "input_tokens": 10, "output_tokens": 5,
            "tool_calls": None, "model": "claude-haiku-4-5-20251001",
        }
        clean_retry = {
            "content": "I don't have an SSH capability for that — "
                       "want me to walk you through doing it yourself?",
            "input_tokens": 10, "output_tokens": 8,
            "tool_calls": None, "model": "claude-haiku-4-5-20251001",
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm",
                   side_effect=[confab, clean_retry]):
            reply = agent_respond(config, db, wq, "ssh to oc1", "s2")

        # Confab phrase should NOT appear; clean retry phrase SHOULD.
        assert "Docker sandbox" not in reply
        assert "I don't have an SSH capability" in reply

    def test_retry_still_confabulates_uses_truth_fallback(self, stack):
        """When the LLM doubles down on the confabulation, the
        guard must replace with the deterministic truth fallback."""
        config, db, wq = stack
        from windyfly.agent.loop import agent_respond
        confab1 = {
            "content": "I'm in a Docker sandbox so I can't.",
            "input_tokens": 10, "output_tokens": 5,
            "tool_calls": None, "model": "claude-haiku-4-5-20251001",
        }
        confab2 = {
            "content": "Same story — I'm sandboxed.",
            "input_tokens": 8, "output_tokens": 4,
            "tool_calls": None, "model": "claude-haiku-4-5-20251001",
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm",
                   side_effect=[confab1, confab2]):
            reply = agent_respond(config, db, wq, "ssh somewhere", "s3")

        # Neither confabulated reply made it through.
        assert "Docker sandbox" not in reply
        assert "sandboxed" not in reply
        # Truth fallback IS what reached the user.
        assert "misleading reason" in reply.lower() \
            or "honest answer" in reply.lower()

    def test_retry_exception_falls_back_to_truth(self, stack):
        """When the retry call itself raises (chain exhausted, etc.),
        we replace with truth fallback rather than crash."""
        config, db, wq = stack
        from windyfly.agent.loop import agent_respond
        confab = {
            "content": "I'm in a sandbox.",
            "input_tokens": 10, "output_tokens": 5,
            "tool_calls": None, "model": "claude-haiku-4-5-20251001",
        }
        # Initial returns confab; retry raises.
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm",
                   side_effect=[confab, RuntimeError("retry blew up")]):
            reply = agent_respond(config, db, wq, "do stuff", "s4")

        assert "I'm in a sandbox" not in reply
        # Truth fallback present
        assert "misleading reason" in reply.lower() \
            or "don't currently have a tool" in reply.lower()
