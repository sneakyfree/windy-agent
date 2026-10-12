"""The /model menu comes from Windy Mind's catalog for this agent (Hub, 10-12).

The hand-kept list fell behind every release: "haiku" still meant 4.5 after Haiku 5.5 shipped, and
an agent asked for a new model said it didn't exist. Mind's GET /v1/catalog/me is the source; when
it can't be read, /model falls back to the hand list. The prompt names the model that actually
served the conversation, never the configured default.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from windyfly.agent import loop, mind_catalog, mind_self

CATALOG = [
    {"id": "claude-opus-5-5", "display_name": "Claude Opus 5.5", "power_rating": 98, "available_now": True},
    {"id": "claude-haiku-4-5", "display_name": "Claude Haiku 4.5", "power_rating": 70, "available_now": True},
    {"id": "claude-haiku-5-5", "display_name": "Claude Haiku 5.5", "power_rating": 80, "available_now": True},
    {"id": "claude-sonnet-5-5", "display_name": "Claude Sonnet 5.5", "power_rating": 90, "available_now": False},
    {"id": "claude-sonnet-5", "display_name": "Claude Sonnet 5", "power_rating": 85, "available_now": True},
]


@pytest.fixture(autouse=True)
def _state(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDYFLY_SESSION_COUNTER_PATH", str(tmp_path / "counters.json"))
    from windyfly.agent.session_reset import _reset_module_state_for_tests
    _reset_module_state_for_tests()
    mind_catalog._reset_for_tests()
    yield
    mind_catalog._reset_for_tests()
    _reset_module_state_for_tests()


def _mind_answers(monkeypatch, status=200, body=None, boom=False):
    """Stand in for Mind: an agent with a token, and one canned answer. Returns the call log."""
    calls: list[str] = []
    monkeypatch.setattr(mind_self, "enabled", lambda: True)
    monkeypatch.setattr(mind_self, "_ept", lambda: "ept-test")
    monkeypatch.setattr(mind_self, "_base", lambda: "http://127.0.0.1:9")

    def request(method, url, ept, body_, timeout, headers):
        calls.append(f"{method} {url}")
        if boom:
            raise OSError("down")
        return SimpleNamespace(status_code=status, json=lambda: body if body is not None else {"data": CATALOG})

    monkeypatch.setattr("windyfly.agent.mind_catalog.mind_auth.request", request)
    return calls


async def _model(raw: str) -> str:
    from windyfly.commands import core
    from windyfly.commands.registry import registry
    core.init_core()
    return await registry.get("model").handler({"platform": "telegram", "channel_id": "1", "_raw": raw})


def test_the_catalog_is_read_from_mind_with_the_agents_token_and_cached(monkeypatch):
    calls = _mind_answers(monkeypatch)
    assert [m["id"] for m in mind_catalog.my_catalog()] == [m["id"] for m in CATALOG]
    mind_catalog.my_catalog()
    assert calls == ["GET http://127.0.0.1:9/v1/catalog/me"]


@pytest.mark.parametrize("kw", [{"status": 500}, {"status": 401}, {"boom": True}, {"body": {"data": "x"}}])
def test_a_failed_read_is_none_and_is_not_retried_every_time(monkeypatch, kw):
    calls = _mind_answers(monkeypatch, **kw)
    assert mind_catalog.my_catalog() is None
    assert mind_catalog.my_catalog() is None
    assert len(calls) == 1


def test_no_token_means_no_call(monkeypatch):
    calls = _mind_answers(monkeypatch)
    monkeypatch.setattr(mind_self, "_ept", lambda: "")
    assert mind_catalog.my_catalog() is None
    assert calls == []


def test_a_family_word_means_the_strongest_of_that_family_available_now():
    assert mind_catalog.resolve("haiku", CATALOG)["id"] == "claude-haiku-5-5"
    assert mind_catalog.resolve("opus", CATALOG)["id"] == "claude-opus-5-5"
    assert mind_catalog.resolve("sonnet", CATALOG)["id"] == "claude-sonnet-5"  # 5.5 is down right now
    assert mind_catalog.resolve("Claude Haiku 4.5", CATALOG)["id"] == "claude-haiku-4-5"
    assert mind_catalog.resolve("CLAUDE-SONNET-5-5", CATALOG)["id"] == "claude-sonnet-5-5"
    assert mind_catalog.resolve("gpt-9", CATALOG) is None


@pytest.mark.asyncio
async def test_the_menu_lists_what_mind_offers(monkeypatch):
    _mind_answers(monkeypatch)
    reply = await _model("")
    assert "Models Windy Mind lets me use:" in reply
    for m in CATALOG:
        assert f"`{m['id']}`" in reply
    assert "Claude Sonnet 5.5 (not available right now)" in reply


@pytest.mark.asyncio
async def test_haiku_now_means_haiku_5_5(monkeypatch):
    from windyfly.agent.session_reset import get_model
    _mind_answers(monkeypatch)
    reply = await _model("haiku")
    assert "claude-haiku-5-5" in reply
    assert get_model("telegram", "1") == "claude-haiku-5-5"


@pytest.mark.asyncio
async def test_a_name_mind_does_not_offer_is_refused_with_what_it_does(monkeypatch):
    from windyfly.agent.session_reset import get_model
    _mind_answers(monkeypatch)
    reply = await _model("gpt-9")
    assert "doesn't offer me 'gpt-9'" in reply and "claude-opus-5-5" in reply
    assert get_model("telegram", "1") is None


@pytest.mark.asyncio
async def test_when_mind_cannot_be_read_the_hand_list_still_works(monkeypatch):
    from windyfly.agent.session_reset import get_model
    _mind_answers(monkeypatch, boom=True)
    assert "Available models:" in await _model("")
    await _model("opus")
    assert get_model("telegram", "1") == "claude-opus-5-5"


def test_a_turn_records_the_model_that_served_it_for_the_prompt(monkeypatch):
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue

    monkeypatch.setattr(loop, "_SERVED_FOR_PROMPT", {})
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue()
    wq.start()

    def fake_call(messages, *, model, temperature, max_tokens, tools, config, **kwargs):
        return {"content": "Hi there", "input_tokens": 1, "output_tokens": 1, "tool_calls": None,
                "model": model, "mind_model": "claude-haiku-5-5", "citations": [], "server_tools_used": 0}

    cfg = {"agent": {"default_model": "claude-sonnet-4-6"}, "memory": {"db_path": ":memory:"}}
    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=fake_call):
            loop.agent_respond(cfg, db, wq, "hello", "telegram:7:v0")
    finally:
        wq.stop()
        db.close()
    assert loop.served_model_for_prompt("telegram:7:v0") == "claude-haiku-5-5"
    assert loop.served_model_for_prompt("telegram:8:v0") is None
