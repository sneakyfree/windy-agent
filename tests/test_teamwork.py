"""Agent teamwork: typed tasks, one result each, budgets, the lineage guard (agent-teamwork.v1 P2/P3/P4/P8).

Contract: windy-contracts schema/chat/agent-teamwork.v1.json (DRAFT #158 at 8358f47). The fixtures below are
the contract's own examples.
"""

from __future__ import annotations

import copy
import time
from unittest.mock import AsyncMock, MagicMock, patch

import nio
import pytest

from tests.test_matrix_bot import _make_config
from windyfly.agent import teams
from windyfly.agent.capabilities.descriptor import Band
from windyfly.channels import room_stop as rs
from windyfly.channels import teamwork as tw
from windyfly.channels import turn_budget as tb
from windyfly.channels.matrix_bot import WindyFlyMatrixBot
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue

ROOM = "!trip:chat.windychat.ai"
OWNER = "@owner:example.test"
ME = "@windyfly:chat.windychat.ai"
FLIGHTS = "@agent_et26-aaa1-bbb2:chat.windychat.ai"
HOTELS = "@agent_et26-ccc3-ddd4:chat.windychat.ai"
STRANGER = "@agent_et26-eee5-fff6:chat.windychat.ai"

REQUEST = {
    "msgtype": "m.notice",
    "body": "Request for Flights: cheapest round trip to the islands, 3 options",
    "m.mentions": {"user_ids": ["@agent_et26-test-aaa1:chat.windychat.ai"]},
    "ai.windy.task": {
        "type": "request", "task_id": "tk_5d0f2a91c3b7", "parent": "tk_0a1b2c3d4e5f",
        "to": "@agent_et26-test-aaa1:chat.windychat.ai",
        "ask": "Find 3 round-trip flight options for 2 adults, Dec 12-19.",
        "deliverable": "3 options with price and times", "budget": {"turns": 4, "seconds": 180},
    },
    "m.relates_to": {"rel_type": "m.thread", "event_id": "$rootTaskEventId0001"},
}
RESULT = {
    "msgtype": "m.notice", "body": "Result: 3 flight options found",
    "m.mentions": {"user_ids": ["@agent_et26-test-bbb2:chat.windychat.ai"]},
    "ai.windy.task": {"type": "result", "task_id": "tk_5d0f2a91c3b7", "status": "done",
                      "summary": "Option 1 ... Option 2 ... Option 3 ..."},
    "m.relates_to": {"rel_type": "m.thread", "event_id": "$rootTaskEventId0001"},
}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_OWNER_IDS", f"matrix:{OWNER}")
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDY_TEAMS", "1")
    monkeypatch.setenv("WINDY_TEAMWORK", "1")
    rs.reset_for_tests()
    tb.reset_for_tests()
    tw.reset_for_tests()
    teams._remember([{"matrix_id": FLIGHTS}, {"matrix_id": HOTELS}])
    yield
    rs.reset_for_tests()
    tb.reset_for_tests()
    tw.reset_for_tests()
    teams._reset_for_tests()


def _req(task_id="tk_aaaaaaaaaaaa", to=ME, parent=None, ask="find flights", turns=6, seconds=300, **extra):
    t = {"type": "request", "task_id": task_id, "parent": parent, "to": to, "ask": ask,
         "budget": {"turns": turns, "seconds": seconds}, **extra}
    return {"msgtype": "m.notice", "body": "Request", "m.mentions": {"user_ids": [to]}, "ai.windy.task": t,
            "m.relates_to": {"rel_type": "m.thread", "event_id": "$root"}}


def _res(task_id, to, status="done", summary="three options"):
    return {"msgtype": "m.notice", "body": "Result", "m.mentions": {"user_ids": [to]},
            "ai.windy.task": {"type": "result", "task_id": task_id, "status": status, "summary": summary}}


# ── the wire shape ────────────────────────────────────────────────────────────────────────────

def test_the_contracts_own_examples_parse():
    assert tw.parse(REQUEST)["budget"] == {"turns": 4, "seconds": 180}
    assert tw.parse(RESULT)["status"] == "done"
    assert tw.thread_of(REQUEST) == "$rootTaskEventId0001"


