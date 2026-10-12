"""A Claude model reached through Windy Mind keeps web search (Hub, 10-12).

The loop used to swap the client-side web_search for Anthropic's server-side tool whenever the
model was Claude. On the Mind route ``_mind_safe_request`` drops every non-function tool (no
provider behind Mind can run it), so an owner on Opus 5.5 ended up with no search at all.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent import models
from windyfly.memory.database import Database
from windyfly.memory.episodes import save_episode
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import register_web_search_tool


def _config() -> dict:
    return {
        "agent": {"default_model": "claude-opus-5-5", "max_context_tokens": 8000,
                  "max_response_tokens": 2000, "temperature": 0.7},
        "memory": {"db_path": ":memory:", "max_episodes_per_context": 20, "max_nodes_per_context": 10},
        "personality": {"soul_path": "SOUL.md", "humor_level": 7, "formality": 4, "proactivity": 5,
                        "verbosity": 5, "reasoning_depth": 6, "autonomy": 3, "epistemic_strictness": 5},
        "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
    }


def _tools_sent(monkeypatch, *, passport: bool, oauth: bool) -> list[dict]:
    """Run one owner turn on claude-opus-5-5 and return the tools handed to call_llm."""
    monkeypatch.setenv("DEFAULT_MODEL", "claude-opus-5-5")
    monkeypatch.setenv("MIND_API_URL", "http://127.0.0.1:9")  # never a real Mind
    monkeypatch.delenv("WINDY_NATIVE_WEB_SEARCH", raising=False)
    if passport:
        monkeypatch.setenv("ETERNITAS_PASSPORT", "ET26-TEST-0000")
    else:
        monkeypatch.delenv("ETERNITAS_PASSPORT", raising=False)
        monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    monkeypatch.setattr(models, "_max_oauth_active", lambda: oauth)
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue()
    wq.start()
    captured: dict = {}

    def fake_call(messages, *, model, temperature, max_tokens, tools, config, **kwargs):
        captured["tools"] = tools
        return {"content": "ok", "input_tokens": 1, "output_tokens": 1, "tool_calls": None,
                "model": model, "citations": [], "server_tools_used": 0}

    try:
        with patch("windyfly.agent.loop.is_online", return_value=True), \
             patch("windyfly.agent.loop.call_llm", side_effect=fake_call):
            from windyfly.agent.loop import agent_respond
            registry = ToolRegistry()
            register_web_search_tool(registry)
            agent_respond(_config(), db, wq, "what's new in the news today?", "telegram:1:v0",
                          tool_registry=registry)
    finally:
        wq.stop()
        db.close()
    return captured["tools"] or []


def _names(tools: list[dict]) -> set[str]:
    return {(t.get("function") or {}).get("name") or t.get("name") for t in tools}


@pytest.mark.parametrize("passport,oauth", [(True, False), (True, True), (False, False)])
def test_on_the_mind_route_the_client_web_search_is_kept(monkeypatch, passport, oauth):
    tools = _tools_sent(monkeypatch, passport=passport, oauth=oauth)
    assert models.mind_route_first()
    assert "web_search" in _names(tools)
    assert not [t for t in tools if t.get("type") and t.get("type") != "function"]


def test_what_mind_receives_is_function_tools_only_and_includes_web_search(monkeypatch):
    tools = _tools_sent(monkeypatch, passport=True, oauth=False)
    sent, _msgs, back = models._mind_safe_request(tools, [{"role": "user", "content": "hi"}])
    assert sent and all(t["type"] == "function" for t in sent)
    assert "web_search" in {back[t["function"]["name"]] for t in sent}


def test_the_direct_route_still_uses_anthropics_native_search(monkeypatch):
    # Max OAuth and no passport: call_llm goes straight to Anthropic, which runs the server tool.
    tools = _tools_sent(monkeypatch, passport=False, oauth=True)
    assert not models.mind_route_first()
    assert "web_search_20250305" in {t.get("type") for t in tools}
    assert "web_search" not in _names([t for t in tools if (t.get("type") or "function") == "function"])


def test_call_llm_and_the_tool_choice_use_the_same_route_rule():
    # One rule decides both, so the tool list can never disagree with where the turn goes.
    import inspect
    src = inspect.getsource(models.call_llm)
    assert "if mind_route_first():" in src
    assert "_has_passport() or not _max_oauth_active()" not in src
