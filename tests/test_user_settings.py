"""User settings (model + memory) — PR #197.

Covers the new ``/model`` and ``/memory`` commands plus the
foundations they sit on:

  - ``models_catalog`` — alias resolution, native/extended caps,
    conflict-resolution matrix.
  - ``session_reset`` extensions — get_model / set_model /
    get_memory_cap / set_memory_cap on the same per-channel store
    that PR #193 introduced for the rolling reset counter.
  - Backward compat — old plain-int counter files still load and
    auto-upgrade to the new dict schema.

Design choice this file pins down (per 2026-05-19 conversation
with Grant): ``/model opus`` takes effect on the user's NEXT
message; it does NOT force a /new. If they want a clean break too,
they use /new explicitly afterward.
"""

from __future__ import annotations

import pytest

from windyfly.agent import models_catalog


@pytest.fixture
def tmp_state_path(tmp_path, monkeypatch):
    """Redirect session_reset persistence to a tmpdir."""
    monkeypatch.setenv(
        "WINDYFLY_SESSION_COUNTER_PATH",
        str(tmp_path / "session-counters.json"),
    )
    from windyfly.agent.session_reset import _reset_module_state_for_tests
    _reset_module_state_for_tests()
    yield tmp_path / "session-counters.json"
    _reset_module_state_for_tests()


class TestModelsCatalog:

    def test_known_models_present(self):
        ids = [m.id for m in models_catalog.list_models()]
        assert "claude-opus-4-7" in ids
        assert "claude-sonnet-4-6" in ids
        assert "claude-haiku-4-5" in ids

    def test_resolve_canonical_id(self):
        assert models_catalog.resolve("claude-opus-4-7").id == \
            "claude-opus-4-7"
        assert models_catalog.resolve("claude-sonnet-4-6").id == \
            "claude-sonnet-4-6"

    def test_resolve_short_alias(self):
        assert models_catalog.resolve("opus").id == "claude-opus-4-7"
        assert models_catalog.resolve("sonnet").id == "claude-sonnet-4-6"
        assert models_catalog.resolve("haiku").id == "claude-haiku-4-5"

    def test_resolve_friendly_alias(self):
        """Grandma-friendly names — pick by intent, not technical id."""
        assert models_catalog.resolve("smartest").id == "claude-opus-4-7"
        assert models_catalog.resolve("balanced").id == "claude-sonnet-4-6"
        assert models_catalog.resolve("fastest").id == "claude-haiku-4-5"

    def test_resolve_case_insensitive(self):
        assert models_catalog.resolve("OPUS").id == "claude-opus-4-7"
        assert models_catalog.resolve(" Sonnet ").id == "claude-sonnet-4-6"

    def test_resolve_dated_variant(self):
        """Anthropic ships dated model ids (claude-sonnet-4-6-20251022).
        Resolve should match the family entry so /status, billing, and
        beta-header logic agree regardless of which exact dated id the
        config has."""
        result = models_catalog.resolve("claude-sonnet-4-6-20251022")
        assert result is not None and result.id == "claude-sonnet-4-6"

    def test_resolve_unknown_returns_none(self):
        assert models_catalog.resolve("definitely-not-a-model") is None
        assert models_catalog.resolve("") is None
        assert models_catalog.resolve("   ") is None

    def test_supports_cap_within_native(self):
        """Smaller caps just work — no beta needed."""
        ok, beta = models_catalog.supports_cap("claude-opus-4-7", 200_000)
        assert ok is True
        assert beta is None

    def test_supports_cap_native_opus(self):
        """Opus is 1M natively — no beta needed."""
        ok, beta = models_catalog.supports_cap("claude-opus-4-7", 1_000_000)
        assert ok is True
        assert beta is None

    def test_supports_cap_extended_sonnet(self):
        """1M on Sonnet requires the context-1m beta header."""
        ok, beta = models_catalog.supports_cap("claude-sonnet-4-6", 1_000_000)
        assert ok is True
        assert beta == "context-1m-2025-08-07"

    def test_supports_cap_extended_haiku(self):
        ok, beta = models_catalog.supports_cap("claude-haiku-4-5", 1_000_000)
        assert ok is True
        assert beta == "context-1m-2025-08-07"

    def test_supports_cap_over_extended(self):
        """2M is past everyone's extended limit — refuse."""
        ok, beta = models_catalog.supports_cap("claude-sonnet-4-6", 2_000_000)
        assert ok is False
        assert beta is None

    def test_supports_cap_unknown_model_conservative(self):
        """Unknown model — be conservative; only allow up to 200K."""
        ok, _ = models_catalog.supports_cap("foo-bar", 200_000)
        assert ok is True
        ok, _ = models_catalog.supports_cap("foo-bar", 500_000)
        assert ok is False

    def test_format_cap(self):
        assert models_catalog.format_cap(200_000) == "200K"
        assert models_catalog.format_cap(1_000_000) == "1M"
        assert models_catalog.format_cap(500_000) == "500K"
        assert models_catalog.format_cap(8_192) == "8K"

    def test_parse_cap_variants(self):
        assert models_catalog.parse_cap("1M") == 1_000_000
        assert models_catalog.parse_cap("1m") == 1_000_000
        assert models_catalog.parse_cap("200K") == 200_000
        assert models_catalog.parse_cap("200k") == 200_000
        assert models_catalog.parse_cap("500_000") == 500_000
        assert models_catalog.parse_cap("1,000,000") == 1_000_000
        assert models_catalog.parse_cap("  500K  ") == 500_000

    def test_parse_cap_rejects_garbage(self):
        assert models_catalog.parse_cap("") is None
        assert models_catalog.parse_cap("a million") is None
        assert models_catalog.parse_cap("-100") is None
        assert models_catalog.parse_cap("0") is None