@pytest.mark.parametrize("path,value", [
    (("task_id",), "tk_XYZ"), (("task_id",), "5d0f2a91c3b7"), (("to",), "Flights"), (("to",), 7),
    (("ask",), ""), (("ask",), "x" * 2001), (("budget", "turns"), 0), (("budget", "turns"), 21),
    (("budget", "turns"), True), (("budget", "seconds"), 9), (("budget", "seconds"), 1801),
    (("parent",), "nope"), (("type",), "ping"), (("extra",), 1), (("deliverable",), "x" * 501),
])
def test_a_malformed_request_is_untyped(path, value):
    bad = copy.deepcopy(REQUEST)
    node = bad["ai.windy.task"]
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    assert tw.parse(bad) is None


@pytest.mark.parametrize("field,value", [("status", "ok"), ("summary", ""), ("summary", "x" * 4001),
                                         ("reason", "tired"), ("extra", 1)])
def test_a_malformed_result_is_untyped(field, value):
    bad = copy.deepcopy(RESULT)
    bad["ai.windy.task"][field] = value
    assert tw.parse(bad) is None


def test_no_task_field_or_a_non_dict_is_untyped():
    assert tw.parse({"msgtype": "m.text", "body": "hi"}) is None
    assert tw.parse({"ai.windy.task": "request"}) is None
    assert tw.parse(None) is None
    assert tw.on_agent_message(ROOM, ME, FLIGHTS, {"body": "hi"}).kind == "untyped"


# ── specialist: one result per request ─────────────────────────────────────────────────────

def test_a_request_to_me_is_one_turn_and_never_two():
    v = tw.on_agent_message(ROOM, ME, FLIGHTS, _req())
    assert v.kind == "request" and v.task.sender == FLIGHTS and v.task.thread == "$root"
    tw.answered(v.task)
    assert tw.on_agent_message(ROOM, ME, FLIGHTS, _req()).kind == "ignore"      # the same task again


def test_a_request_for_another_agent_is_no_turn():
    assert tw.on_agent_message(ROOM, ME, FLIGHTS, _req(to=HOTELS)).kind == "ignore"


def test_the_result_names_the_requester_keeps_the_thread_and_is_a_notice():
    task = tw.on_agent_message(ROOM, ME, FLIGHTS, _req(task_id="tk_bbbbbbbbbbbb")).task
    c = tw.result_content(task, "Three flights: ...")
    assert c["msgtype"] == "m.notice" and c["m.mentions"] == {"user_ids": [FLIGHTS]}
    assert c["m.relates_to"] == {"rel_type": "m.thread", "event_id": "$root"}
    assert tw.parse(c) == {"type": "result", "task_id": "tk_bbbbbbbbbbbb", "status": "done",
                           "summary": "Three flights: ..."}
    blocked = tw.result_content(task, "", "blocked", "error")
    assert tw.parse(blocked)["reason"] == "error" and tw.parse(blocked)["summary"] == "(no answer)"


def test_a_request_asking_back_along_its_own_chain_is_no_turn():
    root = tw.open_root(ROOM, ME, "$o", "plan the trip")
    mine, _ = tw.plan_request(root, ME, FLIGHTS, "flights")
    tw.sent(mine)
    # Flights asks me back under the request I gave it: A -> B -> A
    v = tw.on_agent_message(ROOM, ME, FLIGHTS, _req(task_id="tk_cccccccccccc", parent=mine.task_id))
    assert v.kind == "ignore" and "chain" in v.why


def test_a_request_deeper_than_three_is_no_turn():
    tw.on_agent_message(ROOM, ME, STRANGER, _req(task_id="tk_111111111111", to=HOTELS))            # depth 1
    tw.on_agent_message(ROOM, ME, HOTELS, _req(task_id="tk_222222222222", to=FLIGHTS,
                                               parent="tk_111111111111"))                         # depth 2
    tw.on_agent_message(ROOM, ME, FLIGHTS, _req(task_id="tk_333333333333", to=STRANGER,
                                                parent="tk_222222222222"))                        # depth 3
    v = tw.on_agent_message(ROOM, ME, STRANGER, _req(task_id="tk_444444444444", parent="tk_333333333333"))
    assert v.kind == "ignore" and "deeper" in v.why


