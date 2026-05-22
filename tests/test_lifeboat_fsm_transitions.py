"""Phase 2.2.3 — FSM transition tests at the observability layer.

The as-built doc enumerated 19 transitions (T1-T19). Each test here
exercises ONE transition: arrange the "from" state, fire the trigger,
assert the FSM observable transitioned to the expected "to" state.

These do not test behavior change (the full FSM-enforcement refactor
is still gated on Grant's design Qs Q3+Q5). They test that the
observability layer correctly reports state across each transition.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from windyfly.agent import resurrect as _r
from windyfly.agent.resurrect import LifeboatState, current_state


@pytest.fixture(autouse=True)
def _clean_provider_cooldowns():
    """Each test starts with a clean cooldown dict; clears after too."""
    from windyfly.agent.models import _provider_cooldowns
    _provider_cooldowns.clear()
    yield
    _provider_cooldowns.clear()


def _isolate_flags(monkeypatch, tmp_path):
    """Point all flag files at tmp_path so tests don't touch real state."""
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"))


class TestT1_ManualResurrect:
    """HEALTHY → LIFEBOAT via /resurrect"""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        assert current_state() == LifeboatState.HEALTHY
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        assert current_state() == LifeboatState.LIFEBOAT


class TestT4_ProviderFailure:
    """HEALTHY → DEGRADED via short-cooldown provider failure"""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        assert current_state() == LifeboatState.HEALTHY
        from windyfly.agent.models import _provider_cooldowns
        # Short cooldown (60s remaining = DEGRADED per current_state precedence)
        _provider_cooldowns["anthropic"] = (time.time() + 60, 1)
        assert current_state() == LifeboatState.DEGRADED


class TestT5_PermaAuthLongCooldown:
    """HEALTHY → AUTH_DEAD via long-cooldown perma-auth failure"""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        assert current_state() == LifeboatState.HEALTHY
        from windyfly.agent.models import _provider_cooldowns
        # >15min remaining = AUTH_DEAD per #210 long-bucket
        _provider_cooldowns["anthropic"] = (time.time() + 1800, 1)
        assert current_state() == LifeboatState.AUTH_DEAD


class TestT6_DegradedClearsOnSuccess:
    """DEGRADED → HEALTHY when cooldown clears."""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns["anthropic"] = (time.time() + 60, 1)
        assert current_state() == LifeboatState.DEGRADED
        _provider_cooldowns.clear()
        assert current_state() == LifeboatState.HEALTHY


class TestT8_RecoverySuccessMarksGrace:
    """LIFEBOAT (with successful probe) → RECOVERING (grace window)."""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        # Force lifeboat on
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        assert current_state() == LifeboatState.LIFEBOAT
        # Clear flag (simulates probe success); mark grace
        _r.normalize()
        _r._mark_post_recovery()
        assert current_state() == LifeboatState.RECOVERING


class TestT12_NormalCommandClearsLifeboat:
    """LIFEBOAT → HEALTHY via /normal."""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        assert current_state() == LifeboatState.LIFEBOAT
        _r.normalize()
        assert current_state() == LifeboatState.HEALTHY


class TestT14_GraceExpiry:
    """RECOVERING → HEALTHY when grace marker expires (via env override)."""

    def test_transition(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        # Stamp grace
        _r._mark_post_recovery()
        assert current_state() == LifeboatState.RECOVERING
        # _within_post_recovery_grace reads the timestamp from FILE
        # CONTENT (not mtime) — overwrite with an old timestamp to
        # simulate expiry past _POST_RECOVERY_GRACE_S (5 min).
        grace_path = _r._post_recovery_grace_path()
        old_ts = time.time() - (_r._POST_RECOVERY_GRACE_S + 60)
        grace_path.write_text(str(old_ts))
        assert current_state() == LifeboatState.HEALTHY


class TestPrecedenceMatrix:
    """Per as-built doc §5: LIFEBOAT > RECOVERING > AUTH_DEAD > DEGRADED > HEALTHY."""

    def test_lifeboat_beats_auth_dead(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns["anthropic"] = (time.time() + 1800, 1)
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        # Both LIFEBOAT-flag and AUTH_DEAD-cooldown present;
        # current_state must pick LIFEBOAT (most-derived)
        assert current_state() == LifeboatState.LIFEBOAT

    def test_recovering_beats_auth_dead(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns["anthropic"] = (time.time() + 1800, 1)
        _r._mark_post_recovery()
        assert current_state() == LifeboatState.RECOVERING

    def test_auth_dead_beats_degraded(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        from windyfly.agent.models import _provider_cooldowns
        # Two providers: one short (DEGRADED-shape), one long (AUTH_DEAD-shape)
        _provider_cooldowns["openai"] = (time.time() + 60, 1)
        _provider_cooldowns["anthropic"] = (time.time() + 1800, 1)
        # current_state iterates and picks the first AUTH_DEAD it sees
        assert current_state() == LifeboatState.AUTH_DEAD


class TestExpiredCooldownsIgnored:
    """Provider cooldowns in the past should be skipped (no state)."""

    def test_expired_cooldown_returns_healthy(self, monkeypatch, tmp_path):
        _isolate_flags(monkeypatch, tmp_path)
        from windyfly.agent.models import _provider_cooldowns
        # Cooldown already expired
        _provider_cooldowns["anthropic"] = (time.time() - 100, 1)
        assert current_state() == LifeboatState.HEALTHY
