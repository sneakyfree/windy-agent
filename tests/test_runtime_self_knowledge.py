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
            "sk-ant-oat01-X",   # oauth fallback
            "sk-ant-api03-X",   # regular
            "",                  # none
        ]
        expected = {"kind", "label_short", "label_long"}
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
        """/status must include an ``Auth:`` line so Grant can verify
        which billing path is active without checking journalctl."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-PLACEHOLDER")
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("DEFAULT_MODEL", "claude-sonnet-4-6")
        monkeypatch.setenv("WINDYFLY_DB_PATH", "/nonexistent/path.db")

        # Boot the command registry and pull /status out.
        from windyfly.commands.registry import registry
        from windyfly.commands import core
        core.init_core()
        cmd = registry.get("status")
        assert cmd is not None, "/status command not registered"

        reply = await cmd.handler(ctx=None)
        assert "Auth:" in reply, f"no Auth: line in /status: {reply!r}"
        assert "OAuth Max" in reply, (
            f"/status didn't surface OAuth path for oat token: {reply!r}"
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