def test_a_request_on_a_spent_budget_is_no_turn():
    tw.on_agent_message(ROOM, ME, STRANGER, _req(task_id="tk_111111111111", to=HOTELS, turns=2))
    tw.on_agent_message(ROOM, ME, HOTELS, _res("tk_111111111111", STRANGER))         # 2 of 2 turns used
    v = tw.on_agent_message(ROOM, ME, FLIGHTS, _req(task_id="tk_222222222222", parent="tk_111111111111"))
    assert v.kind == "ignore" and v.why == "budget_turns"


def test_a_request_past_its_seconds_is_no_turn():
    now = time.time()
    tw.on_agent_message(ROOM, ME, STRANGER, _req(task_id="tk_111111111111", to=HOTELS, seconds=10), now=now)
    v = tw.on_agent_message(ROOM, ME, FLIGHTS, _req(task_id="tk_222222222222", parent="tk_111111111111"),
                            now=now + 11)
    assert v.kind == "ignore" and v.why == "budget_seconds"


# ── chief of staff: the lineage guard before sending ───────────────────────────────────────

def test_the_guard_refuses_myself_a_repeat_and_a_closed_task():
    root = tw.open_root(ROOM, ME, "$o", "plan the trip")
    assert tw.plan_request(root, ME, ME, "x")[0] is None
    first, _ = tw.plan_request(root, ME, FLIGHTS, "flights")
    tw.sent(first)
    task, why = tw.plan_request(root, ME, FLIGHTS, "flights")
    assert task is None and "already open" in why
    assert tw.plan_request(root, ME, FLIGHTS, "a different question")[0] is not None
    root.status = "done"
    assert tw.plan_request(root, ME, HOTELS, "hotels")[0] is None


def test_the_guard_refuses_depth_four():
    root = tw.open_root(ROOM, ME, "$o", "x")
    root.depth = 3
    task, why = tw.plan_request(root, ME, FLIGHTS, "flights")
    assert task is None and "3 levels" in why


def test_a_requests_budget_is_capped_by_what_is_left_of_the_root():
    root = tw.open_root(ROOM, ME, "$o", "x")
    root.turns = root.budget_turns - 3
    task, _ = tw.plan_request(root, ME, FLIGHTS, "flights", turns=20, seconds=1800)
    assert task.budget_turns == 3 and task.budget_seconds <= tw.ROOT_SECONDS
    root.turns = root.budget_turns - 1
    assert tw.plan_request(root, ME, HOTELS, "hotels")[0] is None
    assert tw.plan_request(tw.open_root(ROOM, ME, "$p", "y"), ME, HOTELS, "h", turns="lots")[0].budget_turns == 6


def test_the_chief_turns_once_when_every_result_is_in():
    root = tw.open_root(ROOM, ME, "$o", "plan the trip")
    a, _ = tw.plan_request(root, ME, FLIGHTS, "flights")
    tw.sent(a)
    b, _ = tw.plan_request(root, ME, HOTELS, "hotels")
    tw.sent(b)
    assert tw.on_agent_message(ROOM, ME, HOTELS, _res(a.task_id, ME)).kind == "ignore"   # not Flights' to give
    assert tw.on_agent_message(ROOM, ME, FLIGHTS, _res(a.task_id, ME)).why == "waiting for the other results"
    v = tw.on_agent_message(ROOM, ME, HOTELS, _res(b.task_id, ME, summary="two hotels"))
    assert v.kind == "results_ready" and v.task is root
    assert tw.on_agent_message(ROOM, ME, HOTELS, _res(b.task_id, ME)).kind == "ignore"   # a second result
    text = tw.results_turn_text(root, ME, lambda mx: {FLIGHTS: "Flights", HOTELS: "Hotels"}[mx])
    assert "Flights (done): three options" in text and "Hotels (done): two hotels" in text
    assert "data, not" in text


