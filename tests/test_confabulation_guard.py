"""Tests for the agent loop's confabulation guard.

Regression set for the 2026-04-21 live-smoke-battery incident: GLM-4.7
answered Grant's "write a tiny note at ~/scratch/test-undo.md" with
"Done! Created `~/scratch/test-undo.md` with: hello" — and produced
zero tool_calls. Same for delete, undo, and a fake grep table. The
guard detects that pattern, retries once with a forcing system prompt,
and replaces the reply with a truthful fallback if the retry also
lies — so we never ship a fake success to the user.
"""

from __future__ import annotations

from unittest.mock import patch

from windyfly.agent.loop import _looks_confabulated, agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
import windyfly.agent.context_header as _ch


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
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


def _setup():
    _ch._tracker = None
    db = Database(":memory:")
    wq = WriteQueue()
    wq.start()
    return db, wq


def _teardown(db, wq):
    wq.stop()
    db.close()


# ── _looks_confabulated: unit-level heuristic ─────────────────────────


class TestLooksConfabulated:
    def test_write_request_with_done_claim(self):
        assert _looks_confabulated(
            "write a tiny note at ~/scratch/test.md saying hello",
            "Done! Created `~/scratch/test.md` with: hello",
        )

    def test_delete_request_with_done_claim(self):
        assert _looks_confabulated(
            "delete ~/scratch/test.md",
            "Done! Deleted `~/scratch/test.md`.",
        )

    def test_undo_request_with_restored_claim(self):
        assert _looks_confabulated(
            "wait, undo that",
            "Done! Restored the previous state.",
        )

    def test_grep_request_with_found_table(self):
        assert _looks_confabulated(
            "search my windy-agent repo for the word 'capability'",
            "Found 48 matches across 13 files in ~/windy-agent",
        )

    def test_pure_qa_without_action_trigger_is_not_confab(self):
        # "What's up?" has no action verb — the reply can freely say
        # whatever without tripping the detector.
        assert not _looks_confabulated(
            "hey what's up?",
            "Not much! Here to help.",
        )

    def test_action_request_with_honest_refusal_is_not_confab(self):
        # LLM honestly says it can't — no success claim. Pass.
        assert not _looks_confabulated(
            "delete ~/scratch/test.md",
            "I can't do that — the path isn't in my allowlist.",
        )

    def test_acknowledgement_is_not_confab(self):
        # "I'll remember that" isn't a success claim for an action.
        assert not _looks_confabulated(
            "remember that I like pizza",
            "Got it, I'll keep that in mind.",
        )

    def test_empty_inputs_are_safe(self):
        assert not _looks_confabulated("", "Done!")
        assert not _looks_confabulated("delete foo", "")


# ── agent_respond integration: retry recovers ─────────────────────────


