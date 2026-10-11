"""Strand-to-green Wave 4: prove the agent-loop yellows with tests.

Codons covered here (names carry the codon so the audit can grep them):

* C0.9  in-session recall: turn 2 of a session sees turn 1 in its messages.
* C1.10 a Windy Pro tool (get_translation_history) runs through
        agent_respond via function calling and its result is fed back.
* C9.3  the real journal helper writes a ``journal_entry`` node on the
        10th interaction of a session (and not before).
* C9.2  the real relationship-moment path saves a ``relationship_moment``
        node on a non-neutral turn.

No network and no real model: ``call_llm`` and ``is_online`` are patched
in ``windyfly.agent.loop`` (same pattern as
tests/test_confab_retry_ends_with_user.py), and httpx is patched for the
Windy Pro tool.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.nodes import get_nodes_by_type
from windyfly.memory.write_queue import WriteQueue

JOURNAL_SYSTEM = "You write brief, genuine diary entries."
MOMENT_SYSTEM = "You write brief emotional summaries."


def _make_config() -> dict:
    return {
        "agent": {
            "default_model": "claude-haiku-4-5-20251001",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {
            "db_path": ":memory:",
            "max_episodes_per_context": 20,
            "max_nodes_per_context": 10,
        },
        "personality": {
            "soul_path": "SOUL.md",
            "humor_level": 7, "formality": 4, "proactivity": 5,
            "verbosity": 5, "reasoning_depth": 6, "autonomy": 3,
            "epistemic_strictness": 5,
        },
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


def _llm_result(content: str, tool_calls=None) -> dict:
    return {
        "content": content,
        "input_tokens": 5,
        "output_tokens": 5,
        "tool_calls": tool_calls,
        "model": "claude-haiku-4-5-20251001",
        "citations": [],
        "server_tools_used": 0,
    }


def _drain(wq: WriteQueue) -> None:
    """Block until every write enqueued so far has been processed.

    The worker calls ``task_done`` for each item (finally-block), so
    ``join`` on the underlying queue is an exact barrier. Medium-priority
    batches are flushed before the worker goes idle on an empty queue,
    so wait for that too.
    """
    wq._queue.join()


@pytest.fixture
def stack():
    db = Database(":memory:")
    # A prior episode so the brand-new-bot welcome can never short-circuit.
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue()
    wq.start()
    yield _make_config(), db, wq
    try:
        wq.stop()
    except Exception:
        pass
    db.close()


@pytest.fixture(autouse=True)
def _fresh_journal_counts():
    from windyfly.agent.loop import _session_interaction_counts
    _session_interaction_counts.clear()
    yield
    _session_interaction_counts.clear()


def _is_helper_call(messages: list[dict]) -> bool:
    first = messages[0].get("content", "") if messages else ""
    return first in (JOURNAL_SYSTEM, MOMENT_SYSTEM)


# ── C0.9 in-session recall ──────────────────────────────────────────


def test_C0_9_second_turn_messages_contain_first_turn(stack):
    config, db, wq = stack
    session = "wave4-recall-session"
    first_user = "My favourite colour is teal and my dog is called Biscuit."
    first_reply = "Teal suits you, and Biscuit is a fine name for a dog."
    second_user = "Remind me what my dog is called."

    main_calls: list[list[dict]] = []
    replies = iter([first_reply, "Your dog is called Biscuit."])

    def fake_call_llm(messages, **kwargs):
        if _is_helper_call(messages):
            return _llm_result("helper output that is long enough")
        main_calls.append([dict(m) for m in messages])
        return _llm_result(next(replies))

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
         patch("windyfly.agent.loop.is_online", return_value=True):
        r1 = agent_respond(config, db, wq, first_user, session)
        _drain(wq)
        r2 = agent_respond(config, db, wq, second_user, session)

    assert first_reply in r1
    assert "Biscuit" in r2
    assert len(main_calls) == 2, f"expected one model call per turn, got {len(main_calls)}"

    second = main_calls[1]
    user_contents = [m.get("content", "") for m in second if m.get("role") == "user"]
    asst_contents = [m.get("content", "") for m in second if m.get("role") == "assistant"]
    assert any(first_user in c for c in user_contents), (
        "turn 1's user message is missing from turn 2's model input: "
        f"{user_contents!r}"
    )
    assert any(first_reply in c for c in asst_contents), (
        "turn 1's reply is missing from turn 2's model input: "
        f"{asst_contents!r}"
    )
    # Order: the first exchange comes before the new question, and the
    # new question is the last user message.
    idx_first = next(i for i, m in enumerate(second)
                     if m.get("role") == "user" and first_user in m.get("content", ""))
    idx_second = max(i for i, m in enumerate(second)
                     if m.get("role") == "user" and second_user in m.get("content", ""))
    assert idx_first < idx_second
    assert second[-1]["role"] == "user" and second_user in second[-1]["content"]


def test_C0_9_recall_is_scoped_to_the_session(stack):
    """The other half of the contract: a DIFFERENT session's turn does not
    leak into this session's turn-2 history."""
    config, db, wq = stack
    main_calls: list[list[dict]] = []

    def fake_call_llm(messages, **kwargs):
        if _is_helper_call(messages):
            return _llm_result("helper output that is long enough")
        main_calls.append([dict(m) for m in messages])
        return _llm_result("Okay, noted for this chat.")

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
         patch("windyfly.agent.loop.is_online", return_value=True):
        agent_respond(config, db, wq, "The secret word is pumpernickel.", "session-a")
        _drain(wq)
        agent_respond(config, db, wq, "Hello from a different chat.", "session-b")

    history_b = [m for m in main_calls[1] if m.get("role") in ("user", "assistant")]
    assert not any("pumpernickel" in (m.get("content") or "") for m in history_b)