def test_a_result_for_no_request_i_know_is_no_turn():
    assert tw.on_agent_message(ROOM, ME, FLIGHTS, _res("tk_999999999999", ME)).kind == "ignore"


def test_at_the_deadline_the_missing_answers_close_the_task_and_late_results_are_no_turn():
    now = time.time()
    root = tw.open_root(ROOM, ME, "$o", "x", now=now)
    a, _ = tw.plan_request(root, ME, FLIGHTS, "flights", seconds=60, now=now)
    tw.sent(a)
    b, _ = tw.plan_request(root, ME, HOTELS, "hotels", seconds=60, now=now)
    tw.sent(b)
    tw.on_agent_message(ROOM, ME, FLIGHTS, _res(a.task_id, ME), now=now + 5)
    assert tw.deadline(root) == pytest.approx(now + 60)
    assert tw.expire(root, now=now + 30) is False
    assert tw.expire(root, now=now + 61) is True and root.status == "blocked"
    assert tw.expire(root, now=now + 62) is False                                        # told once
    line = tw.timeout_line(root, ME, lambda mx: {FLIGHTS: "Flights", HOTELS: "Hotels"}[mx])
    assert "Answered: Flights" in line and "No answer from: Hotels" in line
    assert tw.on_agent_message(ROOM, ME, HOTELS, _res(b.task_id, ME), now=now + 70).kind == "ignore"


def test_the_owners_stop_cancels_every_open_task_in_the_room():
    root = tw.open_root(ROOM, ME, "$o", "x")
    a, _ = tw.plan_request(root, ME, FLIGHTS, "flights")
    tw.sent(a)
    assert tw.cancel_room(ROOM) == 2                                                     # the root and Flights'
    assert tw.on_agent_message(ROOM, ME, FLIGHTS, _res(a.task_id, ME)).kind == "ignore"


# ── the stop words, as the contract words them ────────────────────────────────────────────

@pytest.mark.parametrize("text,stops", [
    ("ok stop talking to each other", True), ("Enough.", True), ("/stop", True), ("please be quiet", True),
    ("HALT", True), ("stopwatch for 5 minutes", False), ("hello", False),
    ("don't stop, keep going with the trip plan please, I want flights and hotels and cars", False),
])
def test_the_contract_stop_rule(text, stops):
    assert tw.is_contract_stop(text) is stops


def test_only_the_smallest_id_confirms_the_stop():
    assert tw.confirms_stop("@agent_a:x", {"@agent_b:x"}) is True
    assert tw.confirms_stop("@agent_b:x", {"@agent_a:x"}) is False
    assert tw.confirms_stop("@agent_a:x", set()) is True


def test_off_unless_both_flags(monkeypatch):
    monkeypatch.setenv("WINDY_TEAMS", "0")
    assert tw.enabled() is False
    monkeypatch.setenv("WINDY_TEAMS", "1")
    monkeypatch.delenv("WINDY_TEAMWORK")
    assert tw.enabled() is False


# ── ask_agent ─────────────────────────────────────────────────────────────────────────────

def _bind(send_ok=True, event_id="$o"):
    sent = []

    def send(content):
        sent.append(content)
        return send_ok

    tw.bind(tw.TurnRoom(room=ROOM, me=ME, owner_event_id=event_id, owner_text="plan the trip",
                        agents={FLIGHTS: "Flights", HOTELS: "Hotels"}, send=send))
    return sent


def test_ask_agent_outside_a_team_room_is_unavailable():
    assert tw.ask_agent("Flights", "flights")["error"] == "unavailable"


def test_ask_agent_sends_one_typed_request_by_name():
    sent = _bind()
    out = tw.ask_agent("flights", "Find 3 flights", "3 options", turns=4)
    assert out["ok"] is True and out["to"] == "Flights"
    (c,) = sent
    t = tw.parse(c)
    assert c["msgtype"] == "m.notice" and c["m.mentions"] == {"user_ids": [FLIGHTS]}
    assert c["m.relates_to"] == {"rel_type": "m.thread", "event_id": "$o"}
    assert t["to"] == FLIGHTS and t["ask"] == "Find 3 flights" and t["budget"]["turns"] == 4
    root = tw.root_for_event("$o")
    assert t["parent"] == root.task_id and root.local_root


