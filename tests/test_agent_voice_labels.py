"""Boss ruling BOARD 2026-11-07T15:55:25Z on #749: the agent's own LABELS follow Grant's 11-06 name, "Windy Fly Agent"
(the reply signature header, the Matrix presence lines, the passport description sent at hatch)."""

from __future__ import annotations

from pathlib import Path

from windyfly.eternitas.models import RegistrationRequest

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"


def test_the_passport_description_names_the_agent() -> None:
    assert RegistrationRequest(name="x").description == "Windy Fly Agent, a personal AI assistant"


def test_the_matrix_presence_lines_name_the_agent() -> None:
    bot = (SRC / "channels" / "matrix_bot.py").read_text(encoding="utf-8")
    assert 'status_msg="Windy Fly Agent is ready 🪰"' in bot
    assert 'status_msg="Windy Fly Agent shutting down"' in bot
    assert 'status_msg="Windy Fly is ready' not in bot and 'status_msg="Windy Fly shutting down"' not in bot