# ── C1.10 Windy Pro tool via function calling ───────────────────────


def test_C1_10_translation_history_tool_runs_through_agent_respond(stack):
    config, db, wq = stack
    from windyfly.tools.registry import ToolRegistry
    from windyfly.tools.windy_api import register_windy_tools

    registry = ToolRegistry()
    # The Windy Pro tools register only when Windy Pro answers this agent's
    # credential at start (#510); here it does.
    with patch("windyfly.tools.windy_api.windy_pro_answers", return_value=True):
        register_windy_tools(registry)

    history_payload = {
        "translations": [
            {"source": "hola", "target": "hello", "lang": "es-en"},
            {"source": "merci", "target": "thank you", "lang": "fr-en"},
        ]
    }
    fake_resp = MagicMock()
    fake_resp.json.return_value = history_payload
    fake_resp.raise_for_status.return_value = None

    tool_call = {
        "id": "call_wave4_hist",
        "type": "function",
        "function": {
            "name": "get_translation_history",
            "arguments": json.dumps({"limit": 2}),
        },
    }
    calls: list[dict] = []

    def fake_call_llm(messages, **kwargs):
        if _is_helper_call(messages):
            return _llm_result("helper output that is long enough")
        calls.append({"messages": [dict(m) for m in messages],
                      "tools": kwargs.get("tools")})
        if len(calls) == 1:
            return _llm_result("", tool_calls=[tool_call])
        return _llm_result("You recently translated 'hola' and 'merci'.")

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
         patch("windyfly.agent.loop.is_online", return_value=True), \
         patch("windyfly.tools.windy_api.httpx.get", return_value=fake_resp) as http_get:
        reply = agent_respond(config, db, wq,
                              "Show me my recent translations", "wave4-pro-tool",
                              tool_registry=registry)

    # The model was offered the tool on the first call.
    offered = [
        (t.get("function") or {}).get("name") or t.get("name")
        for t in (calls[0]["tools"] or [])
    ]
    assert "get_translation_history" in offered

    # The real tool ran and hit the Windy Pro history endpoint with the
    # model's arguments (httpx mocked: no network).
    http_get.assert_called_once()
    url = http_get.call_args.args[0]
    assert url.endswith("/api/v1/user/history")
    assert http_get.call_args.kwargs["params"] == {"limit": 2}

    # The tool result was fed back to the model as a role=tool message
    # bound to the call id, and the final text is what the user gets.
    assert len(calls) == 2
    tool_msgs = [m for m in calls[1]["messages"] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call_wave4_hist"
    assert json.loads(tool_msgs[0]["content"]) == history_payload
    asst_with_call = [m for m in calls[1]["messages"]
                      if m.get("role") == "assistant" and m.get("tool_calls")]
    assert asst_with_call and asst_with_call[-1]["tool_calls"][0]["id"] == "call_wave4_hist"
    assert "hola" in reply and "merci" in reply


# ── C9.3 journal on the 10th interaction ────────────────────────────


def _journal_nodes(db):
    return get_nodes_by_type(db, "journal_entry", limit=50)


def test_C9_3_journal_helper_writes_entry_on_tenth_interaction(stack):
    config, db, wq = stack
    from windyfly.agent.loop import _maybe_write_journal_entry

    entry = "Today we planned the garden together and the user seemed calm."
    llm = MagicMock(return_value=_llm_result(entry))

    with patch("windyfly.agent.loop.call_llm", llm):
        for i in range(1, 10):
            _maybe_write_journal_entry(db, wq, config, f"neutral message {i}",
                                       "a reply", "neutral", "wave4-journal")
        _drain(wq)
        assert llm.call_count == 0, "journal must not fire before the 10th turn"
        assert _journal_nodes(db) == []

        _maybe_write_journal_entry(db, wq, config, "tenth message",
                                   "tenth reply", "neutral", "wave4-journal")
        _drain(wq)

    assert llm.call_count == 1
    assert llm.call_args.args[0][0]["content"] == JOURNAL_SYSTEM
    nodes = _journal_nodes(db)
    assert len(nodes) == 1
    node = nodes[0]
    assert node["source"] == "agent_journal"
    meta = json.loads(node["metadata"]) if isinstance(node["metadata"], str) else node["metadata"]
    assert meta["entry"] == entry
    assert meta["session_id"] == "wave4-journal"
    assert meta["emotional_context"] == "neutral"


def test_C9_3_journal_fires_on_tenth_agent_respond_turn(stack):
    """End to end: ten ordinary turns in one session through agent_respond;
    the journal LLM call happens exactly once, on turn 10, and the node
    lands in memory."""
    config, db, wq = stack
    entry = "We chatted about tomatoes all afternoon; the user was relaxed."
    journal_calls_after_turn: list[int] = []
    turn = {"n": 0}

    def fake_call_llm(messages, **kwargs):
        if messages and messages[0].get("content") == JOURNAL_SYSTEM:
            journal_calls_after_turn.append(turn["n"])
            return _llm_result(entry)
        if messages and messages[0].get("content") == MOMENT_SYSTEM:
            return _llm_result("helper output that is long enough")
        return _llm_result("Tomatoes like full sun and steady water.")

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
         patch("windyfly.agent.loop.is_online", return_value=True):
        for n in range(1, 11):
            turn["n"] = n
            agent_respond(config, db, wq, f"Tell me tomato fact number {n}",
                          "wave4-journal-e2e")
            _drain(wq)
            if n < 10:
                assert _journal_nodes(db) == [], f"journal written early at turn {n}"

    assert journal_calls_after_turn == [10]
    nodes = _journal_nodes(db)
    assert len(nodes) == 1
    assert entry[:40] in nodes[0]["name"]


# ── C9.2 relationship moment on a non-neutral turn ──────────────────


def test_C9_2_extract_relationship_moment_saves_node(stack):
    config, db, wq = stack
    from windyfly.agent.loop import _extract_relationship_moment

    moment = "stressed → we untangled the broken router together → relief"
    llm = MagicMock(return_value=_llm_result(f'"{moment}"'))
    with patch("windyfly.agent.loop.call_llm", llm):
        _extract_relationship_moment(
            db, wq, config,
            "ugh my router is broken again", "Let's reset it step by step.",
            "stressed", "wave4-moment",
        )
        _drain(wq)

    assert llm.call_count == 1
    assert llm.call_args.args[0][0]["content"] == MOMENT_SYSTEM
    nodes = get_nodes_by_type(db, "relationship_moment", limit=10)
    assert len(nodes) == 1
    node = nodes[0]
    assert node["source"] == "agent_observed"
    meta = json.loads(node["metadata"]) if isinstance(node["metadata"], str) else node["metadata"]
    assert meta["summary"] == moment  # surrounding quotes stripped
    assert meta["emotional_context"] == "stressed"
    assert meta["session_id"] == "wave4-moment"


def test_C9_2_non_neutral_agent_turn_saves_moment_and_neutral_does_not(stack):
    config, db, wq = stack
    moment = "frustrated → we fixed the printer queue together → relief"

    def fake_call_llm(messages, **kwargs):
        first = messages[0].get("content") if messages else ""
        if first == MOMENT_SYSTEM:
            return _llm_result(moment)
        if first == JOURNAL_SYSTEM:
            return _llm_result("A short diary entry about the printer today.")
        return _llm_result("Let's clear the print queue first.")

    from windyfly.agent.loop import agent_respond
    with patch("windyfly.agent.loop.call_llm", side_effect=fake_call_llm), \
         patch("windyfly.agent.loop.is_online", return_value=True):
        agent_respond(config, db, wq, "What is a print queue?", "wave4-moment-e2e")
        _drain(wq)
        assert get_nodes_by_type(db, "relationship_moment", limit=10) == []

        agent_respond(config, db, wq,
                      "ugh, I'm so frustrated, the printer is jammed again",
                      "wave4-moment-e2e")
        _drain(wq)

    nodes = get_nodes_by_type(db, "relationship_moment", limit=10)
    assert len(nodes) == 1
    meta = json.loads(nodes[0]["metadata"]) if isinstance(nodes[0]["metadata"], str) else nodes[0]["metadata"]
    assert meta["summary"] == moment
    assert meta["emotional_context"] == "stressed"