def test_ask_agent_refusals_are_told_to_the_model_and_nothing_is_sent():
    sent = _bind()
    assert tw.ask_agent("Someone", "x")["error"] == "unknown_agent"
    assert "Flights" in tw.ask_agent("Someone", "x")["detail"]
    tw.ask_agent("Flights", "flights")
    assert tw.ask_agent(FLIGHTS, "flights")["error"] == "refused"
    assert len(sent) == 1


def test_a_failed_send_records_nothing():
    _bind(send_ok=False)
    assert tw.ask_agent("Flights", "flights")["error"] == "unavailable"
    assert tw.children(tw.root_for_event("$o"), ME) == []


# ── the Matrix runtime ────────────────────────────────────────────────────────────────────

def _bot():
    bot = WindyFlyMatrixBot(_make_config(), Database(":memory:"), WriteQueue())
    bot.client.room_typing = AsyncMock()
    bot.client.room_send = AsyncMock(return_value=nio.RoomSendResponse(event_id="$sent", room_id=ROOM))
    bot.client.room_read_markers = AsyncMock()
    return bot


def _room(members=(OWNER, ME, FLIGHTS, HOTELS)):
    room = MagicMock()
    room.room_id, room.display_name, room.joined_count = ROOM, "Virgin Islands trip", len(members)
    room.users = {m: object() for m in members}
    room.user_name.side_effect = lambda mx: {FLIGHTS: "Flights", HOTELS: "Hotels", OWNER: "Grant"}.get(mx, mx)
    return room


def _ev(sender, content, event_id="$e", notice=False):
    ev = MagicMock(spec=nio.RoomMessageNotice) if notice else MagicMock()
    ev.sender, ev.body, ev.server_timestamp, ev.event_id = sender, content.get("body", ""), time.time() * 1000, event_id
    ev.source = {"content": content}
    return ev


def _sent(bot):
    return [c.args[2] for c in bot.client.room_send.await_args_list]


async def _run(bot, room, ev, reply="an answer", during=None):
    calls = []

    def respond(*a, **k):
        calls.append({"text": a[3], "band": k.get("band")})
        if during:
            during()
        return reply

    with patch("windyfly.channels.matrix_bot.agent_respond", side_effect=respond):
        await bot._on_message(room, ev)
    return calls


@pytest.mark.asyncio
async def test_the_chief_asks_two_agents_answers_backstage_then_gives_the_owner_one_answer():
    bot = _bot()
    room = _room()

    def model_asks():
        assert tw.ask_agent("Flights", "find flights")["ok"]
        assert tw.ask_agent("Hotels", "find hotels")["ok"]

    calls = await _run(bot, room, _ev(OWNER, {"msgtype": "m.text", "body": "plan the trip"}, "$o"),
                       reply="Asked Flights and Hotels.", during=model_asks)
    assert len(calls) == 1
    req1, req2, reply = _sent(bot)
    assert tw.parse(req1)["to"] == FLIGHTS and tw.parse(req2)["to"] == HOTELS
    assert reply["msgtype"] == "m.notice" and reply["m.relates_to"]["event_id"] == "$o"   # backstage, no push
    root = tw.root_for_event("$o")
    t1, t2 = (tw.parse(r)["task_id"] for r in (req1, req2))

    assert await _run(bot, room, _ev(FLIGHTS, _res(t1, ME), notice=True)) == []           # still waiting
    calls = await _run(bot, room, _ev(HOTELS, _res(t2, ME, summary="two hotels"), notice=True),
                       reply="Here is your trip.")
    assert len(calls) == 1 and "Hotels (done): two hotels" in calls[0]["text"]
    assert calls[0]["band"] <= Band.TRUSTED                                                # no outward tools
    answer = _sent(bot)[-1]
    assert answer["msgtype"] == "m.text" and answer["body"] == "Here is your trip."
    assert answer["ai.windy.task"]["task_id"] == root.task_id and root.status == "done"
    n = len(_sent(bot))
    assert await _run(bot, room, _ev(HOTELS, _res(t2, ME), notice=True)) == []           # nothing after
    assert len(_sent(bot)) == n


