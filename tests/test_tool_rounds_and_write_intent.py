"""Tool-round budget + write-intent tripwire regression suite (PR #165).

Surfaced 2026-05-10 by a TURNOVER.md write failure. Log evidence:

  17:12:33 [req:dceff021] agent_respond start session=...
  17:12:39 LLM picked: github.list_repo × 2 (round 1)
  17:12:47 Executing tool: github.fetch_file × 4 (round 2)
  17:12:55 Executing tool: github.fetch_file × 3 (round 3)
  [tool loop exits — no round 4 available]
  17:14    bot replies: "Now let me write the updated TURNOVER.md..."
           — NO github.put_file call this turn
  17:58    Grant: "??"
           [same 3-rounds-of-reads-then-text repeats]

Two issues + two fixes:

  1. **Tool round budget too small.** _DEFAULT_TOOL_ROUNDS was 3.
     A common read-discover-read-write pattern needs at least 4.
     Bumped to 5 (one round of headroom).

  2. **No telemetry for "intent-to-write without execution".**
     The bot says it will write but never invokes a write-class
     tool. Existing _looks_confabulated catches PAST-tense success
     claims; this is FORWARD-LOOKING commitments. New tripwire
     logs agent.write_intent_unexecuted so we can dashboard the
     pattern and decide if 5 rounds is enough.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch


# ─── 1. Round budget value pinned ────────────────────────────────


class TestRoundBudget:

    def test_default_is_five(self):
        from windyfly.agent.loop import _DEFAULT_TOOL_ROUNDS
        assert _DEFAULT_TOOL_ROUNDS == 5, (
            "Read-discover-then-write needs at least 4 rounds. "
            "Default lifted to 5 (one round of headroom). If you're "
            "thinking of lowering it, read the PR #165 description "
            "first — 3 rounds caused a real TURNOVER.md write failure."
        )


# ─── 2. Write-class tool detection ───────────────────────────────


class TestWriteClassToolDetector:

    def test_no_tool_calls_returns_false(self):
        from windyfly.agent.loop import _was_write_class_tool_invoked
        assert _was_write_class_tool_invoked(None) is False
        assert _was_write_class_tool_invoked([]) is False

    def test_only_read_tools_returns_false(self):
        from windyfly.agent.loop import _was_write_class_tool_invoked
        calls = [
            {"function": {"name": "github.fetch_file"}},
            {"function": {"name": "github.list_repo"}},
            {"function": {"name": "web_search"}},
        ]
        assert _was_write_class_tool_invoked(calls) is False

    def test_write_tool_returns_true(self):
        from windyfly.agent.loop import _was_write_class_tool_invoked
        calls = [
            {"function": {"name": "github.fetch_file"}},
            {"function": {"name": "github.put_file"}},
        ]
        assert _was_write_class_tool_invoked(calls) is True

    def test_fs_write_recognized(self):
        from windyfly.agent.loop import _was_write_class_tool_invoked
        calls = [{"function": {"name": "fs.write_file"}}]
        assert _was_write_class_tool_invoked(calls) is True

    def test_shell_exec_recognized(self):
        """shell.exec is treated as write-class because it can do
        anything — conservative, prevents tripwire false-positive."""
        from windyfly.agent.loop import _was_write_class_tool_invoked
        calls = [{"function": {"name": "shell.exec"}}]
        assert _was_write_class_tool_invoked(calls) is True

    def test_handles_missing_function_wrapper(self):
        from windyfly.agent.loop import _was_write_class_tool_invoked
        # Some tool-call shapes have flat 'name' not nested under 'function'.
        calls = [{"name": "github.put_file"}]
        assert _was_write_class_tool_invoked(calls) is True


# ─── 3. Write-intent text detector ───────────────────────────────


class TestWriteIntentDetector:

    def test_screenshot_phrase_detected(self):
        """The exact phrasing from the 2026-05-10 screenshot."""
        from windyfly.agent.loop import _looks_write_intent_unexecuted
        text = (
            "Here's my synthesis. Now let me write the updated "
            "TURNOVER.md to my repo:"
        )
        hit, marker = _looks_write_intent_unexecuted(text, False)
        assert hit is True
        assert marker is not None
        assert "let me write" in marker

    def test_alternate_phrasings_detected(self):
        from windyfly.agent.loop import _looks_write_intent_unexecuted
        cases = [
            "I'll write this to your GitHub now.",
            "Now I'm writing the file to your repo.",
            "I'll commit these changes shortly.",
            "Let me save that for you.",
            "Let me create the file.",
            "I'll push this update.",
        ]
        for text in cases:
            hit, _ = _looks_write_intent_unexecuted(text, False)
            assert hit is True, f"missed: {text!r}"

    def test_write_already_invoked_suppresses_trip(self):
        """The key gating rule: if a write tool WAS invoked, the
        intent text is now an HONEST description of what happened —
        no tripwire."""
        from windyfly.agent.loop import _looks_write_intent_unexecuted
        text = "Now let me write the updated file to your repo..."
        hit, _ = _looks_write_intent_unexecuted(text, write_tool_was_invoked=True)
        assert hit is False

    def test_unrelated_text_does_not_trip(self):
        from windyfly.agent.loop import _looks_write_intent_unexecuted
        cases = [
            "Here's a summary of what I found.",
            "Brian Hill is a loan officer in Austin.",
            "I don't have a tool for that.",
            "",
        ]
        for text in cases:
            hit, _ = _looks_write_intent_unexecuted(text, False)
            assert hit is False, f"false positive: {text!r}"

    def test_empty_or_none(self):
        from windyfly.agent.loop import _looks_write_intent_unexecuted
        assert _looks_write_intent_unexecuted(None, False) == (False, None)
        assert _looks_write_intent_unexecuted("", False) == (False, None)


# ─── 4. End-to-end agent_respond integration ─────────────────────


def _make_config():
    return {
        "agent": {"default_model": "claude-sonnet-4-6",
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


class TestEndToEndTripwireFires:

    def test_intent_text_no_write_logs_event(self, stack):
        """The exact screenshot scenario: bot says "Now let me write..."
        with no write-class tool call → agent.write_intent_unexecuted
        event fires."""
        config, db, wq = stack
        result = {
            "content": "Now let me write the updated TURNOVER.md to my repo:",
            "input_tokens": 100, "output_tokens": 30,
            "tool_calls": None, "model": "claude-sonnet-4-6",
            "citations": [], "server_tools_used": 0,
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=result), \
             patch("windyfly.agent.loop.log_event") as mock_log:
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "write TURNOVER.md to my repo", "s1")

        names = [c.args[2] for c in mock_log.call_args_list]
        assert "agent.write_intent_unexecuted" in names

    def test_clean_response_does_not_log_event(self, stack):
        """A reply that doesn't mention writing → no event."""
        config, db, wq = stack
        result = {
            "content": "Here's a summary of what I learned.",
            "input_tokens": 100, "output_tokens": 30,
            "tool_calls": None, "model": "claude-sonnet-4-6",
            "citations": [], "server_tools_used": 0,
        }
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", return_value=result), \
             patch("windyfly.agent.loop.log_event") as mock_log:
            from windyfly.agent.loop import agent_respond
            agent_respond(config, db, wq, "summarize", "s2")

        names = [c.args[2] for c in mock_log.call_args_list]
        assert "agent.write_intent_unexecuted" not in names
