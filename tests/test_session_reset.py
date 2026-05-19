"""Session-rolling /new regression suite (PR #193).

Pre-2026-05-19 ``/new`` returned a literal ``"NEW_SESSION"`` string
that no channel-layer code read. So:

  - ``_session_tokens[session_id]`` never reset → pct_remaining
    slowly dropped below 10% → LOW WORKING MEMORY block fired even
    on user-initiated fresh starts (the 2026-05-19 screenshot).
  - ``session_id = "telegram:{chat_id}"`` was stable across /new,
    so ``get_recent_episodes(session_id=...)`` kept returning the
    same prior turns and the model kept seeing the old conversation.

This file pins down the new contract:

  - ``next_session_id`` returns ``"{platform}:{channel_id}:v0"`` by
    default and reflects the latest counter on subsequent calls.
  - ``reset_session`` increments, persists, clears the OLD
    ``_session_tokens`` entry, and returns the NEW id.
  - State survives process restart via JSON persistence.
  - Path override env var is respected (so tests don't write to
    the user's home dir).
"""

from __future__ import annotations

import json

import pytest

from windyfly.agent.session_reset import (
    next_session_id,
    reset_session,
    _reset_module_state_for_tests,
)


@pytest.fixture
def tmp_counter_path(tmp_path, monkeypatch):
    """Redirect the counter file to a tmpdir for the duration of a
    test so we don't touch the user's real ~/.windy directory."""
    p = tmp_path / "session-counters.json"
    monkeypatch.setenv("WINDYFLY_SESSION_COUNTER_PATH", str(p))
    _reset_module_state_for_tests()
    yield p
    _reset_module_state_for_tests()


class TestNextSessionId:

    def test_returns_v0_by_default(self, tmp_counter_path):
        assert next_session_id("telegram", "8545546994") == \
            "telegram:8545546994:v0"

    def test_is_idempotent(self, tmp_counter_path):
        """Calling next_session_id multiple times must NOT bump —
        only reset_session bumps. Channels call it on every message."""
        a = next_session_id("telegram", "12345")
        b = next_session_id("telegram", "12345")
        c = next_session_id("telegram", "12345")
        assert a == b == c

    def test_different_channels_isolated(self, tmp_counter_path):
        a = next_session_id("telegram", "1")
        b = next_session_id("telegram", "2")
        c = next_session_id("discord", "1")
        assert a == "telegram:1:v0"
        assert b == "telegram:2:v0"
        assert c == "discord:1:v0"


class TestResetSession:

    def test_increments_counter(self, tmp_counter_path):
        assert next_session_id("telegram", "1") == "telegram:1:v0"
        reset_session("telegram", "1")
        assert next_session_id("telegram", "1") == "telegram:1:v1"
        reset_session("telegram", "1")
        assert next_session_id("telegram", "1") == "telegram:1:v2"

    def test_returns_new_session_id(self, tmp_counter_path):
        new_id = reset_session("telegram", "42")
        assert new_id == "telegram:42:v1"

    def test_clears_old_session_token_counter(self, tmp_counter_path):
        """The OLD session_id's _session_tokens entry must be cleared
        so the dict doesn't accumulate dead entries over many /news
        in a long-running process."""
        from windyfly.agent.loop import _session_tokens
        _session_tokens["telegram:99:v0"] = 150_000
        reset_session("telegram", "99")
        assert "telegram:99:v0" not in _session_tokens, (
            "old session_id should be removed from _session_tokens "
            "after reset"
        )

    def test_does_not_affect_other_channels(self, tmp_counter_path):
        reset_session("telegram", "1")
        # channel 2 still on v0
        assert next_session_id("telegram", "2") == "telegram:2:v0"
        # discord:1 still on v0
        assert next_session_id("discord", "1") == "discord:1:v0"

    def test_concurrent_resets_dont_lose_increments(self, tmp_counter_path):
        """Lock must serialize resets so two concurrent /new calls
        from different threads can't both read counter=N, both
        increment locally, both write N+1 — losing one increment."""
        import threading
        for _ in range(50):
            t1 = threading.Thread(target=lambda: reset_session("t", "x"))
            t2 = threading.Thread(target=lambda: reset_session("t", "x"))
            t1.start()
            t2.start()
            t1.join()
            t2.join()
        # 50 iterations * 2 resets each = 100 increments
        assert next_session_id("t", "x") == "t:x:v100"


