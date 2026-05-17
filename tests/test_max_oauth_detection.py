"""Max-plan OAuth detection regression suite.

Surfaced 2026-05-17 while diagnosing windy-0:

  - Grant's env had the oat token in ``ANTHROPIC_API_KEY`` (the
    catch-all credential slot most installers default to).
  - ``_call_anthropic`` detected the ``sk-ant-oat01-`` prefix and
    routed to the OAuth client (Max-plan billing path) — correct.
  - ``_max_oauth_active()`` did NOT mirror that detection. It only
    checked ``OAuthManager`` (which reads
    ``ANTHROPIC_OAUTH_ACCESS_TOKEN``). So it returned False, and
    ``call_llm`` routed through Mind on every request.
  - Mind happens to be unreachable today → chain falls through to
    direct Anthropic call → Max plan billing survives by accident.
  - The moment Mind comes online with Anthropic models registered,
    Max-plan users start silently paying API03 rates.

This file pins down: the two detection paths
(``ANTHROPIC_OAUTH_ACCESS_TOKEN`` env var AND ``ANTHROPIC_API_KEY``
with oat prefix) must both flip ``_max_oauth_active()`` to True so
the Mind bypass stays consistent with the actual call site.
"""

from __future__ import annotations

import pytest

from windyfly.agent import models


@pytest.fixture(autouse=True)
def _reset_oauth_manager_singleton():
    """OAuthManager is a process-singleton; reset between tests so
    env-var changes are observed."""
    from windyfly.agent import oauth as oauth_mod
    saved = oauth_mod._manager
    oauth_mod._manager = None
    yield
    oauth_mod._manager = saved


@pytest.fixture(autouse=True)
def _reset_auth_path_log_flag():
    """``_log_anthropic_auth_path_once`` is gated by a module-level
    flag; reset it so each test observes a fresh first-call log."""
    models._ANTHROPIC_AUTH_PATH_LOGGED = False
    yield
    models._ANTHROPIC_AUTH_PATH_LOGGED = False


class TestMaxOAuthActive:
    """``_max_oauth_active()`` must return True for BOTH detection
    paths so Mind broker bypass is consistent with call-site behavior."""

    def test_returns_true_when_oauth_access_token_env_is_set(self, monkeypatch):
        monkeypatch.setenv(
            "ANTHROPIC_OAUTH_ACCESS_TOKEN", "sk-ant-oat01-PLACEHOLDER",
        )
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert models._max_oauth_active() is True

    def test_returns_true_when_api_key_holds_oat_prefix(self, monkeypatch):
        """The regression case — oat token stuffed into API_KEY slot."""
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-oat01-PLACEHOLDER")
        assert models._max_oauth_active() is True

    def test_returns_false_for_regular_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-PLACEHOLDER")
        assert models._max_oauth_active() is False

    def test_returns_false_for_empty_creds(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_OAUTH_ACCESS_TOKEN", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        assert models._max_oauth_active() is False

    def test_explicit_oauth_env_wins_over_api_key(self, monkeypatch):
        """If both are set with different prefixes, explicit OAuth env
        is the canonical source — same precedence as ``_call_anthropic``."""
        monkeypatch.setenv(
            "ANTHROPIC_OAUTH_ACCESS_TOKEN", "sk-ant-oat01-EXPLICIT",
        )
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-FALLBACK")
        assert models._max_oauth_active() is True


class TestAnthropicAuthPathLogging:
    """``_log_anthropic_auth_path_once`` must log exactly once per
    process and pick the right message based on which path is live."""

    def test_logs_oauth_manager_path(self, caplog):
        caplog.set_level("INFO", logger="windyfly.agent.models")
        models._log_anthropic_auth_path_once(
            oauth_via_manager=True,
            oauth_via_api_key=False,
            api_key_only=False,
        )
        msgs = [r.message for r in caplog.records]
        assert any("OAuth Max plan via OAuthManager" in m for m in msgs)

    def test_logs_api_key_fallback_path(self, caplog):
        caplog.set_level("INFO", logger="windyfly.agent.models")
        models._log_anthropic_auth_path_once(
            oauth_via_manager=False,
            oauth_via_api_key=True,
            api_key_only=False,
        )
        msgs = [r.message for r in caplog.records]
        assert any("via ANTHROPIC_API_KEY fallback" in m for m in msgs)
        # Should also include the hardening suggestion.
        assert any("ANTHROPIC_OAUTH_ACCESS_TOKEN" in m for m in msgs)

    def test_logs_pay_per_token_warning(self, caplog):
        caplog.set_level("WARNING", logger="windyfly.agent.models")
        models._log_anthropic_auth_path_once(
            oauth_via_manager=False,
            oauth_via_api_key=False,
            api_key_only=True,
        )
        msgs = [r.message for r in caplog.records]
        assert any("NOT on Max plan" in m for m in msgs)

    def test_logs_only_once_per_process(self, caplog):
        caplog.set_level("INFO", logger="windyfly.agent.models")
        models._log_anthropic_auth_path_once(
            oauth_via_manager=True,
            oauth_via_api_key=False,
            api_key_only=False,
        )
        models._log_anthropic_auth_path_once(
            oauth_via_manager=True,
            oauth_via_api_key=False,
            api_key_only=False,
        )
        models._log_anthropic_auth_path_once(
            oauth_via_manager=True,
            oauth_via_api_key=False,
            api_key_only=False,
        )
        oauth_msgs = [
            r for r in caplog.records
            if "OAuth Max plan via OAuthManager" in r.message
        ]
        assert len(oauth_msgs) == 1, (
            f"expected exactly 1 log; got {len(oauth_msgs)}. "
            "Per-call logging would spam journalctl."
        )

    def test_no_creds_path_warns(self, caplog):
        caplog.set_level("WARNING", logger="windyfly.agent.models")
        models._log_anthropic_auth_path_once(
            oauth_via_manager=False,
            oauth_via_api_key=False,
            api_key_only=False,
        )
        msgs = [r.message for r in caplog.records]
        assert any("NO credentials in env" in m for m in msgs)
