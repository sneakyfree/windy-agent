"""Runtime self-knowledge / positive truth injection (PR #192).

Companion to the HOST tripwire in PR #190/191. The tripwire catches
specific surface forms of confabulation post-hoc; this layer prevents
the confabulation in the first place by giving the model positive
facts it can quote when asked about itself.

Three surfaces under test:

  1. ``get_anthropic_auth_path()`` returns labeled facts about which
     auth path is live — readable by /status, the prompt, and any
     future operator-facing surface.
  2. ``/status`` command output includes an ``Auth:`` line.
  3. ``assemble_prompt`` emits a RUNTIME CONTEXT block with model,
     auth path, and process supervisor — so the model has truth to
     anchor to instead of hedge-confabulating.

Driven by the 2026-05-18 Telegram screenshot: bot was asked "which
model are you?" and replied "I can't tell you exactly... run that on
your VPS" — even though ``/status`` two messages earlier returned the
exact model. The bot HAD the answer; it just wasn't surfaced anywhere
the model could see.
"""

from __future__ import annotations

import pytest

from windyfly.agent import models


@pytest.fixture(autouse=True)
def _reset_oauth_manager_singleton():
    from windyfly.agent import oauth as oauth_mod
    saved = oauth_mod._manager
    oauth_mod._manager = None
    yield
    oauth_mod._manager = saved


class TestFingerprintAndContextCap:

    def test_fingerprint_oat_token(self):
        token = (
            "sk-ant-oat01-VwUdywrPUNW2MlOu4FNOPGhg3P3-hc6z-z8wpl"
            "HFQkOgj9lEJkRXWwPvP-vsHmIFe-AE7_Klesjco3QTWjIP1A-hUMCwAAA"
        )
        fp = models._fingerprint_token(token)
        # Shape: first 15 + … + last 4
        assert fp.startswith("sk-ant-oat01-Vw")
        assert "…" in fp
        assert fp.endswith("wAAA")
        # Crucially: the BODY of the token is NOT in the fingerprint
        assert "UdywrPUNW2MlOu" not in fp

    def test_fingerprint_empty(self):
        assert models._fingerprint_token("") == "(empty)"
        assert models._fingerprint_token("short") == "(empty)"

    def test_context_cap_known_models(self):
        assert models.get_context_cap("claude-opus-4-7") == 1_000_000
        assert models.get_context_cap("claude-sonnet-4-6") == 200_000
        assert models.get_context_cap("claude-haiku-4-5") == 200_000
        assert models.get_context_cap("gpt-4o") == 128_000
        assert models.get_context_cap("llama3.2:3b") == 8_192

    def test_context_cap_heuristic_fallbacks(self):
        # Unknown opus variant — heuristic should still return 1M
        assert models.get_context_cap("claude-opus-future-x") == 1_000_000
        # Unknown sonnet variant — 200K
        assert models.get_context_cap("claude-sonnet-future") == 200_000
        # Unknown haiku variant
        assert models.get_context_cap("claude-haiku-future") == 200_000
        # Garbage model name — conservative 8K small-model default
        assert models.get_context_cap("foo-bar-baz") == 8_192

    def test_context_cap_empty_model(self):
        # Empty string is treated as "unknown" but with the more
        # generous 200K default since most production paths won't
        # pass empty.
        assert models.get_context_cap("") == 200_000


