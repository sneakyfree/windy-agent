"""Mind's no-third-party gate (Mind 10-16 privacy release): an owner whose account uses only
Windy's own computers.

503 no_third_party_unavailable = Windy's own computers are busy for a few seconds, NOT an outage:
wait Retry-After once and ask once more; still busy = no Mind cool-down, no direct chain except a
model on the owner's own machine (said plainly, no 🚨), and never the loop's auto-resurrect (it
latches the backup brain for later turns). 403 no_third_party_model = the named model runs outside
Windy: no model swap, no fall-through, one plain line. Nothing to another agent. A real Mind outage
still lifeboats / resurrects exactly as before. Mocked Mind and models only: no network.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

os.environ["WINDYFLY_PROVIDERS_PATH"] = "/tmp/windyfly-test-no-such-file.json"

from windyfly.agent import loop, models  # noqa: E402

LOCAL = {"provider_key": "ollama", "type": "openai", "api_key": "ollama", "base_url": "http://localhost:11434/v1"}
OUTSIDE = {"provider_key": "groq", "type": "openai", "api_key": "k", "base_url": "https://api.groq.com/openai/v1"}
LOCAL_REPLY = {"content": "from the local model", "input_tokens": 1, "output_tokens": 1,
               "tool_calls": [], "citations": [], "server_tools_used": []}
MIND_OK = {"model": "m", "choices": [{"message": {"content": "from mind"}}]}


def _resp(status: int, error: str = "", retry_after: str | None = None, body: dict | None = None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.headers = {k: v for k, v in {"x-mind-error": error, "retry-after": retry_after}.items() if v}
    r.json.return_value = body or {}
    r.text = ""
    return r


BUSY = _resp(503, "no_third_party_unavailable", retry_after="10")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MIND_API_URL", "ETERNITAS_PASSPORT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", "ept_test")
    monkeypatch.setattr(models, "_mind_helper_enabled", lambda: False)
    models._provider_cooldowns.clear()
    yield
    models._provider_cooldowns.clear()


def _call_with_mind(monkeypatch, answers, *, provider=LOCAL, model=None):
    """call_llm with Mind answering `answers` in order and one chain entry (`provider`)."""
    sleeps: list[float] = []
    monkeypatch.setattr(models.time, "sleep", lambda s: sleeps.append(s))
    it = iter(answers)
    with patch("httpx.post", side_effect=lambda *a, **k: next(it)) as mind, \
            patch.object(models, "_call_openai", return_value=dict(LOCAL_REPLY)) as direct, \
            patch.object(models, "_build_chain", return_value=["chain-model"]), \
            patch.object(models, "get_provider_for_model", side_effect=lambda m, c=None: provider):
        try:
            out = models.call_llm([{"role": "user", "content": "hi"}], model=model)
        except Exception as exc:  # noqa: BLE001
            out = exc
    return out, mind.call_count, direct.call_count, sleeps


# ── the model layer ─────────────────────────────────────────────────────────────────────

def test_busy_waits_retry_after_once_then_the_owners_local_model_answers_marked(monkeypatch):
    out, mind_calls, local_calls, sleeps = _call_with_mind(monkeypatch, [BUSY, BUSY])
    assert out["content"] == "from the local model" and out["mind_busy_local"] is True
    assert (mind_calls, local_calls) == (2, 1)
    assert sleeps == [10.0]
    assert not models._is_provider_in_cooldown("windy-mind")


def test_busy_with_no_local_model_never_calls_an_outside_provider(monkeypatch):
    out, mind_calls, direct_calls, _ = _call_with_mind(monkeypatch, [BUSY, BUSY], provider=OUTSIDE)
    assert isinstance(out, models.MindNoThirdPartyBusy)
    assert "providers" not in str(out).lower()  # never the loop's chain-exhausted branch
    assert (mind_calls, direct_calls) == (2, 0)
    assert not models._is_provider_in_cooldown("windy-mind")


def test_busy_then_free_answers_from_mind_unmarked(monkeypatch):
    out, mind_calls, local_calls, _ = _call_with_mind(monkeypatch, [BUSY, _resp(200, body=MIND_OK)])
    assert out["content"] == "from mind" and "mind_busy_local" not in out
    assert (mind_calls, local_calls) == (2, 0)


def test_the_busy_wait_is_capped_at_30_seconds(monkeypatch):
    long = _resp(503, "no_third_party_unavailable", retry_after="600")
    _, _, _, sleeps = _call_with_mind(monkeypatch, [long, long])
    assert sleeps == [30.0]


def test_an_outside_model_is_refused_with_no_swap_and_no_fall_through(monkeypatch):
    refused = _resp(403, "no_third_party_model")
    out, mind_calls, direct_calls, _ = _call_with_mind(monkeypatch, [refused], model="claude-opus-5-5")
    assert isinstance(out, models.MindNoThirdPartyModel) and out.model == "claude-opus-5-5"
    assert (mind_calls, direct_calls) == (1, 0)
    assert not models._is_provider_in_cooldown("windy-mind")


def test_the_helper_path_refuses_an_outside_model_the_same_way(monkeypatch):
    from windyfly._vendor.mind_client import MindRefusedError

    monkeypatch.setattr(models, "_mind_helper_enabled", lambda: True)
    err = MindRefusedError(403, "no_third_party_model", "outside", {"x-mind-error": "no_third_party_model"})
    with patch("windyfly.agent.mind_helper.chat", side_effect=err), \
            patch.object(models, "_call_openai", return_value=dict(LOCAL_REPLY)) as direct, \
            patch.object(models, "_build_chain", return_value=["chain-model"]), \
            patch.object(models, "get_provider_for_model", side_effect=lambda m, c=None: LOCAL):
        with pytest.raises(models.MindNoThirdPartyModel):
            models.call_llm([{"role": "user", "content": "hi"}], model="claude-opus-5-5")
    assert direct.call_count == 0


def test_a_real_mind_outage_still_uses_the_lifeboat_unmarked(monkeypatch):
    down = _resp(503, "mind_unavailable", retry_after="5")
    out, _, local_calls, _ = _call_with_mind(monkeypatch, [down, down])
    assert out["content"] == "from the local model" and local_calls == 1
    assert "mind_busy_local" not in out


# ── the loop: plain lines, never the auto-resurrect ─────────────────────────────────────

def _respond(monkeypatch, *, raise_exc=None, result=None, session="sess-ntp", agent_sender=False):
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
    llm = {"side_effect": raise_exc} if raise_exc is not None else {"return_value": result}
    with tempfile.TemporaryDirectory() as td:
        db = Database(str(Path(td) / "n.db"))
        wq = WriteQueue()
        wq.start()
        try:
            if agent_sender:
                silence.mark_agent_sender(session)
            with patch("windyfly.agent.loop.call_llm", **llm):
                reply = agent_respond(cfg, db, wq, "are you there?", session, ToolRegistry())
        finally:
            silence.clear_agent_sender(session)
            wq.stop()
            db.close()
    return reply, len(resurrects), len(offline)


def test_busy_says_one_plain_line_and_never_resurrects(monkeypatch):
    reply, resurrects, offline = _respond(monkeypatch, raise_exc=models.MindNoThirdPartyBusy())
    assert reply == loop.NO_THIRD_PARTY_BUSY_REPLY
    assert "\U0001f6a8" not in reply
    assert (resurrects, offline) == (0, 0)


def test_an_outside_model_says_one_plain_line_and_never_resurrects(monkeypatch):
    reply, resurrects, offline = _respond(monkeypatch, raise_exc=models.MindNoThirdPartyModel("x"))
    assert reply == loop.NO_THIRD_PARTY_MODEL_REPLY
    assert (resurrects, offline) == (0, 0)


def test_no_line_in_answer_to_another_agent(monkeypatch):
    reply, resurrects, offline = _respond(monkeypatch, raise_exc=models.MindNoThirdPartyBusy(), agent_sender=True)
    assert reply == "" and (resurrects, offline) == (0, 0)


def test_a_local_answer_while_busy_says_so_plainly(monkeypatch):
    reply, resurrects, _ = _respond(monkeypatch, result={**LOCAL_REPLY, "mind_busy_local": True})
    assert reply.startswith(loop.NO_THIRD_PARTY_LOCAL_PREFIX)
    assert "from the local model" in reply and "\U0001f6a8" not in reply
    assert resurrects == 0


def test_a_real_outage_still_tries_to_resurrect(monkeypatch):
    _, resurrects, _ = _respond(
        monkeypatch, raise_exc=RuntimeError("All providers in chain failed (windy-mind: mind http 503)"))
    assert resurrects == 1
