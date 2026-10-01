"""Found by the 0.7.2.1 clean-machine proof from PyPI (2026-09-23).

`windy go </dev/null` on an already set-up agent died with an EOFError
traceback at a setup prompt. With no terminal, every prompt now takes its
stated default. (The dead-passport cases for the terminal hatch went with
it in 0.7.5 — ADR-059, one hallway.)
"""

from __future__ import annotations

import io

import pytest
from rich.prompt import Confirm, Prompt

from windyfly import prompts


@pytest.fixture()
def no_terminal(monkeypatch):
    """stdin at EOF, as with `windy go </dev/null`."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))


# ── 1. no terminal: prompts take their default ────────────────────────

@pytest.mark.parametrize("default", [True, False, "1", "", "Windy Fly"])
def test_ask_returns_the_default_on_eof(default, no_terminal, capsys):
    ask_fn = Confirm.ask if isinstance(default, bool) else Prompt.ask
    assert prompts.ask(ask_fn, "  Question?", default=default) == default
    assert "no terminal to answer" in capsys.readouterr().out


def test_ask_passes_answers_through_when_there_is_a_terminal():
    assert prompts.ask(lambda q, default: "typed", "Q?", default="x") == "typed"