@pytest.mark.asyncio
async def test_the_specialist_answers_a_request_once_as_a_result_with_no_outward_band():
    bot = _bot()
    room = _room()
    calls = await _run(bot, room, _ev(FLIGHTS, _req(task_id="tk_abcabcabcabc", ask="find flights"), notice=True),
                       reply="Three flights.")
    assert len(calls) == 1 and "Ask: find flights" in calls[0]["text"] and calls[0]["band"] <= Band.TRUSTED
    (res,) = _sent(bot)
    assert tw.parse(res) == {"type": "result", "task_id": "tk_abcabcabcabc", "status": "done",
                             "summary": "Three flights."}
    assert res["msgtype"] == "m.notice" and res["m.mentions"] == {"user_ids": [FLIGHTS]}
    assert res["m.relates_to"] == {"rel_type": "m.thread", "event_id": "$root"}
    assert await _run(bot, room, _ev(FLIGHTS, _req(task_id="tk_abcabcabcabc"), notice=True)) == []
    assert len(_sent(bot)) == 1


@pytest.mark.asyncio
async def test_typed_traffic_does_not_spend_the_untyped_backstop():
    bot = _bot()
    room = _room()
    for i in range(tb.limit() + 2):
        await _run(bot, room, _ev(FLIGHTS, _req(task_id=f"tk_{i:012x}"), notice=True))
    assert len(_sent(bot)) == tb.limit() + 2


@pytest.mark.asyncio
async def test_a_notice_is_never_a_turn_unless_it_is_a_typed_task(monkeypatch):
    bot = _bot()
    room = _room()
    untyped = {"msgtype": "m.notice", "body": "fyi", "m.mentions": {"user_ids": [ME]}}
    assert await _run(bot, room, _ev(FLIGHTS, untyped, notice=True)) == []
    monkeypatch.setenv("WINDY_TEAMWORK", "0")
    assert await _run(bot, room, _ev(FLIGHTS, _req(), notice=True)) == []
    bot.client.room_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_stop_during_the_turn_drops_the_result():
    bot = _bot()
    room = _room()
    await _run(bot, room, _ev(FLIGHTS, _req(), notice=True), during=lambda: rs.stop(ROOM))
    bot.client.room_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_contract_stop_in_a_team_room_cancels_tasks_and_one_agent_confirms():
    bot = _bot()
    bot.bot_user_id = "@agent_et26-0000-0000:chat.windychat.ai"            # the smallest id in the room
    room = _room((OWNER, bot.bot_user_id, FLIGHTS, HOTELS))
    task = tw.on_agent_message(ROOM, bot.bot_user_id, FLIGHTS, _req(to=bot.bot_user_id)).task
    assert await _run(bot, room, _ev(OWNER, {"msgtype": "m.text", "body": "ok stop talking to each other"})) == []
    assert rs.stopped(ROOM) and task.status == "blocked"
    (line,) = _sent(bot)
    assert line == {"msgtype": "m.notice", "body": tw.STOP_LINE, "windy_original": True}


@pytest.mark.asyncio
async def test_a_bigger_id_stops_silently():
    bot = _bot()                                                             # @windyfly sorts after @agent_
    assert await _run(bot, _room(), _ev(OWNER, {"msgtype": "m.text", "body": "enough now"})) == []
    assert rs.stopped(ROOM)
    bot.client.room_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_in_a_room_with_no_other_agent_stop_the_timer_is_answered():
    bot = _bot()
    calls = await _run(bot, _room((OWNER, ME)), _ev(OWNER, {"msgtype": "m.text", "body": "stop the timer"}))
    assert [c["text"] for c in calls] == ["stop the timer"] and not rs.stopped(ROOM)


