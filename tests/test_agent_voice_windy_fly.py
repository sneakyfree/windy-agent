"""Boss TILE RULINGS (BOARD 2026-11-06T19:19:22Z): "Windy Fly" is the app; the agent is "your Windy Fly agent".
Where the AGENT speaks as itself, it never says it IS Windy Fly. (Process/CLI/log strings name the software and stay.)"""

from __future__ import annotations

from windyfly.agent.welcome import WELCOME_TEXT, format_welcome


def test_the_unnamed_welcome_says_your_windy_fly_agent() -> None:
    assert "I'm your Windy Fly agent" in WELCOME_TEXT
    assert "I'm Windy Fly" not in WELCOME_TEXT
    assert format_welcome({"agent": {"name": "Windy Fly"}}) == WELCOME_TEXT


def test_a_named_agent_still_introduces_itself_by_its_own_name() -> None:
    assert "I'm Zed —" in format_welcome({"agent": {"name": "Zed"}})


def test_the_matrix_greeting_and_help_header_use_the_agent_wording() -> None:
    from pathlib import Path
    src = Path(__file__).resolve().parents[1] / "src" / "windyfly"
    bot = (src / "channels" / "matrix_bot.py").read_text(encoding="utf-8")
    assert "Hey! I'm your Windy Fly agent" in bot and "Hey! I'm Windy Fly," not in bot
    reg = (src / "commands" / "registry.py").read_text(encoding="utf-8")
    assert "Your Windy Fly agent — what I can do" in reg