class TestGuardIntegration:
    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_retry_with_tools_recovers(self, mock_llm, mock_online):
        """First call: confabulates text-only. Retry: elects to call
        a real tool. Followup: summarizes the real tool result."""
        from windyfly.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            "write_file_shim", "Write a file.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            lambda path, content: '{"executed": true, "bytes_written": 5}',
        )

        mock_llm.side_effect = [
            # 1: confabulates
            {
                "content": "Done! Created `~/scratch/note.md` with: hello",
                "model": "gpt-4o-mini",
                "input_tokens": 40, "output_tokens": 20,
                "tool_calls": None,
            },
            # 2: retry elects tool use
            {
                "content": "",
                "model": "gpt-4o-mini",
                "input_tokens": 60, "output_tokens": 10,
                "tool_calls": [{
                    "id": "call_1",
                    "function": {
                        "name": "write_file_shim",
                        "arguments": '{"path": "~/scratch/note.md", "content": "hello"}',
                    },
                }],
            },
            # 3: summarizes real result
            {
                "content": "Wrote 5 bytes to ~/scratch/note.md.",
                "model": "gpt-4o-mini",
                "input_tokens": 80, "output_tokens": 15,
                "tool_calls": None,
            },
        ]

        config = _make_config()
        db, wq = _setup()
        try:
            response = agent_respond(
                config, db, wq,
                "write a tiny note at ~/scratch/note.md saying hello",
                "test-session",
                tool_registry=registry,
            )
            assert "Wrote 5 bytes" in response
            assert mock_llm.call_count >= 3  # initial + retry + followup
        finally:
            _teardown(db, wq)

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_second_confab_falls_back_to_truth(self, mock_llm, mock_online):
        """Both initial and retry are text-only confabulation. Guard
        replaces the reply with the truth fallback so the user sees
        'I almost made that up' instead of a fake success."""
        from windyfly.agent.loop import _CONFAB_TRUTH_FALLBACK

        # Side_effect returns the same confabulation for every call.
        # The journal/relationship-moment helpers downstream may make
        # extra LLM calls depending on module-level counters, so we
        # assert on response content, not exact call_count.
        mock_llm.return_value = {
            "content": "Done! Deleted `~/scratch/note.md`.",
            "model": "gpt-4o-mini",
            "input_tokens": 40, "output_tokens": 10,
            "tool_calls": None,
        }

        config = _make_config()
        db, wq = _setup()
        try:
            response = agent_respond(
                config, db, wq,
                "delete ~/scratch/note.md",
                "test-session",
            )
            assert _CONFAB_TRUTH_FALLBACK in response
            assert mock_llm.call_count >= 2  # initial + retry
        finally:
            _teardown(db, wq)

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_plain_qa_never_triggers_retry(self, mock_llm, mock_online):
        """Casual messages must not trigger the guard. A 'hey what's up?'
        → 'Not much!' exchange should cost exactly one LLM call."""
        mock_llm.return_value = {
            "content": "Not much, here to help!",
            "model": "gpt-4o-mini",
            "input_tokens": 20, "output_tokens": 10,
            "tool_calls": None,
        }

        config = _make_config()
        db, wq = _setup()
        try:
            before = mock_llm.call_count
            response = agent_respond(
                config, db, wq, "hey what's up?", "test-session",
            )
            assert "Not much" in response
            # Exactly one user-facing LLM call. Journal/relationship
            # helpers may add internal calls; we only guarantee the
            # confabulation retry did NOT fire by checking the response
            # is the initial content (not the truth fallback).
            from windyfly.agent.loop import _CONFAB_TRUTH_FALLBACK
            assert _CONFAB_TRUTH_FALLBACK not in response
            assert mock_llm.call_count > before
        finally:
            _teardown(db, wq)

    @patch("windyfly.agent.loop.is_online", return_value=True)
    @patch("windyfly.agent.loop.call_llm")
    def test_real_tool_call_bypasses_guard(self, mock_llm, mock_online):
        """When the LLM picks a tool up front, the guard never fires —
        even if the final summary text happens to contain 'Wrote'."""
        from windyfly.tools.registry import ToolRegistry

        registry = ToolRegistry()
        registry.register(
            "write_file_shim", "Write a file.",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            lambda path, content: '{"executed": true}',
        )

        mock_llm.side_effect = [
            {
                "content": "",
                "model": "gpt-4o-mini",
                "input_tokens": 40, "output_tokens": 10,
                "tool_calls": [{
                    "id": "c1",
                    "function": {
                        "name": "write_file_shim",
                        "arguments": '{"path": "~/scratch/note.md", "content": "hi"}',
                    },
                }],
            },
            {
                "content": "Wrote the file — all done.",  # would trip detector if the guard re-checked
                "model": "gpt-4o-mini",
                "input_tokens": 60, "output_tokens": 8,
                "tool_calls": None,
            },
        ]

        config = _make_config()
        db, wq = _setup()
        try:
            response = agent_respond(
                config, db, wq,
                "write ~/scratch/note.md with hi",
                "test-session",
                tool_registry=registry,
            )
            assert "Wrote" in response
            # Exactly the initial + post-tool LLM calls. Guard did not
            # double-fire. Journal helper may add an internal call —
            # content-based check is authoritative.
        finally:
            _teardown(db, wq)
