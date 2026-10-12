"""Mind could not read this agent's pause switch (503 grant_unavailable): FAIL CLOSED (Hub 10-12).

Mind contract (mind-refusal, Mind #401): grant_unavailable = Mind spent nothing because it could
not read the owner's pause switch; WAIT Retry-After, then try once more. The owner's stop may be in
force, so the agent must not answer from anywhere else: not the local lifeboat in the chain, not
the loop's auto-resurrect to Ollama, not a provider key. It says one pinned line to a person, once
per window, and nothing to another agent. A real Mind outage (mind_unavailable, 5xx, timeouts)
still lifeboats / resurrects exactly as before. Mocked Mind and models only: no network.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import loop, models  # noqa: E402

PINNED = "I can't check my pause switch right now, so I'm holding off. I'll try again shortly."
LOCAL = {"provider_key": "ollama", "type": "openai", "api_key": "ollama", "base_url": "http://localhost:11434/v1"}
LIFEBOAT_REPLY = {"content": "lifeboat", "input_tokens": 1, "output_tokens": 1,
                  "tool_calls": [], "citations": [], "server_tools_used": []}


def _resp(status: int, error: str = "", retry_after: str | None = None, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {k: v for k, v in {"x-mind-error": error, "retry-after": retry_after}.items() if v}
    r.json.return_value = body or {}
    r.text = ""
    return r


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MIND_API_URL", "ETERNITAS_PASSPORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test")
    monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)
    models._provider_cooldowns.clear()
    loop._reset_pause_unreadable_for_tests()
    yield
    models._provider_cooldowns.clear()
    loop._reset_pause_unreadable_for_tests()


def _call_with_mind(monkeypatch, answers):
    """call_llm with Mind answering `answers` in order and a local lifeboat model in the chain."""
    sleeps: list[float] = []
    monkeypatch.setattr(models.time, "sleep", lambda s: sleeps.append(s))
    it = iter(answers)
    with patch("httpx.post", side_effect=lambda *a, **k: next(it)) as mind, \
            patch.object(models, "_call_openai", return_value=LIFEBOAT_REPLY) as lifeboat, \
            patch.object(models, "_build_chain", return_value=["llama3.2:3b"]), \
            patch.object(models, "get_provider_for_model", side_effect=lambda m, c=None: LOCAL):
        try:
            out = models.call_llm([{"role": "user", "content": "hi"}])
        except Exception as exc:  # noqa: BLE001
            out = exc
    return out, mind.call_count, lifeboat.call_count, sleeps


# ── the model layer: the chain lifeboat door ────────────────────────────────────────────

def test_unreadable_switch_waits_retries_once_then_stops_without_the_lifeboat(monkeypatch):
    unreadable = _resp(503, "grant_unavailable", retry_after="30")
    out, mind_calls, lifeboat_calls, sleeps = _call_with_mind(monkeypatch, [unreadable, unreadable])
    assert isinstance(out, models.MindPauseUnreadable)
    assert (mind_calls, lifeboat_calls) == (2, 0)
    assert sleeps == [30.0]
    assert not models._is_provider_in_cooldown("windy-mind")


def test_the_wait_is_capped_at_30_seconds(monkeypatch):
    unreadable = _resp(503, "grant_unavailable", retry_after="600")
    _, _, _, sleeps = _call_with_mind(monkeypatch, [unreadable, unreadable])
    assert sleeps == [30.0]


def test_a_readable_switch_on_the_retry_answers_normally(monkeypatch):
    ok = _resp(200, body={"model": "m", "choices": [{"message": {"content": "from mind"}}]})
    out, mind_calls, lifeboat_calls, _ = _call_with_mind(
        monkeypatch, [_resp(503, "grant_unavailable", retry_after="30"), ok])
    assert out["content"] == "from mind" and (mind_calls, lifeboat_calls) == (2, 0)


def test_a_real_mind_outage_still_uses_the_lifeboat(monkeypatch):
    down = _resp(503, "mind_unavailable", retry_after="5")
    out, _, lifeboat_calls, _ = _call_with_mind(monkeypatch, [down, down])
    assert out["content"] == "lifeboat" and lifeboat_calls == 1


def test_grant_off_is_still_the_owners_hard_stop(monkeypatch):
    out, mind_calls, lifeboat_calls, _ = _call_with_mind(monkeypatch, [_resp(403, "grant_off")])
    assert isinstance(out, models.AgentPausedByOwner) and not isinstance(out, models.MindPauseUnreadable)
    assert (mind_calls, lifeboat_calls) == (1, 0)
    models.clear_owner_pause()


# ── the loop: the auto-resurrect door, and the owner's line ─────────────────────────────

def _respond(monkeypatch, raise_exc, *, session="sess-gu", agent_sender=False):
    import tempfile
    from pathlib import Path

    from windyfly.agent.loop import agent_respond
    from windyfly.channels import silence
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.tools.registry import ToolRegistry

    resurrects: list[int] = []
    offline: list[int] = []
    monkeypatch.setattr(loop, "is_online", lambda: True)
    monkeypatch.setattr("windyfly.agent.resurrect.auto_resurrect_attempt",
                        lambda **k: resurrects.append(1) or {"ok": False, "reason": "disabled"})
    monkeypatch.setattr(loop, "get_offline_response", lambda *a, **k: offline.append(1) or "offline")
    cfg = {"agent": {"default_model": "claude-sonnet-4-6"}, "memory": {}, "personality": {"preset": "buddy"}}
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "g.db"))
        wq = WriteQueue()
        wq.start()
        try:
            if agent_sender:
                silence.mark_agent_sender(session)
            with patch("windyfly.agent.loop.call_llm", side_effect=raise_exc):
                reply = agent_respond(cfg, db, wq, "are you there?", session, ToolRegistry())
        finally:
            silence.clear_agent_sender(session)
            wq.stop()
            db.close()
    return reply, len(resurrects), len(offline)


def test_the_loop_never_resurrects_behind_an_unreadable_switch(monkeypatch):
    reply, resurrects, offline = _respond(monkeypatch, models.MindPauseUnreadable())
    assert reply == PINNED
    assert (resurrects, offline) == (0, 0)


def test_the_line_is_said_once_per_window_not_per_message(monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(loop.time, "time", lambda: clock[0])
    first, _, _ = _respond(monkeypatch, models.MindPauseUnreadable())
    second, _, _ = _respond(monkeypatch, models.MindPauseUnreadable())
    clock[0] += loop.PAUSE_UNREADABLE_WINDOW_S + 1
    third, _, _ = _respond(monkeypatch, models.MindPauseUnreadable())
    assert (first, second, third) == (PINNED, "", PINNED)


def test_no_line_in_answer_to_another_agent(monkeypatch):
    reply, resurrects, offline = _respond(monkeypatch, models.MindPauseUnreadable(), agent_sender=True)
    assert reply == "" and (resurrects, offline) == (0, 0)


def test_a_real_outage_still_tries_to_resurrect(monkeypatch):
    _, resurrects, _ = _respond(monkeypatch, RuntimeError("All providers in chain failed (windy-mind: mind http 503)"))
    assert resurrects == 1
