"""Regression: confab-retry messages array must end with role=user.

Anthropic's API requires the final message in the array to be
role=user (unless using the explicit-prefill assistant flow). Our
adapter ``_openai_messages_to_anthropic`` strips ALL system messages
out and concatenates them into the top-level ``system`` kwarg.

The two confabulation guards in ``loop.py`` (regular at section 2.6
and self-env at section 2.7) used to append the retry directive with
``role=system``. After stripping, the messages array ended with
role=assistant — and Anthropic returned

    HTTP 400: This model does not support assistant message prefill.
    The conversation must end with a user message.

Surfaced 2026-05-11 by the overnight stress harness on prompt #130
("Research the 'down payment assistance' programs available in Texas.").
The exception bubbled all the way up: response_len=0, the user got
nothing, and the bot ate a RuntimeError. Both confab guards were
broken — both fail open under any provider whose adapter pulls
system messages out of the messages list.

This test pins both guards' retry payloads to terminate with
role=user so the bug stays fixed.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


def _make_config():
    return {
        "agent": {
            "default_model": "claude-haiku-4-5-20251001",
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
            "humor_level": 7, "formality": 4, "proactivity": 5,
            "verbosity": 5, "reasoning_depth": 6, "autonomy": 3,
            "epistemic_strictness": 5,
        },
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


class TestConfabRetryMessagesEndWithUser:

    def test_regular_confab_retry_messages_end_with_user(self, stack):
        """When the action-success guard fires its retry, the messages
        list passed to the retry call_llm must end with role=user."""
        config, db, wq = stack

        # First call returns a confabulation: a "Done!" claim with
        # no tool calls (action prompt + text-only fake success).
        initial = {
            "content": "Done! I've created the file.",
            "input_tokens": 5,
            "output_tokens": 5,
            "tool_calls": None,
            "model": "claude-sonnet-4-6",
            "citations": [],
            "server_tools_used": 0,
        }
        # Retry returns a clean truth admission (any non-confab text
        # works — we just need to inspect the messages passed to it).
        retry = {
            "content": "I cannot create files in this turn.",
            "input_tokens": 5,
            "output_tokens": 5,
            "tool_calls": None,
            "model": "claude-sonnet-4-6",
            "citations": [],
            "server_tools_used": 0,
        }

        captured_calls: list[list[dict]] = []

        def fake_call_llm(messages, **_kwargs):
            captured_calls.append([dict(m) for m in messages])
            return initial if len(captured_calls) == 1 else retry

        from windyfly.agent.loop import agent_respond
        with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
             patch("windyfly.agent.loop.is_online", return_value=True):
            agent_respond(config, db, wq,
                          "Create a new file called test.txt with hello in it",
                          "session-confab-retry")

        # Two LLM calls: initial + retry. The retry's messages must end with user.
        assert len(captured_calls) >= 2, \
            f"expected initial + retry, got {len(captured_calls)} calls"
        retry_messages = captured_calls[1]
        assert retry_messages, "retry got empty messages"
        last = retry_messages[-1]
        assert last["role"] == "user", (
            f"retry messages must end with role=user; ended with "
            f"role={last['role']!r}. Anthropic would reject. Full tail: "
            f"{retry_messages[-3:]}"
        )
        # And the retry directive should be in that final user message,
        # tagged as a system reminder so the model treats it as meta.
        assert "[SYSTEM REMINDER]" in last["content"], (
            f"retry directive missing meta tag in last user message: "
            f"{last['content'][:200]!r}"
        )

    def test_self_env_confab_retry_messages_end_with_user(self, stack):
        """When the self-env guard fires its retry, same contract."""
        config, db, wq = stack

        # Confabulated refusal claiming a fake sandbox limit.
        initial = {
            "content": (
                "I'm in a Docker sandbox with --network=none, so I "
                "cannot make outbound HTTP calls."
            ),
            "input_tokens": 5,
            "output_tokens": 5,
            "tool_calls": None,
            "model": "claude-sonnet-4-6",
            "citations": [],
            "server_tools_used": 0,
        }
        retry = {
            "content": "Sorry — I should have just tried. Let me proceed.",
            "input_tokens": 5,
            "output_tokens": 5,
            "tool_calls": None,
            "model": "claude-sonnet-4-6",
            "citations": [],
            "server_tools_used": 0,
        }

        captured_calls: list[list[dict]] = []

        def fake_call_llm(messages, **_kwargs):
            captured_calls.append([dict(m) for m in messages])
            return initial if len(captured_calls) == 1 else retry

        from windyfly.agent.loop import agent_respond
        with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
             patch("windyfly.agent.loop.is_online", return_value=True):
            agent_respond(config, db, wq,
                          "Hit github.com/anthropics and fetch their homepage",
                          "session-self-env-retry")

        assert len(captured_calls) >= 2, \
            f"expected initial + retry, got {len(captured_calls)} calls"
        retry_messages = captured_calls[1]
        last = retry_messages[-1]
        assert last["role"] == "user", (
            f"self-env retry must end with role=user; ended with "
            f"role={last['role']!r}"
        )
        assert "[SYSTEM REMINDER]" in last["content"], (
            f"self-env retry directive missing meta tag: "
            f"{last['content'][:200]!r}"
        )

    def test_anthropic_adapter_after_strip_ends_with_user(self):
        """Direct adapter test: simulate the post-confab messages
        and verify the adapter output ends with role=user.

        This is the precise regression: feed the adapter the shape
        the buggy code used to produce ([..., assistant, system])
        and confirm the fix shape ([..., assistant, user]) survives
        the system-strip pass intact."""
        from windyfly.agent.models import _openai_messages_to_anthropic

        # The PRE-FIX shape — should leave only [..., assistant].
        pre_fix = [
            {"role": "user", "content": "do a thing"},
            {"role": "assistant", "content": "Done!"},
            {"role": "system", "content": "STOP. retry properly."},
        ]
        _, api_msgs_buggy = _openai_messages_to_anthropic(pre_fix)
        assert api_msgs_buggy[-1]["role"] == "assistant", (
            "pre-fix shape: adapter must strip system → ends with assistant "
            "(which Anthropic rejects — this is the documented bug)"
        )

        # The POST-FIX shape — should leave [..., assistant, user].
        post_fix = [
            {"role": "user", "content": "do a thing"},
            {"role": "assistant", "content": "Done!"},
            {"role": "user", "content": "[SYSTEM REMINDER] retry properly."},
        ]
        _, api_msgs_fixed = _openai_messages_to_anthropic(post_fix)
        assert api_msgs_fixed[-1]["role"] == "user", (
            "post-fix shape: messages must end with user role for Anthropic"
        )
