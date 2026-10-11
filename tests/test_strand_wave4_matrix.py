"""Strand-to-green Wave 4: Matrix channel yellows, proven with a mocked client.

* C1.5  typing indicator ON before the turn runs, OFF after the reply is sent.
* C1.6  presence is set to "online" at start, before the sync loop runs
        (and "offline" on the way out).
* Password login: with no token, ``login`` logs in with
  MATRIX_BOT_PASSWORD through nio (mocked), and a LoginError is fatal.
* Offline queue: a failed ``room_send`` of a reply lands in
  ``_pending_responses`` and a later ``_flush_pending`` sends it.

Same mocking style as tests/test_matrix_bot.py; no homeserver is contacted.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import nio
import pytest

from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

ROOM = "!wave4:chat.windychat.ai"
USER = "@grandma:chat.windychat.ai"


def _config(tmp_path) -> dict:
    return {
        "agent": {"default_model": "gpt-4o-mini", "max_context_tokens": 8000,
                  "max_response_tokens": 2000, "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20,
                   "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "epistemic_strictness": 5},
        "matrix": {
            "homeserver": "https://chat.windychat.ai",
            "bot_user": "@windyfly:chat.windychat.ai",
            "store_path": str(tmp_path / "matrix_store"),
        },
    }


@pytest.fixture
def bot(tmp_path):
    db = Database(":memory:")
    b = WindyFlyMatrixBot(_config(tmp_path), db, WriteQueue())
    yield b
    db.close()


def _room():
    room = MagicMock()
    room.room_id = ROOM
    room.user_name.return_value = "Grandma"
    return room


def _event(body: str = "How do I bake bread?"):
    event = MagicMock()
    event.sender = USER
    event.body = body
    event.server_timestamp = time.time() * 1000
    event.source = {"content": {}}
    return event


# ── C1.5 typing ─────────────────────────────────────────────────────


async def test_C1_5_typing_on_before_turn_and_off_after_reply(bot):
    timeline: list[tuple] = []

    async def typing(room_id, on, timeout=None):
        timeline.append(("typing", room_id, on))

    async def send(room_id, msgtype, content):
        timeline.append(("send", room_id, content["body"]))

    async def fake_run_turn(fn, *args, **kwargs):
        timeline.append(("turn",))
        return "Start with flour, water, salt and yeast."

    bot.client.room_typing = AsyncMock(side_effect=typing)
    bot.client.room_send = AsyncMock(side_effect=send)

    with patch("windyfly.agent.executor.run_turn", side_effect=fake_run_turn):
        await bot._on_message(_room(), _event())

    assert timeline == [
        ("typing", ROOM, True),
        ("turn",),
        ("send", ROOM, "Start with flour, water, salt and yeast."),
        ("typing", ROOM, False),
    ]
    # The ON call carries a timeout so a crashed turn cannot leave the
    # indicator stuck forever.
    on_call = bot.client.room_typing.await_args_list[0]
    assert on_call.kwargs.get("timeout")


async def test_C1_5_typing_cleared_even_when_turn_raises(bot):
    timeline: list[tuple] = []

    async def typing(room_id, on, timeout=None):
        timeline.append(("typing", on))

    async def send(room_id, msgtype, content):
        timeline.append(("send",))

    async def failing_turn(fn, *args, **kwargs):
        timeline.append(("turn",))
        raise RuntimeError("model exploded")

    bot.client.room_typing = AsyncMock(side_effect=typing)
    bot.client.room_send = AsyncMock(side_effect=send)

    with patch("windyfly.agent.executor.run_turn", side_effect=failing_turn):
        await bot._on_message(_room(), _event())

    assert timeline[0] == ("typing", True)
    assert ("turn",) in timeline
    assert timeline[-1] == ("typing", False)


# ── C1.6 presence ───────────────────────────────────────────────────


async def test_C1_6_presence_online_at_start_before_sync(bot):
    timeline: list[tuple] = []

    bot.login = AsyncMock()
    bot._auto_trust_devices = AsyncMock()
    bot._replay_offline_queue = AsyncMock()
    bot._setup_signal_handlers = MagicMock()  # leave the test loop's signals alone

    async def no_heartbeat():
        await asyncio.Event().wait()

    bot._heartbeat_loop = no_heartbeat

    async def presence(state, status_msg=None):
        timeline.append(("presence", state))

    sync_started = asyncio.Event()

    async def sync_forever(*a, **k):
        timeline.append(("sync",))
        sync_started.set()
        await asyncio.Event().wait()

    bot.client.set_presence = AsyncMock(side_effect=presence)
    bot.client.sync_forever = sync_forever
    bot.client.add_event_callback = MagicMock()
    bot.client.add_response_callback = MagicMock()
    bot.client.close = AsyncMock()

    start_task = asyncio.create_task(bot.start())
    await asyncio.wait_for(sync_started.wait(), timeout=5)

    assert timeline[0] == ("presence", "online")
    assert timeline.index(("presence", "online")) < timeline.index(("sync",))
    first = bot.client.set_presence.await_args_list[0]
    assert first.args[0] == "online"

    bot._handle_shutdown_signal(15, asyncio.get_running_loop())
    await asyncio.wait_for(start_task, timeout=5)
    assert timeline[-1] == ("presence", "offline")


# ── password login ──────────────────────────────────────────────────


@patch.dict("os.environ", {"MATRIX_BOT_PASSWORD": "pw-for-tests-only"}, clear=True)
async def test_matrix_password_login_when_no_token(bot):
    bot.client.login = AsyncMock(return_value=MagicMock(name="LoginResponse"))
    bot.client.whoami = AsyncMock(return_value=MagicMock(user_id=bot.bot_user_id))
    bot._setup_encryption = AsyncMock()

    with patch("windyfly.chat_session.fetch_agent_chat_session",
               new=AsyncMock(return_value=None)):
        await bot.login()

    bot.client.login.assert_awaited_once_with("pw-for-tests-only")
    bot._setup_encryption.assert_awaited_once()
    # Identity is still realigned from the token the login produced.
    bot.client.whoami.assert_awaited_once()


@patch.dict("os.environ", {"MATRIX_BOT_PASSWORD": "wrong-pw"}, clear=True)
async def test_matrix_password_login_error_is_fatal(bot):
    bot.client.login = AsyncMock(return_value=nio.LoginError("Invalid password"))
    bot._setup_encryption = AsyncMock()

    with patch("windyfly.chat_session.fetch_agent_chat_session",
               new=AsyncMock(return_value=None)):
        with pytest.raises(RuntimeError, match="Matrix login failed"):
            await bot.login()
    bot._setup_encryption.assert_not_awaited()


# ── offline queue: failed send → pending → flush ────────────────────


async def test_matrix_failed_send_queues_reply_and_flush_sends_it(bot):
    reply = "Your bread needs about 40 minutes at 220C."
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock(side_effect=RuntimeError("homeserver unreachable"))

    with patch("windyfly.agent.executor.run_turn", new=AsyncMock(return_value=reply)):
        await bot._on_message(_room(), _event())

    assert bot._pending_responses == [(ROOM, reply)]

    # Homeserver back: the flush delivers exactly that reply and empties the queue.
    bot.client.room_send = AsyncMock()
    await bot._flush_pending()

    bot.client.room_send.assert_awaited_once()
    room_id, msgtype, content = bot.client.room_send.await_args.args
    assert room_id == ROOM
    assert msgtype == "m.room.message"
    assert content["body"] == reply
    assert content["windy_original"] is True
    assert bot._pending_responses == []


async def test_matrix_flush_that_fails_again_keeps_reply_queued(bot):
    bot._pending_responses.append((ROOM, "still waiting"))
    bot.client.room_send = AsyncMock(side_effect=RuntimeError("still down"))
    await bot._flush_pending()
    assert bot._pending_responses == [(ROOM, "still waiting")]