class TestPersistence:

    def test_counter_survives_module_reload(self, tmp_counter_path):
        """A process restart re-reads the file — counters must
        persist or /new gets undone every time the bot restarts."""
        reset_session("telegram", "1")
        reset_session("telegram", "1")
        # Simulate process restart by clearing in-memory state
        _reset_module_state_for_tests()
        # Counter should restore from disk
        assert next_session_id("telegram", "1") == "telegram:1:v2"

    def test_corrupt_json_starts_empty(self, tmp_counter_path):
        tmp_counter_path.write_text("not valid json {")
        _reset_module_state_for_tests()
        # Should NOT raise — falls back to empty, /new still works
        assert next_session_id("telegram", "1") == "telegram:1:v0"

    def test_missing_file_starts_empty(self, tmp_counter_path):
        """No file yet = fresh state. /new creates the file."""
        if tmp_counter_path.exists():
            tmp_counter_path.unlink()
        _reset_module_state_for_tests()
        assert next_session_id("telegram", "1") == "telegram:1:v0"
        reset_session("telegram", "1")
        assert tmp_counter_path.exists(), (
            "reset_session should create the persistence file"
        )

    def test_persisted_data_is_valid_json(self, tmp_counter_path):
        reset_session("telegram", "1")
        reset_session("discord", "abc")
        data = json.loads(tmp_counter_path.read_text())
        assert isinstance(data, dict)
        # PR #197: schema bumped from plain int to dict-per-channel.
        # Both shapes still load (see test_legacy_int_format_loads
        # below) but new writes always emit the dict shape.
        assert data["telegram:1"] == {
            "reset_count": 1, "model": None, "memory_cap": None,
        }
        assert data["discord:abc"] == {
            "reset_count": 1, "model": None, "memory_cap": None,
        }

    def test_legacy_int_format_loads(self, tmp_counter_path):
        """Files written by PR #193 used a plain-int counter per
        channel. PR #197 must auto-upgrade those entries to the
        dict shape — never break an existing deployment."""
        tmp_counter_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_counter_path.write_text(
            json.dumps({"telegram:legacy": 5, "discord:old": 2})
        )
        _reset_module_state_for_tests()
        # Reset count is recovered from the int values
        assert next_session_id("telegram", "legacy") == \
            "telegram:legacy:v5"
        assert next_session_id("discord", "old") == "discord:old:v2"


class TestCmdNewIntegration:
    """End-to-end: /new from a command-context dict actually rolls
    the session and clears tokens."""

    @pytest.mark.asyncio
    async def test_cmd_new_with_full_context_resets(
        self, tmp_counter_path,
    ):
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        from windyfly.agent.loop import _session_tokens

        core.init_core()
        cmd = registry.get("new")
        assert cmd is not None

        # Pre-state: prior session has token usage
        _session_tokens["telegram:777:v0"] = 180_000

        reply = await cmd.handler({"platform": "telegram",
                                    "channel_id": "777"})

        # Counter should have bumped
        assert next_session_id("telegram", "777") == "telegram:777:v1"
        # Old token counter cleared
        assert "telegram:777:v0" not in _session_tokens
        # Reply must NOT be the old "NEW_SESSION" sentinel
        assert reply != "NEW_SESSION"
        assert "fresh" in reply.lower() or "clean" in reply.lower()

    @pytest.mark.asyncio
    async def test_cmd_new_without_channel_id_gracefully_errors(
        self, tmp_counter_path,
    ):
        """If channel_id is missing from ctx, /new must NOT crash —
        return an explanatory message instead so the operator knows
        the channel layer didn't plumb through."""
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("new")
        reply = await cmd.handler({"platform": "telegram"})
        assert "channel_id" in reply.lower() or "couldn't" in reply.lower()
        # Counter must NOT bump on the error path
        assert next_session_id("telegram", "anything") == \
            "telegram:anything:v0"
