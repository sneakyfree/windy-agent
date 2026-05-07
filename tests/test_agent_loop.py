"""Tests for the agent loop with mocked LLM.

Tests prompt assembly, agent_respond flow, episode saving,
cost logging, fact extraction, tool execution, budget enforcement,
emotional context, intent detection, and epistemic filtering.
"""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent.loop import _extract_and_store_facts, agent_respond
from windyfly.agent.prompt import _extract_keywords, assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes
from windyfly.memory.write_queue import WriteQueue
import windyfly.agent.context_header as _ch
import windyfly.agent.loop as _loop


@pytest.fixture(autouse=True)
def _reset_module_state(tmp_path, monkeypatch):
    """Module-level dicts in agent.loop accumulate across tests when
    multiple tests share the same session_id (most do — "test-session").
    Without resetting between tests, the per-session journal counter
    crosses its every-10th threshold and triggers an unexpected
    call_llm invocation, which exhausts mock side_effect lists in
    later tests in the file. Symptom: test_tool_reloop_executes_tools
    flaky — passes alone, fails when the file runs in order. Reset
    pre-test to break the cross-test dependency.

    Also isolates the spend-monitor pause/yolo flag paths to a temp
    dir per test. Surfaced 2026-05-02: production auto-pause from a
    Sonnet stress run leaked into unit tests because the fixture
    didn't override WINDY_PAUSE_FLAG. Tests using mocked LLM should
    never see the production pause state."""
    _loop._session_tokens.clear()
    _loop._session_interaction_counts.clear()
    _ch._tracker = None
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.setenv("WINDY_YOLO_FLAG", str(tmp_path / ".yolo"))
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(tmp_path / ".guest"))
    yield
    _loop._session_tokens.clear()
    _loop._session_interaction_counts.clear()