class TestSessionResetSettingsAccessors:
    """The session_reset module gained per-channel model + memory_cap
    fields on top of its existing reset-counter responsibility."""

    def test_get_model_default_none(self, tmp_state_path):
        from windyfly.agent.session_reset import get_model
        assert get_model("telegram", "x") is None

    def test_set_get_model(self, tmp_state_path):
        from windyfly.agent.session_reset import set_model, get_model
        set_model("telegram", "x", "claude-opus-4-7")
        assert get_model("telegram", "x") == "claude-opus-4-7"

    def test_set_model_to_none_clears(self, tmp_state_path):
        from windyfly.agent.session_reset import set_model, get_model
        set_model("telegram", "x", "claude-opus-4-7")
        set_model("telegram", "x", None)
        assert get_model("telegram", "x") is None

    def test_set_get_memory_cap(self, tmp_state_path):
        from windyfly.agent.session_reset import (
            set_memory_cap, get_memory_cap,
        )
        set_memory_cap("telegram", "x", 1_000_000)
        assert get_memory_cap("telegram", "x") == 1_000_000

    def test_settings_survive_reset(self, tmp_state_path):
        """A /new must NOT wipe the user's model / memory picks —
        those are settings, not session-scoped state."""
        from windyfly.agent.session_reset import (
            set_model, set_memory_cap, reset_session,
            get_model, get_memory_cap,
        )
        set_model("telegram", "x", "claude-opus-4-7")
        set_memory_cap("telegram", "x", 1_000_000)
        reset_session("telegram", "x")
        assert get_model("telegram", "x") == "claude-opus-4-7"
        assert get_memory_cap("telegram", "x") == 1_000_000

    def test_parse_session_id(self):
        from windyfly.agent.session_reset import parse_session_id
        assert parse_session_id("telegram:8545546994:v3") == \
            ("telegram", "8545546994", 3)
        assert parse_session_id("telegram:8545546994") == \
            ("telegram", "8545546994", 0)
        assert parse_session_id("") == ("", "", 0)


