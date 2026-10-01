"""The offline queue is never replayed through the model; the owner is told the count."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.agent import offline


@pytest.fixture
def queue_path(tmp_path, monkeypatch):
    p = tmp_path / "offline_queue.json"
    monkeypatch.setattr(offline, "_QUEUE_PATH", p)
    return p


def test_nothing_goes_through_the_model(queue_path):
    for i in range(3):
        offline.queue_message(f"m{i}", "s1")
    with patch("windyfly.agent.loop.agent_respond") as ar:
        assert offline.replay_queued_messages({}, MagicMock(), MagicMock()) == 3
    ar.assert_not_called()
    assert offline.get_queued_messages() == []


def test_empty_queue_is_a_no_op(queue_path):
    assert offline.replay_queued_messages({}, MagicMock(), MagicMock()) == 0


def test_notice_wording():
    assert offline.offline_notice(1) == (
        "While I was offline you sent 1 message. Ask again if you still need anything from them.")
    assert offline.offline_notice(11).startswith("While I was offline you sent 11 messages.")


@pytest.mark.asyncio
async def test_matrix_bot_tells_the_owner(queue_path):
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue

    offline.queue_message("hello?", "s1")
    bot = WindyFlyMatrixBot({"matrix": {"homeserver": "https://x", "user_id": "@windyfly:x",
                                        "access_token": "t"}}, Database(":memory:"), WriteQueue())
    bot._hatch_dm_room_id = "!dm:x"
    bot.client.room_send = AsyncMock()
    await bot._replay_offline_queue()
    args = bot.client.room_send.call_args[0]
    assert args[0] == "!dm:x" and "While I was offline you sent 1 message" in args[2]["body"]
