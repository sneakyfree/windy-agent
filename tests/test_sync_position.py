"""The laptop-home MUST-FIX (#687 F): an agent that sleeps or restarts resumes its Windy Chat sync where it left off,
so the owner's messages from meanwhile are answered once, never dropped by the 120 s catch-up window."""

from __future__ import annotations

import os
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from windyfly.channels import sync_position as sp

ME, DEV, ROOM = "@agent_et26-test-aaaa:chat.windychat.ai", "DEVICE1", "!dm:chat.windychat.ai"


def test_a_saved_position_comes_back_for_the_same_user_and_device_only():
    assert sp.load(ME, DEV) is None
    assert sp.save(ME, DEV, "s123_456")
    assert sp.load(ME, DEV) == "s123_456"
    assert sp.load(ME, "OTHER-DEVICE") is None and sp.load("@someone:else", DEV) is None
    assert (os.stat(sp._path()).st_mode & 0o777) == 0o600


def test_an_unreadable_position_is_a_fresh_start():
    sp._path().write_text("{not json")
    assert sp.load(ME, DEV) is None


def test_nothing_to_save_is_not_saved():
    assert not sp.save(ME, DEV, "") and not sp.save("", DEV, "s1")


def _bot():
    from windyfly.channels.matrix_bot import WindyFlyMatrixBot
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue

    cfg = {"matrix": {"homeserver": "https://chat.windychat.ai", "bot_user": ME},
           "agent": {"default_model": "m"}, "memory": {"db_path": ":memory:"}, "personality": {}}
    bot = WindyFlyMatrixBot(cfg, Database(":memory:"), WriteQueue())
    bot.bot_user_id = ME
    bot.client.device_id = DEV
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock()
    return bot


def _owner_message(age_s: float, event_id: str):
    room = MagicMock()
    room.room_id = ROOM
    room.user_name.return_value = "Owner"
    ev = MagicMock()
    ev.sender = "@owner:chat.windychat.ai"
    ev.body = "are you there?"
    ev.event_id = event_id
    ev.server_timestamp = (time.time() - age_s) * 1000
    ev.source = {}
    return room, ev


@pytest.mark.asyncio
async def test_a_message_from_while_the_laptop_slept_is_answered_after_a_resume():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="here!") as respond:
        bot = _bot()
        bot._resumed_sync = True  # start() found a saved position
        bot._hatch_dm_room_id = ROOM
        await bot._on_message(*_owner_message(30 * 60, "$while-asleep"))  # inside the 1 h cap (Boss 11:08Z)
        assert respond.call_count == 1


@pytest.mark.asyncio
async def test_after_a_resume_other_rooms_keep_the_120_s_window():
    """Only the owner's DM is caught up; old messages in any other room are never answered late."""
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot()
        bot._resumed_sync = True
        bot._hatch_dm_room_id = "!another-room:chat.windychat.ai"
        await bot._on_message(*_owner_message(3 * 3600, "$group-chatter"))
        assert respond.call_count == 0


@pytest.mark.asyncio
async def test_a_fresh_start_still_skips_an_old_backlog():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        await _bot()._on_message(*_owner_message(3 * 3600, "$ancient"))
        assert respond.call_count == 0


@pytest.mark.asyncio
async def test_a_resume_never_answers_a_message_twice():
    from windyfly.channels import handled_events

    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        msg = _owner_message(30, "$answered-before-the-sleep")
        await _bot()._on_message(*msg)
        handled_events.reset_cache()  # a new process
        bot = _bot()
        bot._resumed_sync = True
        bot._hatch_dm_room_id = ROOM
        await bot._on_message(*msg)
        assert respond.call_count == 1


def test_the_position_is_saved_after_a_sync_throttled_and_for_sure_on_shutdown():
    bot = _bot()
    bot.client.next_batch = "s1"
    bot._save_sync_position()
    assert sp.load(ME, DEV) == "s1"
    bot.client.next_batch = "s2"
    bot._save_sync_position()            # within SAVE_EVERY_S: not yet
    assert sp.load(ME, DEV) == "s1"
    bot._save_sync_position(force=True)  # graceful shutdown
    assert sp.load(ME, DEV) == "s2"


@pytest.mark.asyncio
async def test_a_successful_sync_saves_the_position():
    bot = _bot()
    bot.client.next_batch = "s9"
    await bot._on_sync_response(MagicMock())
    assert sp.load(ME, DEV) == "s9"


def test_start_resumes_from_the_saved_position():
    sp.save(ME, DEV, "s777")
    bot = _bot()
    bot._resume_saved_position()
    assert bot.client.next_batch == "s777" and bot._resumed_sync is True


def test_no_saved_position_is_a_fresh_start():
    bot = _bot()
    bot._resume_saved_position()
    assert bot._resumed_sync is False


# ── bounds on the catch-up (Boss 11-03 11:08Z / 11:09Z, Hub #9) ──────────────────────────────────────────────────────

def _resumed_bot():
    bot = _bot()
    bot._resumed_sync, bot._hatch_dm_room_id = True, ROOM
    return bot


