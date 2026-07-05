"""Regression: the CLI channel must route slash-commands through the shared
rescue-first handler, not the bare command registry.

Before this, ``run_cli`` called ``registry.execute`` directly, so the grandma
rescue kit (/normal, /resurrect, /pause, /resume) was invisible on the CLI —
and /normal is the exact command the Ollama-lifeboat banner tells the user to
type. It returned "Unknown command: normal". Proven live 2026-07-05.
"""

from __future__ import annotations

from unittest.mock import patch

import windyfly.channels.cli as cli_mod


def _run_cli_with_inputs(inputs, config):
    """Drive run_cli by feeding console.input a canned sequence."""
    it = iter(inputs)

    def fake_input(_prompt=""):
        try:
            return next(it)
        except StopIteration:
            # Simulate Ctrl-D to exit the loop cleanly.
            raise EOFError

    with patch.object(cli_mod.console, "input", side_effect=fake_input):
        cli_mod.run_cli(config)


def test_cli_slash_command_routes_through_handle_incoming(tmp_path):
    config = {"memory": {"db_path": str(tmp_path / "cli.db")}}

    async def _fake_handle(text, ctx):
        assert ctx.get("platform") == "terminal"
        return True, f"routed:{text}"

    with patch(
        "windyfly.channels.base.handle_incoming", side_effect=_fake_handle
    ) as spy:
        _run_cli_with_inputs(["/normal"], config)

    # The command must have been dispatched through the rescue-first path.
    called_texts = [c.args[0] for c in spy.call_args_list]
    assert "/normal" in called_texts


def test_cli_normal_is_not_unknown_command(tmp_path):
    """End-to-end through the real rescue layer: /normal must NOT come back
    as an 'Unknown command' the way the bare registry reported it."""
    config = {"memory": {"db_path": str(tmp_path / "cli.db")}}
    printed = []

    with patch.object(cli_mod.console, "print", side_effect=lambda *a, **k: printed.append(" ".join(str(x) for x in a))):
        _run_cli_with_inputs(["/normal"], config)

    joined = "\n".join(printed)
    assert "Unknown command" not in joined
