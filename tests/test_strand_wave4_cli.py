"""Strand-to-green Wave 4, C0.8: a plain (non-slash) CLI chat turn.

Drives ``windyfly.channels.cli.run_cli`` with scripted terminal input and
proves an ordinary message goes through ``agent_respond`` to the model
(mocked) and the reply is printed as ``Fly: <reply>``.

The existing CLI tests only cover the slash-command path
(test_cli_rescue_routing.py). Two shapes here:

* the default suite shape (first-contact welcome disabled by the
  conftest autouse fixture), and
* a brand-new database with the real first-contact welcome left ON
  (``virgin_db_welcome``): message 1 gets the one-time tour, message 2
  reaches the model.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

import windyfly.channels.cli as cli_mod


def _llm_result(content: str) -> dict:
    return {
        "content": content,
        "input_tokens": 5,
        "output_tokens": 5,
        "tool_calls": None,
        "model": "claude-haiku-4-5-20251001",
        "citations": [],
        "server_tools_used": 0,
    }


def _config(tmp_path) -> dict:
    return {
        "agent": {
            "default_model": "claude-haiku-4-5-20251001",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {
            "db_path": str(tmp_path / "cli-wave4.db"),
            "max_episodes_per_context": 20,
            "max_nodes_per_context": 10,
        },
        "personality": {"soul_path": "SOUL.md", "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


def _drive(inputs, config, fake_llm):
    """Run the CLI loop over ``inputs`` (then Ctrl-D); return printed lines."""
    it = iter(inputs)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            raise EOFError

    printed: list[str] = []
    with patch.object(cli_mod.console, "input", side_effect=fake_input), \
         patch.object(cli_mod.console, "print",
                      side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))), \
         patch("windyfly.agent.loop.call_llm", side_effect=fake_llm), \
         patch("windyfly.agent.loop.is_online", return_value=True):
        cli_mod.run_cli(config)
    return printed


def test_C0_8_cli_plain_message_reaches_model_and_reply_is_printed(tmp_path):
    seen_user_messages: list[str] = []

    def fake_llm(messages, **kwargs):
        if kwargs.get("session_id"):
            seen_user_messages.append(messages[-1]["content"])
        return _llm_result("Paris is the capital of France.")

    printed = _drive(["What is the capital of France?"], _config(tmp_path), fake_llm)

    assert seen_user_messages == ["What is the capital of France?"]
    fly_lines = [p for p in printed if p.startswith("[fly]Fly:[/fly]")]
    assert fly_lines == ["[fly]Fly:[/fly] Paris is the capital of France."]
    assert not any("Error" in p for p in printed)
    # The loop exited cleanly on Ctrl-D and shut down.
    assert any("Goodbye" in p for p in printed)


@pytest.mark.virgin_db_welcome
def test_C0_8_cli_first_message_gets_welcome_then_chat_turn_reaches_model(tmp_path):
    """Real first contact on an empty DB: the one-time tour answers the
    first message WITHOUT a model call; the next message is a normal turn."""
    from windyfly.agent.welcome import format_welcome

    model_calls: list[str] = []

    def fake_llm(messages, **kwargs):
        if kwargs.get("session_id"):
            model_calls.append(messages[-1]["content"])
        return _llm_result("Sure: two plus two is four.")

    config = _config(tmp_path)
    printed = _drive(["hello", "what is two plus two?"], config, fake_llm)

    fly_lines = [p for p in printed if p.startswith("[fly]Fly:[/fly]")]
    assert len(fly_lines) == 2
    assert fly_lines[0] == f"[fly]Fly:[/fly] {format_welcome(config)}"
    assert fly_lines[1] == "[fly]Fly:[/fly] Sure: two plus two is four."
    assert model_calls == ["what is two plus two?"]


def test_C0_8_cli_quit_word_ends_session_without_model_call(tmp_path):
    calls: list[int] = []

    def fake_llm(messages, **kwargs):
        calls.append(1)
        return _llm_result("should not be called")

    printed = _drive(["quit", "never read"], _config(tmp_path), fake_llm)
    assert calls == []
    assert not any(p.startswith("[fly]Fly:[/fly]") for p in printed)