def _make_config() -> dict:
    return {
        "agent": {
            "default_model": "gpt-4o-mini",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {
            "db_path": ":memory:",
            "max_episodes_per_context": 20,
            "max_nodes_per_context": 10,
        },
        "personality": {
            "soul_path": "SOUL.md",
            "humor_level": 7,
            "formality": 4,
            "proactivity": 5,
            "verbosity": 5,
            "reasoning_depth": 6,
            "autonomy": 3,
            "epistemic_strictness": 5,
        },
        "costs": {
            "daily_budget_usd": 5.0,
            "warn_at_usd": 3.0,
        },
    }


def _make_db() -> Database:
    """Fresh in-memory DB for each test, seeded with one prior
    episode so the first-contact welcome shortcut (PR #142) doesn't
    fire and short-circuit the LLM mock that most tests in this
    file depend on. Tests that specifically want a virgin DB
    (like test_first_contact_guard_fires_on_virgin_db) build their
    own DB."""
    db = Database(":memory:")
    from windyfly.memory.episodes import save_episode
    save_episode(db, "user", "previous turn so welcome doesn't fire",
                 session_id="bootstrap")
    return db


_LLM_RESPONSE = {
    "content": "Hello! I'm Windy Fly.",
    "model": "gpt-4o-mini",
    "input_tokens": 100,
    "output_tokens": 20,
    "tool_calls": None,
}


class TestExtractKeywords:
    def test_basic_extraction(self):
        result = _extract_keywords("What is the weather like today?")
        assert "weather" in result
        assert "like" in result or "today" in result

    def test_filters_stopwords(self):
        result = _extract_keywords("I am going to the store")
        assert "the" not in result.split()
        assert "going" in result or "store" in result

    def test_short_words_filtered(self):
        result = _extract_keywords("I am OK")
        # Short words below min_length should be filtered
        assert "am" not in result.split()

    def test_empty_message(self):
        result = _extract_keywords("")
        assert result == ""


class TestAssemblePrompt:
    def test_has_system_message(self):
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session")
        assert messages[0]["role"] == "system"
        assert len(messages[0]["content"]) > 0
        db.close()

    def test_has_user_message_at_end(self):
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session")
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Hello!"
        db.close()

    def test_includes_mode_override(self):
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session", mode="focused")
        system_content = messages[0]["content"]
        assert "focused" in system_content.lower()
        db.close()

    def test_first_contact_guard_fires_on_virgin_db(self):
        """Stress harness v6 Notebook test 2026-04-27: the bot was
        opening with 'Welcome back!' on a truly virgin DB (episodes=0,
        nodes=0) — the LLM defaults to familiarity language even with
        zero memory backing. Fix: detect first contact and inject an
        explicit instruction not to use 'welcome back' etc.

        This regression locks the new contract: when the DB is virgin,
        the system prompt must contain a FIRST CONTACT instruction."""
        config = _make_config()
        # Explicit virgin DB — _make_db() seeds an episode by default
        # to bypass PR #142's welcome shortcut, but THIS test
        # specifically wants a fresh empty DB to verify prompt-side
        # FIRST CONTACT behavior.
        db = Database(":memory:")
        messages = assemble_prompt(config, db, "Hey, I'm back!", "test-session")
        system_content = messages[0]["content"]
        assert "FIRST CONTACT" in system_content
        assert "DO NOT use 'welcome back'" in system_content or \
               "do not use 'welcome back'" in system_content.lower()
        db.close()

    def test_first_contact_guard_does_NOT_fire_when_memory_exists(self):
        """Inverse of the regression: with prior episodes/nodes, the
        FIRST CONTACT guard must NOT fire — the personality block
        drives tone normally."""
        from windyfly.memory.episodes import save_episode
        config = _make_config()
        db = _make_db()
        # Plant a prior episode so the bot has SOMETHING to remember
        save_episode(
            db, session_id="prior-session", role="user",
            content="hello from yesterday",
        )
        messages = assemble_prompt(config, db, "Hey", "test-session")
        system_content = messages[0]["content"]
        assert "FIRST CONTACT" not in system_content
        db.close()

    def test_low_context_hint_fires_below_10_pct(self):
        """At < 10% remaining the system prompt must instruct the LLM
        to suggest /new in grandma-friendly terms.

        Surfaced 2026-04-27 by a real conversation where the bot
        showed 🔴 0% in the gas-tank header and replied with
        engineer-mode jargon, leaving the user puzzled about whether
        the bot was broken. The fix: tell the LLM the user can /new
        whenever, in plain English."""
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Hello!", "test-session", pct_remaining=3.0,
        )
        system_content = messages[0]["content"]
        assert "LOW WORKING MEMORY" in system_content
        assert "/new" in system_content
        # Must use plain language, not jargon
        assert "context window" not in system_content.lower() or \
               "do not say 'context window'" in system_content.lower()
        db.close()

    def test_low_context_hint_does_NOT_fire_above_10_pct(self):
        """When the session is healthy (>= 10% remaining) the hint
        must stay quiet — otherwise every reply would nag the user
        to /new."""
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Hello!", "test-session", pct_remaining=42.0,
        )
        system_content = messages[0]["content"]
        assert "LOW WORKING MEMORY" not in system_content
        db.close()

    def test_low_context_hint_does_NOT_fire_when_unspecified(self):
        """Backwards compat: callers that don't pass pct_remaining
        (e.g., older tests) get the prior behavior — no hint."""
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session")
        system_content = messages[0]["content"]
        assert "LOW WORKING MEMORY" not in system_content
        db.close()

    def test_low_context_hint_boundary_exactly_10(self):
        """At exactly 10.0% remaining the hint should NOT fire — the
        threshold is strictly below 10. Matches the 🔴/🟡 boundary in
        context_header.format_header (pct >= 10 → 🟡, < 10 → 🔴)."""
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Hello!", "test-session", pct_remaining=10.0,
        )
        system_content = messages[0]["content"]
        assert "LOW WORKING MEMORY" not in system_content
        db.close()

    def test_grandma_mode_fires_for_USER_band(self):
        """Paired grandma → grandma-mode tone instruction must
        be in the system prompt."""
        from windyfly.agent.capabilities import Band
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "How do I update everyone?", "test-session",
            band=Band.USER,
        )
        system_content = messages[0]["content"]
        assert "GRANDMA MODE" in system_content
        # Pin a few of the banned-jargon items so the contract holds
        # if someone re-words the instruction.
        assert "WireGuard" in system_content
        assert "Docker" in system_content
        db.close()

    def test_grandma_mode_includes_tool_output_suppression(self):
        """v13 battery 2026-05-02 surfaced: when the bot under
        band=USER asked about its server, it leaked SSH-config /
        wg-* / kit-* aliases verbatim because the freshly-shipped
        fleet.* capability descriptions mention those terms. The
        GRANDMA MODE instruction must explicitly cover the
        'tool-output → reply' translation step so a curious tool
        description doesn't override the tone gate."""
        from windyfly.agent.capabilities import Band
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Connect to my server.", "test-session",
            band=Band.USER,
        )
        system_content = messages[0]["content"]
        assert "WHEN USING TOOLS" in system_content
        # Specific anti-leak markers
        assert "wg-0c3" in system_content or "wg-" in system_content
        db.close()

    def test_grandma_mode_covers_clarifying_questions(self):
        """Real-LLM verification 2026-05-02 PR #121-followup: the
        bot was leaking 'SSH config' as a *clarifying question*
        ('Do you have SSH access configured?') even though the
        instruction told it not to use SSH in its OWN statements.
        Pin: GRANDMA MODE must explicitly forbid technical
        vocabulary in clarifying questions too, and provide
        plain-English substitutions to use instead."""
        from windyfly.agent.capabilities import Band
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Can you connect to my server?", "test-session",
            band=Band.USER,
        )
        system_content = messages[0]["content"]
        # The new STRICT marker
        assert "GRANDMA MODE — STRICT" in system_content
        # Banned vocabulary explicitly listed
        assert "BANNED VOCABULARY" in system_content
        # Clarifying-question guidance present
        assert "WHEN ASKING CLARIFYING QUESTIONS" in system_content
        assert "PLAIN-ENGLISH SUBSTITUTIONS" in system_content
        # The exact failure-pattern phrase from the leak is called
        # out as a counter-example
        assert "SSH" in system_content
        db.close()

    def test_grandma_mode_fires_for_SANDBOX_band(self):
        """Unknown demo guest → also grandma mode."""
        from windyfly.agent.capabilities import Band
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Hi there", "test-session",
            band=Band.SANDBOX,
        )
        system_content = messages[0]["content"]
        assert "GRANDMA MODE" in system_content
        db.close()

    def test_grandma_mode_does_NOT_fire_for_TRUSTED_band(self):
        """Power-user paired device → personality drives tone."""
        from windyfly.agent.capabilities import Band
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Hi", "test-session", band=Band.TRUSTED,
        )
        system_content = messages[0]["content"]
        assert "GRANDMA MODE" not in system_content
        db.close()

    def test_grandma_mode_does_NOT_fire_for_OWNER_band(self):
        """Grant on his own bot → engineer-mode permitted."""
        from windyfly.agent.capabilities import Band
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(
            config, db, "Hi", "test-session", band=Band.OWNER,
        )
        system_content = messages[0]["content"]
        assert "GRANDMA MODE" not in system_content
        db.close()

    def test_grandma_mode_does_NOT_fire_when_band_unspecified(self):
        """Backwards compat: callers that don't pass band → no
        override (matches OWNER default in agent_respond)."""
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hi", "test-session")
        system_content = messages[0]["content"]
        assert "GRANDMA MODE" not in system_content
        db.close()