class TestCmdModel:

    @pytest.mark.asyncio
    async def test_cmd_model_no_arg_shows_listing(self, tmp_state_path):
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("model")
        assert cmd is not None
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1", "_raw": "",
        })
        assert "claude-opus-4-7" in reply
        assert "claude-sonnet-4-6" in reply
        assert "claude-haiku-4-5" in reply
        assert "/model" in reply  # invites the user to switch

    @pytest.mark.asyncio
    async def test_cmd_model_switch_by_alias(self, tmp_state_path):
        from windyfly.agent.session_reset import get_model
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("model")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "opus",
        })
        assert "claude-opus-4-7" in reply
        assert "next message" in reply.lower()
        # Persisted
        assert get_model("telegram", "1") == "claude-opus-4-7"

    @pytest.mark.asyncio
    async def test_cmd_model_switch_by_canonical_id(self, tmp_state_path):
        from windyfly.agent.session_reset import get_model
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("model")
        await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "claude-haiku-4-5",
        })
        assert get_model("telegram", "1") == "claude-haiku-4-5"

    @pytest.mark.asyncio
    async def test_cmd_model_unknown_returns_friendly_error(
        self, tmp_state_path,
    ):
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("model")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "gpt-5",
        })
        assert "don't know" in reply.lower() or "unknown" in reply.lower()

    @pytest.mark.asyncio
    async def test_cmd_model_warns_on_pinned_cap_incompatibility(
        self, tmp_state_path,
    ):
        """If user has /memory 1M and then /model switches to a model
        that can't even hit 1M via beta, /model surfaces the conflict
        immediately."""
        from windyfly.agent.session_reset import set_memory_cap
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        # Pin 1M, then switch to a fictional small model — not in
        # catalog. The test instead pins a cap > opus' 1M to force a
        # conflict against any catalog model.
        set_memory_cap("telegram", "1", 5_000_000)
        cmd = registry.get("model")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "opus",
        })
        assert "clamp" in reply.lower() or "tops out" in reply.lower() \
            or "memory" in reply.lower()


class TestCmdMemory:

    @pytest.mark.asyncio
    async def test_cmd_memory_no_arg_shows_current(self, tmp_state_path):
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("memory")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1", "_raw": "",
        })
        assert "memory" in reply.lower()
        assert "set with" in reply.lower() or "/memory" in reply.lower()

    @pytest.mark.asyncio
    async def test_cmd_memory_set_within_native(self, tmp_state_path):
        from windyfly.agent.session_reset import (
            set_model, get_memory_cap,
        )
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        # On Opus (1M native) — 500K is well within native
        set_model("telegram", "1", "claude-opus-4-7")
        cmd = registry.get("memory")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "500K",
        })
        assert "500K" in reply
        assert get_memory_cap("telegram", "1") == 500_000

    @pytest.mark.asyncio
    async def test_cmd_memory_set_engages_extended_tier(
        self, tmp_state_path,
    ):
        """1M on Sonnet requires the extended beta — the bot must
        confirm that's been engaged."""
        from windyfly.agent.session_reset import (
            set_model, get_memory_cap,
        )
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        set_model("telegram", "1", "claude-sonnet-4-6")
        cmd = registry.get("memory")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "1M",
        })
        assert "1M" in reply
        assert "extended" in reply.lower()
        assert get_memory_cap("telegram", "1") == 1_000_000

    @pytest.mark.asyncio
    async def test_cmd_memory_refuses_when_model_cant_deliver(
        self, tmp_state_path,
    ):
        """5M is past everyone's max — must refuse and suggest /model."""
        from windyfly.agent.session_reset import (
            set_model, get_memory_cap,
        )
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        set_model("telegram", "1", "claude-sonnet-4-6")
        cmd = registry.get("memory")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "5M",
        })
        assert "can't" in reply.lower() or "max" in reply.lower()
        # Persistence must NOT change on refusal
        assert get_memory_cap("telegram", "1") is None

    @pytest.mark.asyncio
    async def test_cmd_memory_default_clears_pinned_cap(
        self, tmp_state_path,
    ):
        from windyfly.agent.session_reset import (
            set_memory_cap, get_memory_cap,
        )
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        set_memory_cap("telegram", "1", 1_000_000)
        cmd = registry.get("memory")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "default",
        })
        assert "cleared" in reply.lower() or "default" in reply.lower()
        assert get_memory_cap("telegram", "1") is None

    @pytest.mark.asyncio
    async def test_cmd_memory_rejects_garbage(self, tmp_state_path):
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("memory")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1",
            "_raw": "a lot",
        })
        assert "couldn't parse" in reply.lower() or \
            "couldn't" in reply.lower() or "parse" in reply.lower()