def _sync_response(limited: bool, room: str = ROOM):
    timeline = MagicMock()
    timeline.limited = limited
    info = MagicMock()
    info.timeline = timeline
    resp = MagicMock()
    resp.rooms.join = {room: info}
    return resp


def _bodies(bot):
    return [c.args[2]["body"] for c in bot.client.room_send.await_args_list]


@pytest.mark.asyncio
async def test_61_minutes_old_is_never_acted_on_and_gets_one_reply():
    from windyfly.channels import matrix_bot as mb

    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="sent!") as respond:
        bot = _resumed_bot()
        msg = _owner_message(61 * 60, "$yes-send-it")
        await bot._on_message(*msg)
        await bot._on_sync_response(_sync_response(limited=False))
        assert respond.call_count == 0  # a stale "yes, send it" is never spent
        assert _bodies(bot) == [mb._STALE_SAY.format(n="1 message", age="61 minutes")]
        await bot._on_message(*msg)       # the same message again: never acted on, never counted again
        await bot._on_sync_response(_sync_response(limited=False))
        assert respond.call_count == 0 and len(_bodies(bot)) == 1


@pytest.mark.asyncio
async def test_59_minutes_old_is_acted_on():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="ok") as respond:
        bot = _resumed_bot()
        await bot._on_message(*_owner_message(59 * 60, "$fresh-enough"))
        assert respond.call_count == 1


@pytest.mark.asyncio
async def test_three_old_messages_get_one_reply_with_the_count_and_the_oldest_age():
    from windyfly.channels import matrix_bot as mb

    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _resumed_bot()
        for i, age in enumerate((3 * 86400, 5 * 3600, 2 * 3600)):
            await bot._on_message(*_owner_message(age, f"$old{i}"))
        await bot._on_sync_response(_sync_response(limited=False))
        assert respond.call_count == 0
        assert _bodies(bot) == [mb._STALE_SAY.format(n="3 messages", age="3 days")]


@pytest.mark.asyncio
async def test_a_limited_dm_and_old_messages_say_the_gap_line_first_then_the_count():
    from windyfly.channels import matrix_bot as mb

    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x"):
        bot = _resumed_bot()
        await bot._on_message(*_owner_message(2 * 3600, "$old"))
        await bot._on_sync_response(_sync_response(limited=True))
        assert _bodies(bot) == [mb._GAP_SAY, mb._STALE_SAY.format(n="1 message", age="2 hours")]
        await bot._on_sync_response(_sync_response(limited=True))  # a second sync: neither again
        assert len(_bodies(bot)) == 2


@pytest.mark.asyncio
async def test_a_limited_group_room_says_nothing():
    bot = _resumed_bot()
    await bot._on_sync_response(_sync_response(limited=True, room="!group:chat.windychat.ai"))
    assert _bodies(bot) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("resumed,limited", [(False, True), (True, False)])
async def test_no_gap_line_without_a_resume_or_a_limited_dm(resumed, limited):
    bot = _bot()
    bot._resumed_sync, bot._hatch_dm_room_id = resumed, ROOM
    await bot._on_sync_response(_sync_response(limited=limited))
    assert _bodies(bot) == []


def _file_event(age_s: float = 5, event_id: str = "$file", room_id: str = ROOM, sender: str = "@owner:chat.windychat.ai"):
    room = MagicMock()
    room.room_id = room_id
    ev = MagicMock()
    ev.sender, ev.event_id, ev.body, ev.url = sender, event_id, "photo.jpg", "mxc://x/y"
    ev.server_timestamp = (time.time() - age_s) * 1000
    ev.source = {}
    return room, ev


def _owner_band(sender, **_):
    from windyfly.agent.capabilities.descriptor import Band

    return Band.OWNER if sender == "@owner:chat.windychat.ai" else Band.USER


async def _send_file(bot, *ev, awaited=False):
    with patch("windyfly.soul_import.flow.awaiting_file", return_value=awaited), \
            patch("windyfly.channels.identity.resolve_band", side_effect=lambda ch, s, **k: _owner_band(s)), \
            patch("windyfly.soul_import.flow.too_big", return_value="taken by the import"):
        await bot._on_file(*ev)


@pytest.mark.asyncio
async def test_an_unrequested_owner_file_in_the_dm_gets_one_plain_line():
    from windyfly.channels import matrix_bot as mb

    bot = _bot()
    bot._hatch_dm_room_id = ROOM
    await _send_file(bot, *_file_event())
    await _send_file(bot, *_file_event())  # the same event again (a replay): nothing
    assert _bodies(bot) == [mb._FILE_SAY]


@pytest.mark.asyncio
async def test_after_a_resume_an_old_unrequested_file_gets_the_line_once():
    from windyfly.channels import matrix_bot as mb

    bot = _resumed_bot()
    await _send_file(bot, *_file_event(age_s=3 * 3600, event_id="$while-asleep"))
    assert _bodies(bot) == [mb._FILE_SAY]


@pytest.mark.asyncio
@pytest.mark.parametrize("room_id,sender", [("!group:chat.windychat.ai", "@owner:chat.windychat.ai"),
                                            (ROOM, "@stranger:chat.windychat.ai")])
