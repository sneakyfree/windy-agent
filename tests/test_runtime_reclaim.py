"""The runtime claim after a sleep (Boss + Hub 11-03, found by the #696 lab): a sleep longer than Mind's 90 s TTL let
the claim lapse, the heartbeat thread exited on the 404 and never claimed again, so a second copy of the agent could
claim it and both would answer. Now the claim is taken again (same runtime_id), BEFORE a wake's messages are answered;
another copy holding it = act on nothing and keep trying; Mind silent 10 min after a wake = pause, then catch up."""

from __future__ import annotations

import json
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from windyfly import runtime_claim as rc


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path))
    monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-AAAA")
    monkeypatch.setenv("WINDY_JWT", "fake.jwt.value")
    monkeypatch.delenv("MIND_API_URL", raising=False)
    monkeypatch.setenv("MIND_BASE_URL", "https://api.windymind.test")
    rc._reset_state_for_tests()
    yield
    rc._reset_state_for_tests()


class _Mind:
    """A fake Mind: answers per path from a script; records every call (path, body)."""

    def __init__(self, monkeypatch, heartbeat=200, claim=200):
        self.heartbeat, self.claim = heartbeat, claim
        self.calls: list[tuple[str, dict]] = []
        real = httpx.Client

        def factory(*a, **kw):
            kw["transport"] = httpx.MockTransport(self._handle)
            return real(*a, **kw)

        monkeypatch.setattr(httpx, "Client", factory)

    def _handle(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path.rsplit("/", 1)[-1]
        self.calls.append((path, json.loads(req.content or b"{}")))
        answer = self.heartbeat if path == "heartbeat" else self.claim if path == "claim" else 200
        if isinstance(answer, Exception):
            raise answer
        body = {"detail": {"claimed": False, "holder": {"source": "matrix", "host": "Windy Fly home"}}} \
            if answer in (202, 409) else {"claimed": True, "ttl_seconds": 90}
        return httpx.Response(answer, json=body)

    def count(self, path: str) -> int:
        return sum(1 for p, _ in self.calls if p == path)


def _granted(mind) -> None:
    assert rc.acquire_runtime_slot(source="matrix") == rc.ClaimOutcome.GRANTED


def _wait(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


# ── runtime_claim ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_lapsed_claim_is_claimed_again_with_the_same_runtime_id_and_heartbeats_go_on(monkeypatch):
    monkeypatch.setattr(rc, "_HEARTBEAT_INTERVAL_S", 0.02)
    mind = _Mind(monkeypatch, heartbeat=404)
    _granted(mind)
    first_id = mind.calls[0][1]["runtime_id"]
    rc.start_heartbeat()
    assert _wait(lambda: mind.count("claim") >= 2)
    mind.heartbeat = 200
    assert _wait(lambda: mind.count("heartbeat") >= 3)
    assert {b["runtime_id"] for p, b in mind.calls if p == "claim"} == {first_id}  # Mind 11-03: the SAME id
    assert rc._state.heartbeat_thread.is_alive()
    assert rc.standing() is rc.Standing.HELD and rc.gate() == "act"


def test_held_elsewhere_acts_on_nothing_keeps_trying_and_takes_only_newer_messages_once_it_is_free(monkeypatch):
    monkeypatch.setattr(rc, "_HEARTBEAT_INTERVAL_S", 0.02)
    mind = _Mind(monkeypatch, heartbeat=404, claim=200)
    _granted(mind)
    mind.claim = 409                                  # another copy claimed the slot while this one slept
    rc.start_heartbeat()
    assert _wait(lambda: rc.standing() is rc.Standing.ELSEWHERE)
    assert rc.gate() == "elsewhere"
    tries = mind.count("claim")
    assert _wait(lambda: mind.count("claim") >= tries + 2)  # keeps trying at the heartbeat interval
    before = time.time() - 1
    mind.claim, mind.heartbeat = 200, 200               # the other copy stopped: its claim expired
    assert _wait(lambda: rc.standing() is rc.Standing.HELD)
    assert rc.gate(before) == "old"                     # arrived while the other copy had it: it answered
    assert rc.gate(time.time() + 1) == "act"
    assert rc._state.heartbeat_thread.is_alive()


def test_mind_unreachable_never_stops_the_heartbeat_and_it_backs_off(monkeypatch):
    monkeypatch.setattr(rc, "_HEARTBEAT_INTERVAL_S", 0.01)
    monkeypatch.setattr(rc, "_MAX_BACKOFF_S", 0.08)
    mind = _Mind(monkeypatch)
    _granted(mind)
    waits: list[float] = []
    real_wait = threading.Event.wait

    def spy(self, timeout=None):
        if threading.current_thread() is rc._state.heartbeat_thread:
            waits.append(timeout)
        return real_wait(self, timeout)

    monkeypatch.setattr(threading.Event, "wait", spy)
    mind.heartbeat = mind.claim = httpx.ConnectError("down")
    rc.start_heartbeat()
    assert _wait(lambda: len(waits) >= 6)
    assert rc.standing() is rc.Standing.UNREACHABLE and rc._state.heartbeat_thread.is_alive()
    assert max(waits) == pytest.approx(0.08) and waits[1] > waits[0]
    mind.heartbeat = mind.claim = 200
    assert _wait(lambda: rc.standing() is rc.Standing.HELD)


def test_a_wake_with_mind_silent_pauses_only_after_10_minutes_and_resumes_when_it_answers(monkeypatch):
    mind = _Mind(monkeypatch)
    _granted(mind)
    mind.claim = 503
    assert rc.reclaim(wake=True) == rc.ClaimOutcome.DEGRADED
    assert rc.gate() == "act"                           # the first 10 minutes: as before (fail-open)
    rc._state.pause_clock -= rc.PAUSE_AFTER_S + 1
    assert rc.gate() == "paused"
    mind.claim = 200
    assert rc.reclaim() == rc.ClaimOutcome.GRANTED
    assert rc.gate() == "act" and rc._state.pause_clock is None


def test_mind_silent_without_a_wake_never_pauses():
    rc._state = rc._ClaimState(passport="p", runtime_id="r", jwt="j", base_url="u", granted=True,
                               standing=rc.Standing.UNREACHABLE)
    assert rc.gate() == "act"


def test_a_heartbeat_re_claim_that_mind_misses_never_starts_the_pause_clock(monkeypatch):
    """Only a WAKE starts the 10-minute pause (Boss 11-03): a Mind outage while awake keeps answering as before."""
    mind = _Mind(monkeypatch)
    _granted(mind)
    mind.claim = httpx.ConnectError("down")
    assert rc.reclaim() == rc.ClaimOutcome.DEGRADED
    assert rc._state.pause_clock is None and rc.gate() == "act"


def test_no_claim_ever_held_means_nothing_to_guard(monkeypatch):
    assert rc.reclaim(wake=True) == rc.ClaimOutcome.SKIPPED
    assert rc.gate() == "act" and rc.standing() is rc.Standing.HELD


# ── the Matrix channel ───────────────────────────────────────────────────────────────────────────────────────────────

ME, DEV, ROOM = "@agent_et26-test-aaaa:chat.windychat.ai", "DEVICE1", "!dm:chat.windychat.ai"


def _bot(asleep_s: float = 0.0):
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
    bot._hatch_dm_room_id = ROOM
    bot._gap_checked = True
    bot._last_sync_success = time.time() - asleep_s
    return bot


def _msg(age_s: float, event_id: str):
    room = MagicMock()
    room.room_id = ROOM
    room.user_name.return_value = "Owner"
    ev = MagicMock()
    ev.sender = "@owner:chat.windychat.ai"
    ev.body = "what was the code word?"
    ev.event_id = event_id
    ev.server_timestamp = (time.time() - age_s) * 1000
    ev.source = {}
    return room, ev


def _sync():
    resp = MagicMock()
    resp.rooms.join = {}
    return resp


def _bodies(bot):
    return [c.args[2]["body"] for c in bot.client.room_send.await_args_list]


def _claim(standing=rc.Standing.HELD, **kw):
    rc._state = rc._ClaimState(passport="p", runtime_id="r", jwt="j", base_url="u", granted=True, standing=standing,
                               **kw)


def _fake_reclaim(order: list[str], then: rc.Standing, pause_clock: float | None = None):
    def fake(wake: bool = False):
        order.append(f"reclaim wake={wake}")
        rc._state.standing = then
        if pause_clock is not None:
            rc._state.pause_clock = pause_clock
        return rc.ClaimOutcome.GRANTED
    return fake


@pytest.mark.asyncio
async def test_after_a_sleep_the_claim_is_taken_again_BEFORE_the_message_is_answered(monkeypatch):
    order: list[str] = []
    _claim(rc.Standing.HELD)
    monkeypatch.setattr(rc, "reclaim", _fake_reclaim(order, rc.Standing.HELD))

    def respond(*a, **kw):
        order.append("answer")
        return "the word was 'kite'"

    with patch("windyfly.channels.matrix_bot.agent_respond", side_effect=respond):
        bot = _bot(asleep_s=5 * 60)
        await bot._on_message(*_msg(4 * 60, "$asked-while-asleep"))
        await bot._on_message(*_msg(3 * 60, "$second-in-the-same-sync"))
        await bot._on_sync_response(_sync())
    assert order == ["reclaim wake=True", "answer", "answer"]  # ONE re-claim per wake, first


@pytest.mark.asyncio
async def test_held_elsewhere_answers_nothing_says_the_line_once_and_keeps_saving_the_position(monkeypatch):
    from windyfly.channels import matrix_bot as mb
    from windyfly.channels import sync_position as sp

    _claim(rc.Standing.HELD)
    monkeypatch.setattr(rc, "reclaim", _fake_reclaim([], rc.Standing.ELSEWHERE))
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot(asleep_s=5 * 60)
        for i in range(3):
            await bot._on_message(*_msg(60 - i, f"$m{i}"))
        bot.client.next_batch = "s-elsewhere"
        await bot._on_sync_response(_sync())
        await bot._on_sync_response(_sync())
        assert respond.call_count == 0
    assert _bodies(bot).count(mb._ELSEWHERE_SAY) == 1
    assert sp.load(ME, DEV) == "s-elsewhere"            # the other copy answered those: never again from here


@pytest.mark.asyncio
async def test_retaken_from_another_copy_answers_only_messages_newer_than_the_retake(monkeypatch):
    _claim(rc.Standing.ELSEWHERE)
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot()
        await bot._on_message(*_msg(20, "$while-elsewhere"))
        _claim(rc.Standing.HELD, retaken_at=time.time() - 5)   # the heartbeat re-took it 5 s ago
        await bot._on_sync_response(_sync())
        await bot._on_message(*_msg(10, "$before-the-retake-not-seen-yet"))
        await bot._on_message(*_msg(1, "$after-the-retake"))
        assert respond.call_count == 1
        assert "what was the code word?" in repr(respond.call_args)


@pytest.mark.asyncio
async def test_paused_holds_the_messages_and_the_position_then_answers_them_once_when_mind_is_back(monkeypatch):
    from windyfly.channels import matrix_bot as mb
    from windyfly.channels import sync_position as sp

    sp.save(ME, DEV, "s-before-the-sleep")
    _claim(rc.Standing.HELD)
    monkeypatch.setattr(rc, "reclaim",
                        _fake_reclaim([], rc.Standing.UNREACHABLE, pause_clock=time.time() - rc.PAUSE_AFTER_S - 1))
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot(asleep_s=20 * 60)
        await bot._on_message(*_msg(15 * 60, "$a"))
        await bot._on_message(*_msg(14 * 60, "$b"))
        bot.client.next_batch = "s-paused"
        await bot._on_sync_response(_sync())
        assert respond.call_count == 0
        assert sp.load(ME, DEV) == "s-before-the-sleep"   # a restart fetches them again
        assert _bodies(bot).count(mb._PAUSE_SAY) == 1
        _claim(rc.Standing.HELD)                           # the heartbeat thread got through: Mind is back
        await bot._on_sync_response(_sync())
        await bot._on_sync_response(_sync())
        assert respond.call_count == 2                     # each held message once
    assert sp.load(ME, DEV) == "s-paused"


@pytest.mark.asyncio
async def test_a_shutdown_while_paused_keeps_the_position_so_the_next_start_takes_the_held_messages(monkeypatch):
    from windyfly.channels import sync_position as sp

    sp.save(ME, DEV, "s-before")
    _claim(rc.Standing.UNREACHABLE, pause_clock=time.time() - rc.PAUSE_AFTER_S - 1)
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x"):
        bot = _bot()
        bot.client.set_presence = AsyncMock()
        bot.client.close = AsyncMock()
        await bot._on_message(*_msg(30, "$held-at-shutdown"))
        bot.client.next_batch = "s-past-the-held-message"
        await bot._graceful_shutdown()
    assert sp.load(ME, DEV) == "s-before"


@pytest.mark.asyncio
async def test_paused_then_held_messages_older_than_the_1h_cap_get_the_one_reply(monkeypatch):
    from windyfly.channels import matrix_bot as mb

    _claim(rc.Standing.UNREACHABLE, pause_clock=time.time() - rc.PAUSE_AFTER_S - 1)
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot()
        await bot._on_message(*_msg(61 * 60, "$too-old"))
        await bot._on_message(*_msg(5 * 60, "$fine"))
        _claim(rc.Standing.HELD)
        await bot._on_sync_response(_sync())
        assert respond.call_count == 1
    assert any(b.startswith("I was offline and just saw") for b in _bodies(bot))
    assert _bodies(bot).count(mb._PAUSE_SAY) == 1


@pytest.mark.asyncio
async def test_paused_then_another_copy_took_over_drops_the_held_messages(monkeypatch):
    from windyfly.channels import sync_position as sp

    _claim(rc.Standing.UNREACHABLE, pause_clock=time.time() - rc.PAUSE_AFTER_S - 1)
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot()
        await bot._on_message(*_msg(30, "$held"))
        _claim(rc.Standing.ELSEWHERE)
        bot.client.next_batch = "s-after"
        await bot._on_sync_response(_sync())
        _claim(rc.Standing.HELD)
        await bot._on_sync_response(_sync())
        assert respond.call_count == 0
    assert bot._held_events == [] and sp.load(ME, DEV) == "s-after"


@pytest.mark.asyncio
async def test_a_normal_sync_never_re_claims(monkeypatch):
    order: list[str] = []
    _claim(rc.Standing.HELD)
    monkeypatch.setattr(rc, "reclaim", _fake_reclaim(order, rc.Standing.HELD))
    with patch("windyfly.channels.matrix_bot.agent_respond", return_value="x") as respond:
        bot = _bot(asleep_s=5)
        await bot._on_message(*_msg(2, "$normal"))
        await bot._on_sync_response(_sync())
        assert respond.call_count == 1
    assert order == []


@pytest.mark.asyncio
async def test_a_wake_with_no_message_still_re_claims_at_once(monkeypatch):
    order: list[str] = []
    _claim(rc.Standing.HELD)
    monkeypatch.setattr(rc, "reclaim", _fake_reclaim(order, rc.Standing.HELD))
    bot = _bot(asleep_s=10 * 60)
    await bot._on_sync_response(_sync())
    await bot._on_sync_response(_sync())
    assert order == ["reclaim wake=True"]


@pytest.mark.asyncio
async def test_the_line_comes_back_only_for_a_new_period(monkeypatch):
    from windyfly.channels import matrix_bot as mb

    _claim(rc.Standing.ELSEWHERE)
    bot = _bot()
    await bot._on_sync_response(_sync())
    await bot._on_sync_response(_sync())
    _claim(rc.Standing.HELD)
    await bot._on_sync_response(_sync())
    _claim(rc.Standing.ELSEWHERE)
    await bot._on_sync_response(_sync())
    assert _bodies(bot).count(mb._ELSEWHERE_SAY) == 2   # once per held-elsewhere period, not per sync


def test_the_two_lines_are_boss_s_words():
    from windyfly.channels import matrix_bot as mb

    assert mb._ELSEWHERE_SAY == ("Another copy of your agent has started answering you, so I've stopped answering "
                                 "from here.")
    assert mb._PAUSE_SAY == "I can't reach Windy right now, so I'm pausing until I can."
