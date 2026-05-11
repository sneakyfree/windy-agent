"""Regression: lifeboat-timeout path must actually queue the message.

Surfaced 2026-05-11 by the overnight stress harness: prompts #34-#38
and #89-#90 hit the Anthropic rate limit, auto-resurrected into
lifeboat, then llama3.2:3b timed out at 30s. The user saw

    🛟 Local model error: timed out talking to my backup brain.
    Your message is queued; I'll try again when I'm healthier.

— but ``loop.py``'s resurrection short-circuit (step 1.7) never called
``queue_message()`` even though it imported it. The text was a lie:
the message was *not* queued, the Matrix bot's ``_replay_offline_queue``
had nothing to replay, the user was ghosted.

Compare to the true-offline branch (step 1.8) at the same site, which
correctly calls ``queue_message(user_message, session_id)``. The two
parallel paths diverged; this test pins the parity.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from windyfly.agent import resurrect as _r


def _make_config():
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


@pytest.fixture
def stack():
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue
    db = Database(":memory:")
    save_episode(db, "user", "bootstrap", session_id="bootstrap")
    wq = WriteQueue(); wq.start()
    yield _make_config(), db, wq
    try: wq.stop()
    except Exception: pass
    db.close()


@pytest.fixture
def isolated_recovery_marker(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST",
                       str(tmp_path / ".recovery_probe_last"))
    yield tmp_path


@pytest.fixture
def isolated_queue(monkeypatch, tmp_path):
    """Per-test offline queue path so we don't read/write the prod queue."""
    qpath = tmp_path / "offline_queue.json"
    monkeypatch.setenv("WINDYFLY_OFFLINE_QUEUE", str(qpath))
    # offline.py reads _QUEUE_PATH at import time; rebind it.
    import windyfly.agent.offline as _off
    monkeypatch.setattr(_off, "_QUEUE_PATH", qpath)
    yield qpath


class TestLifeboatTimeoutQueuesMessage:
    """When the lifeboat itself fails (Ollama timeout), the resurrection
    short-circuit must call queue_message() to honor the user-visible
    "Your message is queued" promise."""

    def test_lifeboat_timeout_actually_queues(
        self, stack, monkeypatch, tmp_path,
        isolated_recovery_marker, isolated_queue,
    ):
        config, db, wq = stack
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))

        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")
        assert _r.is_resurrected() is True

        # Lifeboat returns the standard Ollama-timeout text (matches what
        # offline._call_ollama returns in its except branch).
        ollama_timeout_text = (
            "Local model error: timed out talking to my backup brain. "
            "Your message is queued; I'll try again when I'm healthier."
        )

        from windyfly.agent.loop import agent_respond
        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": False,
                                        "reason": "still_rate_limited"}), \
             patch("windyfly.agent.loop.get_offline_response",
                   return_value=ollama_timeout_text):
            reply = agent_respond(
                config, db, wq,
                "What is the Heisenberg uncertainty principle?",
                "session-stress-35",
            )

        # User-visible reply still has the lifeboat prefix + queued text
        assert reply.lstrip().startswith("🛟"), reply
        assert "queued" in reply.lower()

        # The actual fix: queue file now contains our message.
        assert isolated_queue.exists(), \
            "queue file should exist after lifeboat-timeout fallback"
        queued = json.loads(isolated_queue.read_text())
        assert any(
            entry.get("message", "").startswith(
                "What is the Heisenberg uncertainty principle?"
            )
            and entry.get("session_id") == "session-stress-35"
            for entry in queued
        ), f"message not queued — entries: {queued}"

    def test_lifeboat_success_does_not_queue(
        self, stack, monkeypatch, tmp_path,
        isolated_recovery_marker, isolated_queue,
    ):
        """When Ollama answers cleanly (no timeout marker in the
        response), we should NOT queue — the user got their reply
        already. Avoid queue-bloat from healthy lifeboat turns."""
        config, db, wq = stack
        flag = tmp_path / ".resurrected"
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(flag))

        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="test")

        from windyfly.agent.loop import agent_respond
        with patch.object(_r, "_paid_health_probe",
                          return_value={"ok": False,
                                        "reason": "still_rate_limited"}), \
             patch("windyfly.agent.loop.get_offline_response",
                   return_value="Photons are particles of light."):
            reply = agent_respond(config, db, wq,
                                  "What is a photon?", "session-2")

        assert "Photons" in reply
        # No queue file written (or written empty) since lifeboat answered.
        if isolated_queue.exists():
            queued = json.loads(isolated_queue.read_text())
            assert queued == [], \
                f"unexpected queue entries after healthy lifeboat: {queued}"