class TestAgentRespond:
    @patch("windyfly.agent.loop.call_llm")
    def test_returns_response(self, mock_llm):
        _ch._tracker = None  # Reset context header singleton
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        response = agent_respond(config, db, wq, "Hi there!", "test-session")
        assert "Hello! I'm Windy Fly." in response
        assert mock_llm.called

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_passes_config_to_llm(self, mock_llm, mock_online):
        _ch._tracker = None
        mock_llm.return_value = {
            "content": "Response",
            "model": "gpt-4o-mini",
            "input_tokens": 50,
            "output_tokens": 10,
            "tool_calls": None,
        }

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(config, db, wq, "Test", "test-session")

        call_kwargs = mock_llm.call_args
        assert call_kwargs is not None

        wq.stop()
        db.close()


class TestFactExtraction:
    def test_extracts_name(self):
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        _extract_and_store_facts(db, wq, "My name is Grant")

        time.sleep(0.5)
        wq.stop()

        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'person'")
        assert len(nodes) >= 1
        db.close()

    def test_extracts_location(self):
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        _extract_and_store_facts(db, wq, "I live in San Francisco")

        time.sleep(0.5)
        wq.stop()

        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'location'")
        assert len(nodes) >= 1
        db.close()

    def test_extracts_preference(self):
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        _extract_and_store_facts(db, wq, "I love dark mode")

        time.sleep(0.5)
        wq.stop()

        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'preference'")
        assert len(nodes) >= 1
        db.close()