class TestGetAnthropicAuthPath:

    def test_oauth_via_manager(self, monkeypatch):
        monkeypatch.setenv(
            "ANTHROPIC_OAUTH_ACCESS_TOKEN", "sk-ant-oat01-PLACEHOLDER",
        )
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = models.get_anthropic_auth_path()
        assert result["kind"] == "oauth_manager"
        assert "OAuth Max" in result["label_short"]
        assert "subscription billing" in result["label_long"]
        # PR #195 — fingerprint now part of the return shape
        assert "fingerprint" in result

    def test_oauth_via_api_key_fallback(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-PLACEHOLDER")
        result = models.get_anthropic_auth_path()
        assert result["kind"] == "oauth_api_key"
        assert "via API_KEY" in result["label_short"]
        assert "subscription billing" in result["label_long"]

    def test_regular_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-PLACEHOLDER")
        result = models.get_anthropic_auth_path()
        assert result["kind"] == "api_key"
        assert "pay-per-token" in result["label_short"]

    def test_no_creds(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        result = models.get_anthropic_auth_path()
        assert result["kind"] == "none"
        assert "no" in result["label_short"].lower()

    def test_keys_are_stable_shape(self, monkeypatch):
        """All four paths must return dicts with the same key set —
        callers shouldn't have to None-check fields."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        cases = [
            "sk-ant-oat01-VwUdywrPUNW2MlOu4FNOPGhg3P3-hc6z-z8wplHFQkOgj9lEJkRXWwPvP",
            "sk-ant-api03-LongEnoughTokenStringForFingerprintXX",
            "",
        ]
        expected = {"kind", "label_short", "label_long", "fingerprint"}
        for v in cases:
            if v:
                monkeypatch.setenv("ANTHROPIC_API_KEY", v)
            else:
                monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
            assert set(models.get_anthropic_auth_path().keys()) == expected, \
                f"shape mismatch for ANTHROPIC_API_KEY={v!r}"


class TestStatusCommandIncludesAuth:

    @pytest.mark.asyncio
    async def test_status_shows_auth_line(self, monkeypatch):
        """/status must surface the OAuth path label AND a redacted
        token fingerprint so the operator can identify which key is
        live at a glance (PR #195)."""
        monkeypatch.setenv(
            "ANTHROPIC_API_KEY",
            "sk-ant-oat01-VwUdywrPUNW2MlOu4FNOPGhg3P3-hc6z-z8wplHFQk",
        )
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")

        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")
        assert cmd is not None, "/status command not registered"

        reply = await cmd.handler(ctx=None)
        # PR #195 — emoji header on the plan line
        assert "💳 Plan:" in reply or "Plan:" in reply, (
            f"no Plan: line in /status: {reply!r}"
        )
        assert "OAuth Max" in reply, (
            f"/status didn't surface OAuth path for oat token: {reply!r}"
        )
        # PR #195 — fingerprint visible (truncated, body redacted)
        assert "sk-ant-oat01-Vw" in reply, (
            f"/status didn't show OAuth fingerprint: {reply!r}"
        )
        # The body of the token MUST NOT leak
        assert "UdywrPUNW2MlOu" not in reply, (
            f"/status leaked token body: {reply!r}"
        )

    @pytest.mark.asyncio
    async def test_status_shows_pay_per_token_warning(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-X")
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")
        reply = await cmd.handler(ctx=None)
        assert "pay-per-token" in reply, (
            f"/status didn't surface pay-per-token warning: {reply!r}"
        )

    @pytest.mark.asyncio
    async def test_status_includes_memory_session_uptime(
        self, monkeypatch, tmp_path,
    ):
        """PR #195 grandma-friendly /status: when called with platform
        + channel_id in ctx, /status shows Health, plain-English
        Memory, Brain w/context cap, Plan w/fingerprint, Session,
        Uptime."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv(
            "ANTHROPIC_API_KEY",
            "sk-ant-oat01-VwUdywrPUNW2MlOu4FNOPGhg3P3-hc6z-z8wplHFQk",
        )
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")
        monkeypatch.setenv(
            "WINDYFLY_SESSION_COUNTER_PATH",
            str(tmp_path / "counters.json"),
        )
        from windyfly.agent.session_reset import (
            _reset_module_state_for_tests,
        )
        _reset_module_state_for_tests()

        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")

        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1234",
        })

        # Health
        assert "Health:" in reply
        assert "🟢" in reply  # not degraded
        # Memory (plain-English descriptor + emoji)
        assert "🧠 Memory:" in reply
        assert "feels fresh" in reply.lower() or "free" in reply.lower()
        # Brain (model + per-model context cap)
        assert "🤖 Brain:" in reply
        assert "claude-sonnet-4-6" in reply
        assert "200K context" in reply, (
            f"Sonnet 4.6 should report 200K cap: {reply!r}"
        )
        # Plan (auth + fingerprint)
        assert "💳 Plan:" in reply
        assert "sk-ant-oat01-Vw" in reply
        # Session
        assert "Session: telegram:1234:v0" in reply
        assert "0 fresh starts" in reply
        # Uptime
        assert "Up:" in reply

    @pytest.mark.asyncio
    async def test_status_opus_model_reports_1M_context(
        self, monkeypatch, tmp_path,
    ):
        """The brain-line context cap must scale per model — Opus 4.7
        gets 1M; Sonnet/Haiku stay at 200K. Pre-fix /status hardcoded
        200K and Grant called it out 2026-05-19."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-VwLong" * 4)
        monkeypatch.setenv("DEFAULT_MODEL", "claude-opus-4-7")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")
        monkeypatch.setenv(
            "WINDYFLY_SESSION_COUNTER_PATH",
            str(tmp_path / "counters.json"),
        )
        from windyfly.agent.session_reset import _reset_module_state_for_tests
        _reset_module_state_for_tests()
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "9999",
        })
        assert "1M context" in reply, (
            f"Opus 4.7 should report 1M context cap: {reply!r}"
        )

    @pytest.mark.asyncio
    async def test_status_memory_band_low_says_type_new(
        self, monkeypatch, tmp_path,
    ):
        """When context is < 10% remaining the Memory line must tell
        the user to /new — grandma needs a hint, not a riddle."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-VwLong" * 4)
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")
        monkeypatch.setenv(
            "WINDYFLY_SESSION_COUNTER_PATH",
            str(tmp_path / "counters.json"),
        )
        from windyfly.agent.session_reset import _reset_module_state_for_tests
        from windyfly.agent.loop import _session_tokens
        _reset_module_state_for_tests()
        _session_tokens["telegram:lowmem:v0"] = 190_000  # 5% rem
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")
        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "lowmem",
        })
        assert "/new" in reply, (
            f"low-memory state should suggest /new: {reply!r}"
        )
        assert "🔴" in reply or "nearly full" in reply

    @pytest.mark.asyncio
    async def test_status_session_line_reflects_resets(
        self, monkeypatch, tmp_path,
    ):
        """After /new bumps the counter, /status must show the new
        session_id and reset count — confirms PR #193 + #194 wiring
        agrees end-to-end."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-X")
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")
        monkeypatch.setenv(
            "WINDYFLY_SESSION_COUNTER_PATH",
            str(tmp_path / "counters.json"),
        )
        from windyfly.agent.session_reset import (
            _reset_module_state_for_tests, reset_session,
        )
        _reset_module_state_for_tests()
        reset_session("telegram", "1234")
        reset_session("telegram", "1234")
        reset_session("telegram", "1234")

        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")

        reply = await cmd.handler({
            "platform": "telegram", "channel_id": "1234",
        })

        assert "Session: telegram:1234:v3" in reply
        assert "3 fresh starts" in reply

    @pytest.mark.asyncio
    async def test_status_without_channel_id_omits_memory_lines(
        self, monkeypatch,
    ):
        """CLI / pulse invocations without channel context still get
        the rest of /status — Memory + Session lines just drop."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-VwLong" * 4)
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")

        reply = await cmd.handler(None)

        assert "Memory:" not in reply or "Memory file:" in reply
        assert "Session:" not in reply
        # But the rest still appears
        assert "🤖 Brain:" in reply
        assert "💳 Plan:" in reply
        assert "Memory file:" in reply


class TestRuntimeContextInPrompt:

    def _make_config(self):
        return {
            "agent": {"default_model": "claude-sonnet-4-6",
                      "max_context_tokens": 8000,
                      "max_response_tokens": 2000, "temperature": 0.7},
            "memory": {"db_path": ":memory:",
                       "max_episodes_per_context": 20,
                       "max_nodes_per_context": 10},
            "personality": {"soul_path": "SOUL.md", "humor_level": 7,
                            "formality": 4, "proactivity": 5,
                            "verbosity": 5, "reasoning_depth": 6,
                            "autonomy": 3, "epistemic_strictness": 5},
            "costs": {"daily_budget_usd": 5.0, "warn_at_usd": 3.0},
        }

    @pytest.fixture
    def db(self):
        from windyfly.memory.database import Database
        from windyfly.memory.episodes import save_episode
        db = Database(":memory:")
        save_episode(db, "user", "bootstrap", session_id="bootstrap")
        yield db
        db.close()

    def test_runtime_context_block_present(self, db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-X")
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(self._make_config(), db, "hi", "s-1")
        sys_text = msgs[0]["content"]
        assert "RUNTIME CONTEXT" in sys_text

    def test_runtime_context_includes_model(self, db, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(self._make_config(), db, "hi", "s-2")
        sys_text = msgs[0]["content"]
        # Configured model must appear in RUNTIME CONTEXT so the bot
        # can quote it instead of confabulating "I can't tell you".
        assert "claude-sonnet-4-6" in sys_text

    def test_runtime_context_includes_oauth_when_active(self, db, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-X")
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(self._make_config(), db, "hi", "s-3")
        sys_text = msgs[0]["content"]
        # The bot must have the auth path as a quotable fact.
        assert "OAuth Max plan" in sys_text
        assert "subscription billing" in sys_text

    def test_runtime_context_includes_pay_per_token_when_active(
        self, db, monkeypatch,
    ):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-X")
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(self._make_config(), db, "hi", "s-4")
        sys_text = msgs[0]["content"]
        assert "pay-per-token" in sys_text

    def test_runtime_context_includes_process_supervisor(
        self, db, monkeypatch,
    ):
        """Supervisor detection must run and produce SOME label. The
        specific label (systemd / docker / k8s / lambda) depends on
        runtime env, but the line should always be present."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(self._make_config(), db, "hi", "s-5")
        sys_text = msgs[0]["content"]
        assert "Process:" in sys_text
        # Whatever supervisor was detected, the line must include the
        # explicit anti-VPS-default reminder so the model doesn't
        # round-trip back to "your VPS" the next morning.
        assert "NOT a remote VPS" in sys_text

    def test_runtime_context_directs_model_to_quote_facts(
        self, db, monkeypatch,
    ):
        """The closing line must tell the model to QUOTE the facts
        instead of hedging — otherwise the model still says 'I can't
        tell you exactly' even with the facts in context."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        from windyfly.agent.prompt import assemble_prompt
        msgs = assemble_prompt(self._make_config(), db, "hi", "s-6")
        sys_text = msgs[0]["content"]
        assert "QUOTE" in sys_text
        assert "Do not hedge" in sys_text
