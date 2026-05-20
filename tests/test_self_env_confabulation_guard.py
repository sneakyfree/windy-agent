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

    def test_guardrail_prevents_host_confabulation(self, db):
        """Surfaced 2026-05-17 by a Telegram screenshot where the bot,
        asked which API key it was using, replied 'ssh root@72.60.118.54
        / grep ANTHROPIC ~/.windy/windy-0.env' — directing Grant to
        check his Kit 0 VPS when in fact Windy 0 lives on his Fedora
        workstation. The bot confabulated its own host by conflating
        itself with the sister agent (Kit 0) that DOES live on the VPS.

        Guardrail must spell out that the bot cannot introspect its
        own env from inside the conversation and must not invent an
        SSH command to a remote host."""
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-host")
        sys_text = msgs[0]["content"]
        # 4th pillar must be present.
        assert "HOST" in sys_text
        # The exact failure mode from the screenshot must be banned.
        assert "ssh root@" in sys_text.lower()
        # Must offer the corrective phrasing as a template.
        assert "can't introspect" in sys_text.lower() or \
               "cannot introspect" in sys_text.lower()
        # Must explicitly call out the Kit-0 conflation that drove
        # the original bug — sister agents may live remote, this one
        # may not.
        assert "Kit 0" in sys_text

    def test_bias_to_action_block_present(self, db):
        """PR #200 — counterbalances the cautious guardrails with a
        positive 'try first, ask last' directive. Surfaced 2026-05-19
        when the bot refused fleet operations it had tools for,
        deferred to sister agents, and piled clarifying questions on
        the user instead of just using ssh.exec."""
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-bias")
        sys_text = msgs[0]["content"]
        assert "BIAS TO ACTION" in sys_text
        # The block must explicitly override caution for non-
        # destructive tasks — otherwise the existing guardrails
        # silently dominate.
        assert "OVERRIDES" in sys_text or "overrides" in sys_text

    def test_bias_to_action_tells_model_to_try_first(self, db):
        """The 'try first' directive must be explicit and concrete —
        list real tools the bot has, so the model knows which path to
        take instead of refusing."""
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-try")
        sys_text = msgs[0]["content"]
        assert "TRY FIRST" in sys_text
        # Specific tools named so the model anchors on real
        # capabilities rather than generic "use your tools" advice.
        assert "ssh.exec" in sys_text
        assert "shell.exec" in sys_text or "fs.read_file" in sys_text

    def test_bias_to_action_kills_kit0_deflection(self, db):
        """Specific anti-pattern from the 2026-05-19 screenshot: bot
        said 'I'm not the right agent to dispatch OpenClaw directly'
        and refused. New block must explicitly call out that sister-
        agent mentions are NOT a green light to defer fleet ops."""
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-kit")
        sys_text = msgs[0]["content"]
        # The negation must be explicit — "X does NOT mean Y"
        assert "GATEKEEPERS" in sys_text or "gatekeepers" in sys_text
        # The corrective phrasing must be spelled out
        assert "let me ssh in and do it" in sys_text.lower() or \
            "ssh in and do it" in sys_text.lower()

    def test_bias_to_action_has_destructive_carveout(self, db):
        """An aggressive bot without a safety carve-out is dangerous.
        Block must preserve caution for destructive/irreversible ops
        (rm -rf, dropping tables, deleting accounts) while greenlighting
        reads / restarts / updates / config additions."""
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-safe")
        sys_text = msgs[0]["content"]
        assert "SAFETY CARVE-OUT" in sys_text or \
            "safety carve-out" in sys_text.lower()
        # Specific destructive patterns named
        assert "rm -rf" in sys_text
        # Specific safe patterns named so the model has a positive list
        sys_lower = sys_text.lower()
        assert "restart" in sys_lower
        assert "update" in sys_lower

    def test_bias_to_action_includes_pushback_directive(self, db):
        """When the user pushes back, the bot must DROP caution
        rather than re-justify. Closes the 2026-05-19 'why are you
        asking now' loop."""
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(_make_config(), db, "hi", "session-push")
        sys_text = msgs[0]["content"]
        sys_lower = sys_text.lower()
        # The directive must explicitly handle pushback
        assert "pushed back" in sys_lower or "push back" in sys_lower
        # Must say to drop caution, not explain it
        assert "drop the caution" in sys_lower or "drop caution" in sys_lower

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

    def test_host_confabulation_detected(self):
        """Reproduced 2026-05-18 even after PR #188's prompt-level
        HOST bullet — model still emitted 'windy-0.env on your VPS /
        ssh root@72.60.118.54'. Tripwire is the second line of
        defense for the specific surface forms we keep seeing."""
        from windyfly.agent.loop import _looks_self_env_confabulated
        cases = [
            # The 2026-05-17 / 2026-05-18 screenshot phrases verbatim
            "Check `~/.windy/windy-0.env` on your VPS for the "
            "ANTHROPIC_API_KEY value",
            "Run that on your VPS (or wherever the bot process lives)",
            "ssh root@72.60.118.54\ngrep ANTHROPIC ~/.windy/windy-0.env",
            # Plausible variants the bot might drift into next
            "windy-0.env on the vps where I run",
            "check ~/.windy/windy-0.env on your remote host",
        ]
        for text in cases:
            assert _looks_self_env_confabulated(text) is True, \
                f"missed HOST confab: {text!r}"

    def test_kit_0_delegation_confab_detected(self):
        """Surfaced 2026-05-19: bot diagnosed a (real) prompt-injection
        bug by blaming a non-existent soul-file directive and punted
        the fix to Kit 0 ("Kit 0 should remove that line from the
        soul prompt"). Kit 0 is the VPS coordinator — it doesn't own
        windy-agent's source. The bot was conflating sister-agent
        roles. Tripwire catches the imperative-delegation surface
        form."""
        from windyfly.agent.loop import _looks_self_env_confabulated
        cases = [
            "Kit 0 should remove that line from the soul prompt.",
            "Kit 0 should fix this in the prompt module.",
            "Kit 0 should update the SOUL.md to remove this.",
            "Have Kit 0 fix the prompt-injection issue.",
            "Kit 0 can fix this by editing prompt.py.",
            "Kit 0 needs to update the soul prompt configuration.",
        ]
        for text in cases:
            assert _looks_self_env_confabulated(text) is True, \
                f"missed Kit 0 delegation confab: {text!r}"

    def test_kit_0_legitimate_mentions_pass(self):
        """Third-person mentions of Kit 0 in legitimate contexts
        (explanations, status reports) should NOT trip — patterns
        target imperative 'Kit 0 should/can fix X' shape."""
        from windyfly.agent.loop import _looks_self_env_confabulated
        cases = [
            "Kit 0 is the VPS coordinator at 72.60.118.54.",
            "I'd defer to Kit 0 on VPS-side questions.",
            "Kit 0's lockbox file is the canonical credential store.",
            "What does Kit 0 do in the fleet architecture?",
        ]
        for text in cases:
            assert _looks_self_env_confabulated(text) is False, \
                f"false positive on Kit 0 mention: {text!r}"

    def test_host_legitimate_uses_pass(self):
        """User-instruction context for actual VPSes Grant operates
        should NOT trip the host tripwire — patterns target the bot's
        OWN env file name (windy-0.env) co-occurring with remote-host
        framing, not generic SSH-to-VPS discussion."""
        from windyfly.agent.loop import _looks_self_env_confabulated
        cases = [
            "To SSH into your kit, run: ssh kit-0c3",
            "You can ssh root@example.com to your own server.",
            "The VPS at kit-army-config has the lockbox file.",
            "Edit your env file at /etc/myapp/.env on the VPS.",
        ]
        for text in cases:
            assert _looks_self_env_confabulated(text) is False, \
                f"false positive on HOST tripwire: {text!r}"

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
