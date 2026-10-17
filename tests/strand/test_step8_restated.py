"""Strand rows restated on the record in step 8 of the path to green (Grant's GO 10-17)."""

from __future__ import annotations

from itertools import product
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from windyfly.memory.write_queue import Priority, WriteQueue
from windyfly.personality.engine import build_personality_block

SRC = Path(__file__).resolve().parents[2] / "src" / "windyfly"


# ── P1.03.21: MEDIUM writes are batched for fairness; one failure never stops the rest ─────────

def test_p1_03_21_medium_writes_run_in_batches_and_one_failure_never_stops_the_rest():
    ran: list[int] = []

    def ok(i):
        ran.append(i)

    def bad(i):
        raise RuntimeError("boom")

    wq = WriteQueue()
    wq.start()
    try:
        for i in range(3):
            wq.enqueue(Priority.MEDIUM, ok, i)
        wq.enqueue(Priority.MEDIUM, bad, 99)
        for i in range(3, 6):
            wq.enqueue(Priority.MEDIUM, ok, i)
    finally:
        wq.stop()
    assert ran == [0, 1, 2, 3, 4, 5]


# ── P1.04.6: the owner's SOUL.md is passed whole in raw mode; added slider text is bounded ─────

def test_p1_04_6_the_soul_is_whole_in_raw_mode_and_the_slider_text_is_bounded():
    soul = "\n".join(f"- line {i}: witty, warm and caring" for i in range(400))      # about 13k characters
    assert build_personality_block(soul, {"humor": 0, "personality": 0}, raw=True).startswith(soul)
    keys = ("humor", "formality", "verbosity", "proactivity", "reasoning_depth", "autonomy", "personality")
    short = "X" * 10
    worst = max(len(build_personality_block(short, dict(zip(keys, vals)))) - len(short)
                for vals in product((0, 2, 5, 8, 10), repeat=len(keys)))
    assert worst <= 800


# ── P4.11.15: replies are m.text, so Windy Chat's push covers them (m.notice is never pushed) ─

@pytest.mark.asyncio
async def test_p4_11_15_replies_go_out_as_m_text_so_they_are_pushed():
    from tests.strand.test_p2_matrix import ROOM, _bot, _event

    bot = _bot()
    bot.client.room_read_markers = AsyncMock()
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    room, ev = _event()
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="here you go"):
        await bot._on_message(room, ev)
    sent = [c for c in bot.client.room_send.await_args_list if c.args[1] == "m.room.message"]
    assert sent and all(c.args[2]["msgtype"] == "m.text" for c in sent)
    assert sent[0].args[0] == ROOM
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py") if "m.notice" in p.read_text()]
    assert offenders == []                                   # Synapse's suppress_notices drops m.notice pushes