async def test_a_group_room_or_a_non_owner_file_stays_silent(room_id, sender):
    bot = _bot()
    bot._hatch_dm_room_id = ROOM
    await _send_file(bot, *_file_event(room_id=room_id, sender=sender))
    assert _bodies(bot) == []


@pytest.mark.asyncio
async def test_a_file_a_soul_import_asked_for_is_taken_as_before_not_the_line():
    from windyfly.channels import matrix_bot as mb

    bot = _bot()
    bot._hatch_dm_room_id = ROOM
    await _send_file(bot, *_file_event(), awaited=True)
    assert mb._FILE_SAY not in _bodies(bot) and _bodies(bot) == ["taken by the import"]


@pytest.mark.asyncio
async def test_a_fresh_start_skips_an_old_file_silently():
    bot = _bot()
    bot._hatch_dm_room_id = ROOM
    await _send_file(bot, *_file_event(age_s=3 * 3600))
    assert _bodies(bot) == []


@pytest.mark.parametrize("seconds,said", [(61 * 60, "61 minutes"), (60, "1 minute"), (5 * 3600, "5 hours"),
                                          (3 * 86400, "3 days")])
def test_ages_are_plain_words(seconds, said):
    from windyfly.channels import matrix_bot as mb

    assert mb._plain_age(seconds) == said


# ── an in-process WAKE: a suspended laptop carries on from its in-memory position (Hub on #696) ─────────────────────

def _woken_bot(asleep_s: float):
    bot = _bot()
    bot._hatch_dm_room_id = ROOM
    bot._last_sync_success = time.time() - asleep_s  # the last good sync was before the lid closed
    bot._gap_checked = True                          # this process has synced before
    return bot


@pytest.mark.asyncio
async def test_after_an_in_process_wake_of_30_min_the_dm_message_is_acted_on():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="ok") as respond:
        bot = _woken_bot(30 * 60)
        await bot._on_message(*_owner_message(25 * 60, "$while-the-lid-was-closed"))
        assert respond.call_count == 1  # no longer cut by the 120 s window


@pytest.mark.asyncio
async def test_after_an_in_process_wake_of_2_hours_an_old_message_gets_one_reply():
    from windyfly.channels import matrix_bot as mb

    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _woken_bot(2 * 3600)
        await bot._on_message(*_owner_message(100 * 60, "$old-after-a-wake"))
        await bot._on_sync_response(_sync_response(limited=False))
        assert respond.call_count == 0
        assert _bodies(bot) == [mb._STALE_SAY.format(n="1 message", age="100 minutes")]


@pytest.mark.asyncio
async def test_a_limited_dm_after_an_in_process_wake_gets_the_gap_line():
    from windyfly.channels import matrix_bot as mb

    bot = _woken_bot(3 * 3600)
    await bot._on_sync_response(_sync_response(limited=True))
    assert _bodies(bot) == [mb._GAP_SAY]


@pytest.mark.asyncio
async def test_a_limited_dm_on_a_normal_sync_says_nothing():
    bot = _woken_bot(20)  # the last sync was 20 s ago: no wake
    await bot._on_sync_response(_sync_response(limited=True))
    assert _bodies(bot) == []


@pytest.mark.asyncio
async def test_a_group_room_keeps_the_120_s_window_after_a_wake():
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _woken_bot(3600)
        bot._hatch_dm_room_id = "!another-room:chat.windychat.ai"
        await bot._on_message(*_owner_message(10 * 60, "$group-chatter"))
        assert respond.call_count == 0


@pytest.mark.asyncio
async def test_the_wake_gap_is_wall_time_so_a_system_sleep_counts(monkeypatch):
    """Boss 11:23Z: on macOS time.monotonic() does not advance during system sleep, so the gap must be wall time. A
    jump of time.time() across a 3-hour sleep arms the catch-up even if a monotonic clock barely moved."""
    from windyfly.channels import matrix_bot as mb

    bot = _bot()
    bot._hatch_dm_room_id, bot._gap_checked = ROOM, True
    real = time.time()
    bot._last_sync_success = real
    monkeypatch.setattr(mb.time, "monotonic", lambda: 1.0)        # a sleeping Mac's monotonic clock: unchanged
    monkeypatch.setattr(mb.time, "time", lambda: real + 3 * 3600)  # the wall clock after a 3-hour sleep
    await bot._on_sync_response(_sync_response(limited=True))
    assert _bodies(bot) == [mb._GAP_SAY]


@pytest.mark.asyncio
async def test_a_clock_that_jumped_backwards_counts_as_a_wake(monkeypatch):
    from windyfly.channels import matrix_bot as mb

    bot = _bot()
    bot._hatch_dm_room_id, bot._gap_checked = ROOM, True
    real = time.time()
    bot._last_sync_success = real
    monkeypatch.setattr(mb.time, "time", lambda: real - 600)       # NTP stepped the clock back 10 min
    await bot._on_sync_response(_sync_response(limited=True))
    assert _bodies(bot) == [mb._GAP_SAY]