@pytest.mark.asyncio
async def test_resume_event_and_the_stopped_state_from_the_owner_only():
    bot = _bot()
    room = _room()

    def ev(etype, sender=OWNER, content=None, state_key=None):
        e = MagicMock()
        e.type, e.sender = etype, sender
        e.source = {"type": etype, "content": content or {}}
        if state_key is not None:
            e.source["state_key"] = state_key
        return e

    await bot._on_stop_event(room, ev(tw.STOPPED_STATE, content={"stopped": True, "at": "x"}, state_key=""))
    assert rs.stopped(ROOM)
    await bot._on_stop_event(room, ev(tw.RESUME_EVENT, sender=FLIGHTS))
    assert rs.stopped(ROOM)                                                  # not the owner: nothing
    await bot._on_stop_event(room, ev(tw.RESUME_EVENT))
    assert not rs.stopped(ROOM)
    await bot._on_stop_event(room, ev(tw.STOPPED_STATE, content={"stopped": "yes", "at": "x"}, state_key=""))
    assert not rs.stopped(ROOM)                                              # malformed: nothing
    bot.client.room_send.assert_not_awaited()                                # resume is never answered


@pytest.mark.asyncio
async def test_at_the_deadline_the_owner_gets_one_plain_line_and_no_model_call():
    bot = _bot()
    bot.client.rooms = {ROOM: _room()}
    root = tw.open_root(ROOM, ME, "$o", "plan the trip")
    a, _ = tw.plan_request(root, ME, FLIGHTS, "flights", seconds=60)
    tw.sent(a)
    root.created -= 1000
    a.created -= 1000
    with patch("windyfly.channels.matrix_bot.agent_respond") as respond:
        await bot._expire_root(root)
        respond.assert_not_called()
    (line,) = _sent(bot)
    assert line["msgtype"] == "m.text" and "No answer from: Flights" in line["body"]
    assert line["ai.windy.task"]["status"] == "blocked"
    await bot._expire_root(root)
    assert len(_sent(bot)) == 1


@pytest.mark.asyncio
async def test_a_task_turn_is_capped_at_trusted_even_if_the_sender_resolved_higher():
    bot = _bot()
    with patch("windyfly.channels.identity.resolve_band", return_value=Band.OWNER):
        calls = await _run(bot, _room(), _ev(FLIGHTS, _req(), notice=True))
    assert calls and calls[0]["band"] == Band.TRUSTED


def test_the_guard_refuses_asking_back_the_agent_that_asked_me():
    incoming = tw.on_agent_message(ROOM, ME, FLIGHTS, _req()).task          # Flights asked me
    task, why = tw.plan_request(incoming, ME, FLIGHTS, "and what do you think?")
    assert task is None and "chain" in why
    assert tw.plan_request(incoming, ME, HOTELS, "hotels near the airport")[0] is not None


@pytest.mark.asyncio
async def test_results_on_a_spent_budget_get_the_plain_line_and_no_model_call():
    bot = _bot()
    root = tw.open_root(ROOM, ME, "$o", "plan the trip")
    a, _ = tw.plan_request(root, ME, FLIGHTS, "flights")
    tw.sent(a)
    root.turns = root.budget_turns
    calls = await _run(bot, _room(), _ev(FLIGHTS, _res(a.task_id, ME), notice=True))
    assert calls == []
    (line,) = _sent(bot)
    assert line["msgtype"] == "m.text" and line["ai.windy.task"]["status"] == "blocked"
    assert "Answered: Flights" in line["body"]


@pytest.mark.asyncio
async def test_a_deadline_in_a_stopped_room_says_nothing():
    bot = _bot()
    bot.client.rooms = {ROOM: _room()}
    root = tw.open_root(ROOM, ME, "$o", "plan the trip")
    a, _ = tw.plan_request(root, ME, FLIGHTS, "flights", seconds=60)
    tw.sent(a)
    root.created -= 1000
    a.created -= 1000
    rs.stop(ROOM)
    await bot._expire_root(root)
    bot.client.room_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_stop_does_not_confirm_again():
    bot = _bot()
    bot.bot_user_id = "@agent_et26-0000-0000:chat.windychat.ai"
    room = _room((OWNER, bot.bot_user_id, FLIGHTS, HOTELS))
    await _run(bot, room, _ev(OWNER, {"msgtype": "m.text", "body": "stop"}))
    await _run(bot, room, _ev(OWNER, {"msgtype": "m.text", "body": "STOP!"}))
    assert len(_sent(bot)) == 1
