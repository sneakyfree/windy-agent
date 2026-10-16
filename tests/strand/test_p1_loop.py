"""Strand v2, Part 1: genes 0.4 personality, 0.6 prompt + loop and 0.7 CLI (docs/strand/STRAND_V2.md).

The model is a stub: these prove what Windy Fly sends to it and does with its answer.
No network, no home dir.
"""

from __future__ import annotations

from unittest.mock import patch

from windyfly.agent.loop import agent_respond
from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry

MARK = "Strand-marker: always answer in exactly one haiku."


def _config(tmp_path):
    soul = tmp_path / "SOUL.md"
    soul.write_text(f"# Soul\n\n{MARK}\n", encoding="utf-8")
    return {"agent": {"default_model": "claude-sonnet-4-6"},
            "memory": {"db_path": str(tmp_path / "loop.db")},
            "personality": {"preset": "buddy", "soul_path": str(soul)}}


def test_p1_04_soul_md_reaches_the_system_message(tmp_path):
    db = Database(str(tmp_path / "p.db"))
    try:
        messages = assemble_prompt(_config(tmp_path), db, "hello", "s1")
    finally:
        db.close()
    assert messages[0]["role"] == "system" and MARK in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "hello"}


def _turn(cfg, db, text, session, seen):
    def llm(messages, *args, **kwargs):
        seen.append([m.get("content") or "" for m in messages])
        return {"content": f"reply to: {text}", "tool_calls": None, "input_tokens": 5, "output_tokens": 5}

    wq = WriteQueue()
    wq.start()
    try:
        with patch("windyfly.agent.loop.call_llm", side_effect=llm):
            return agent_respond(cfg, db, wq, text, session, ToolRegistry())
    finally:
        wq.stop()   # flushes the episode writes


def test_p1_06_remembers_the_last_turn_in_the_session(tmp_path):
    cfg = _config(tmp_path)
    db = Database(cfg["memory"]["db_path"])
    seen: list[list[str]] = []
    try:
        _turn(cfg, db, "my cat is called Biscuit", "sess-a", seen)
        _turn(cfg, db, "what is my cat called?", "sess-a", seen)
        _turn(cfg, db, "what is my cat called?", "sess-b", seen)   # another session
    finally:
        db.close()
    second, other = "\n".join(seen[-2]), "\n".join(seen[-1])
    assert "my cat is called Biscuit" in second and "reply to: my cat is called Biscuit" in second
    assert "my cat is called Biscuit" not in other


def test_p1_07_cli_turn_goes_through_the_agent_and_is_printed(tmp_path, monkeypatch):
    from windyfly.channels import cli

    monkeypatch.delenv("WINDY_JWT", raising=False)
    printed: list[str] = []
    calls: list[tuple[str, str]] = []

    def fake_respond(config, db, wq, text, session_id, registry):
        calls.append((text, session_id))
        return "hi from the fly"

    monkeypatch.setattr(cli, "agent_respond", fake_respond)
    lines = iter(["hello fly", "quit"])
    monkeypatch.setattr(cli.console, "input", lambda prompt="": next(lines))
    monkeypatch.setattr(cli.console, "print", lambda *a, **k: printed.append(" ".join(map(str, a))))
    cli.run_cli(_config(tmp_path))

    assert [c[0] for c in calls] == ["hello fly"]
    assert len(calls[0][1]) == 36   # a UUID4 session id
    assert any("hi from the fly" in p for p in printed)


def test_p1_05_model_names_route_to_their_provider(monkeypatch):
    from windyfly.agent import providers

    monkeypatch.setenv("WINDYFLY_PROVIDERS_PATH", "/nonexistent/providers.json")
    route = {m: providers.get_provider_for_model(m)["provider_key"]
             for m in ("gpt-4o-mini", "o3-mini", "claude-sonnet-4-6", "gemini-2.5-pro", "grok-3",
                       "claude-future-9", "gpt-future-9")}   # the last two match by prefix only
    assert route == {"gpt-4o-mini": "openai", "o3-mini": "openai", "claude-sonnet-4-6": "anthropic",
                     "gemini-2.5-pro": "gemini", "grok-3": "grok",
                     "claude-future-9": "anthropic", "gpt-future-9": "openai"}


def test_p1_07_main_loads_env_files_before_the_config():
    import ast
    import inspect

    from windyfly import main as main_mod

    fn = next(n for n in ast.parse(inspect.getsource(main_mod)).body
              if isinstance(n, ast.FunctionDef) and n.name == "main")
    order = [n.func.id for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("load_dotenv", "load_config")]
    assert order[:1] == ["load_dotenv"] and "load_config" in order


def test_p1_06_the_whole_prompt_stays_under_the_effective_limit(tmp_path):
    """A model window of 6,000 tokens and ~10,000 tokens of history: the call keeps the soul, the
    current message and the newest history, drops the oldest, and fits the window less the reply reserve."""
    from types import SimpleNamespace

    from windyfly.agent.prompt import estimate_tokens, reply_reserve
    from windyfly.memory.episodes import save_episode

    cfg = _config(tmp_path)
    db = Database(cfg["memory"]["db_path"])
    for n in range(20):
        save_episode(db, "user" if n % 2 == 0 else "assistant", f"old-{n:02d} " + "x" * 2000, session_id="big")
    seen: list[list[str]] = []
    try:
        with patch("windyfly.agent.models_catalog.resolve", return_value=SimpleNamespace(native_cap=6000)):
            _turn(cfg, db, "and now?", "big", seen)
    finally:
        db.close()
    main = next(call for call in seen if MARK in call[0])
    assert main[-1] == "and now?"
    assert estimate_tokens([{"content": c} for c in main]) <= 6000 - reply_reserve(6000)
    kept = [c[:6] for c in main if c.startswith("old-")]
    assert kept and "old-19" in kept and "old-00" not in kept
    assert kept == sorted(kept)                       # what is left is the newest, still in order