class TestBudgetEnforcement:
    """R2: Budget enforcement should block when daily budget exceeded."""

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    @patch("windyfly.agent.loop.check_budget")
    def test_blocks_when_over_budget(self, mock_budget, mock_llm, mock_online):
        # Reset module-level singletons to avoid test-order pollution
        _ch._tracker = None
        import windyfly.agent.loop as _loop
        _loop._interaction_count = 0
        _loop._session_tokens_used = 0

        mock_budget.return_value = {
            "allowed": False,
            "daily_spend": 5.50,
            "daily_budget": 5.0,
            "warning": True,
            "monthly_spend": 10.0,
        }

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        response = agent_respond(config, db, wq, "Hi", "test-session")
        assert "budget" in response.lower()
        assert not mock_llm.called

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    @patch("windyfly.agent.loop.check_budget")
    def test_warns_when_near_budget(self, mock_budget, mock_llm, mock_online):
        _ch._tracker = None
        import windyfly.agent.loop as _loop
        _loop._interaction_count = 0
        mock_budget.return_value = {
            "allowed": True,
            "daily_spend": 3.50,
            "daily_budget": 5.0,
            "warning": True,
            "monthly_spend": 10.0,
        }
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        response = agent_respond(config, db, wq, "Hi", "test-session")
        # LLM should still be called
        assert mock_llm.called
        # Response should be from LLM, not budget block
        assert "Hello! I'm Windy Fly." in response

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    @patch("windyfly.agent.loop.check_budget")
    def test_warning_alert_does_not_leak_into_user_messages(
        self, mock_budget, mock_llm, mock_online,
    ):
        """Regression: stress harness v4 found the budget warning was
        being injected as a system prompt instructing the LLM to repeat
        it to the user. After 80% of budget, every Telegram reply was
        prefixed with "Heads up: I've used $X.XX of your $5.00 daily
        budget" — the bot's own ops state leaking into user chat.

        Warning-tier alerts must be logged for the operator only; the
        messages list passed to call_llm must not contain the alert."""
        _ch._tracker = None
        import windyfly.agent.loop as _loop
        _loop._interaction_count = 0
        _loop._session_tokens_used = 0

        mock_budget.return_value = {
            "allowed": True,
            "daily_spend": 4.25,
            "daily_budget": 5.0,
            "daily_percent": 85.0,
            "warning": True,
            "monthly_spend": 4.25,
            "monthly_budget": 0,
            "alert": (
                "Heads up: I've used $4.25 of your $5.00 daily budget (85%)."
            ),
        }
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(config, db, wq, "Hi", "test-session")

        assert mock_llm.called, "LLM should still be called when allowed"
        sent_messages = mock_llm.call_args.args[0]
        joined = " ".join(
            m.get("content", "") for m in sent_messages
            if isinstance(m.get("content"), str)
        )
        assert "Heads up: I've used" not in joined, (
            f"Budget alert leaked into LLM messages: {joined!r}"
        )
        assert "tell the user this budget update" not in joined, (
            "System-prompt directive to repeat budget to user must be removed"
        )

        wq.stop()
        db.close()


class TestEmotionalContextIntegration:
    """R3: Emotional context should be detected and stored on episodes."""

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_emotional_context_passed_to_episode(self, mock_llm, mock_online):
        _ch._tracker = None
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(
            config, db, wq,
            "UGH this is SO FRUSTRATING!!!",
            "test-session",
        )
        time.sleep(0.5)
        wq.stop()

        episodes = db.fetchall(
            "SELECT * FROM episodes WHERE session_id = 'test-session'"
        )
        user_eps = [e for e in episodes if e["role"] == "user"]
        assert len(user_eps) >= 1
        assert any(e.get("emotional_context") == "stressed" for e in user_eps)

        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_neutral_context_for_normal_message(self, mock_llm, mock_online):
        _ch._tracker = None
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(config, db, wq, "Hello there", "test-session")
        wq._queue.join()
        wq.stop()

        episodes = db.fetchall(
            "SELECT * FROM episodes WHERE session_id = 'test-session'"
        )
        user_eps = [e for e in episodes if e["role"] == "user"]
        assert len(user_eps) >= 1
        assert any(e.get("emotional_context") == "neutral" for e in user_eps)

        db.close()


class TestIntentDetectionIntegration:
    """R4: Intents should be detected and stored from user messages."""

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_intent_saved_from_message(self, mock_llm, mock_online):
        _ch._tracker = None
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(
            config, db, wq,
            "I want to learn French",
            "test-session",
        )
        wq._queue.join()  # Block until all enqueued items are processed
        wq.stop()

        intents = db.fetchall("SELECT * FROM intents")
        assert len(intents) >= 1
        # The extracted description should contain learn/French
        descriptions = " ".join(i["description"] for i in intents)
        assert "learn" in descriptions.lower() or "french" in descriptions.lower()

        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_no_intent_for_greeting(self, mock_llm, mock_online):
        _ch._tracker = None
        mock_llm.return_value = _LLM_RESPONSE.copy()

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(config, db, wq, "Hello!", "test-session")
        time.sleep(0.5)
        wq.stop()

        intents = db.fetchall("SELECT * FROM intents")
        assert len(intents) == 0

        db.close()


class TestToolExecution:
    """R1/R9: Tool schemas should be passed to LLM and tool_calls executed."""

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_tools_passed_to_llm(self, mock_llm, mock_online):
        _ch._tracker = None
        mock_llm.return_value = _LLM_RESPONSE.copy()

        from windyfly.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register("test_tool", "A test tool", {"type": "object", "properties": {}}, lambda: "ok")

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(config, db, wq, "Hi", "test-session", registry)

        call_args = mock_llm.call_args
        assert call_args is not None
        # tools kwarg should contain the registered tool
        _, kwargs = call_args
        assert kwargs.get("tools") is not None
        assert len(kwargs["tools"]) == 1

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_tool_reloop_executes_tools(self, mock_llm, mock_online):
        """When LLM returns tool_calls, agent executes and calls LLM again."""
        _ch._tracker = None
        from windyfly.tools.registry import ToolRegistry
        registry = ToolRegistry()
        registry.register(
            "get_weather", "Get weather", {"type": "object", "properties": {}},
            lambda: '{"temp": "72F"}',
        )

        # First call returns tool_calls, second call returns final response
        mock_llm.side_effect = [
            {
                "content": "",
                "model": "gpt-4o-mini",
                "input_tokens": 50,
                "output_tokens": 10,
                "tool_calls": [{
                    "id": "call_123",
                    "function": {"name": "get_weather", "arguments": {}},
                }],
            },
            {
                "content": "The weather is 72F!",
                "model": "gpt-4o-mini",
                "input_tokens": 80,
                "output_tokens": 15,
                "tool_calls": None,
            },
        ]

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        response = agent_respond(config, db, wq, "What's the weather?", "test-session", registry)
        assert "The weather is 72F!" in response
        assert mock_llm.call_count == 2  # Initial + after tool

        wq.stop()
        db.close()


class TestEpistemicFiltering:
    """R6: Epistemic strictness should filter nodes in prompt assembly."""

    def test_high_strictness_filters_inferred(self):
        config = _make_config()
        config["personality"]["epistemic_strictness"] = 8
        db = _make_db()

        # Insert nodes with different epistemic statuses
        from windyfly.memory.nodes import upsert_node
        upsert_node(db, "fact", "test_verified", epistemic_status="verified")
        upsert_node(db, "fact", "test_inferred", epistemic_status="inferred")
        upsert_node(db, "fact", "test_speculative", epistemic_status="speculative")

        messages = assemble_prompt(config, db, "test topic", "test-session")

        # Find knowledge section in messages
        knowledge_msgs = [m for m in messages if "Relevant Knowledge" in m.get("content", "")]
        if knowledge_msgs:
            content = knowledge_msgs[0]["content"]
            # Inferred and speculative should be filtered out at strictness > 7
            assert "[INFERRED]" not in content
            assert "[SPECULATIVE]" not in content

        db.close()
