"""Phase 2.2.2 (observability layer) — LifeboatState enum + current_state().

Pins the contract for the new observability-only enum. NO behavior
change in resurrect.py; these tests just confirm the enum mapping
matches the implicit-state precedence documented in
docs/LIFEBOAT_FSM_AS_BUILT.md §5.

When the full FSM-enforcement refactor lands (Phase 2.2.2 proper,
gated on Grant's 5 design Qs), this test file should expand to one
test per real transition.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent import resurrect as _r
from windyfly.agent.resurrect import LifeboatState, current_state


class TestEnumExists:
    def test_enum_has_five_states(self):
        states = {s.value for s in LifeboatState}
        assert states == {
            "healthy", "degraded", "lifeboat", "recovering", "auth_dead",
        }


class TestCurrentState:
    def test_healthy_when_no_flag_no_cooldown(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv(
            "WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"),
        )
        # Clear cooldowns
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns.clear()
        assert current_state() == LifeboatState.HEALTHY

    def test_lifeboat_when_flag_present(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        assert current_state() == LifeboatState.LIFEBOAT

    def test_recovering_when_grace_active(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv(
            "WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"),
        )
        _r._mark_post_recovery()
        assert current_state() == LifeboatState.RECOVERING

    def test_lifeboat_takes_precedence_over_recovering(
        self, monkeypatch, tmp_path,
    ):
        """Per as-built doc §5 precedence: LIFEBOAT > RECOVERING."""
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv(
            "WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"),
        )
        _r._mark_post_recovery()
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        assert current_state() == LifeboatState.LIFEBOAT

    def test_auth_dead_when_long_cooldown_active(
        self, monkeypatch, tmp_path,
    ):
        import time
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv(
            "WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"),
        )
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns.clear()
        # Cooldown >15min remaining = AUTH_DEAD per #210 long-bucket
        _provider_cooldowns["anthropic"] = (time.time() + 1800, 1)
        try:
            assert current_state() == LifeboatState.AUTH_DEAD
        finally:
            _provider_cooldowns.clear()

    def test_degraded_when_short_cooldown_active(
        self, monkeypatch, tmp_path,
    ):
        import time
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv(
            "WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"),
        )
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns.clear()
        # Short cooldown ≤15min = DEGRADED (transient retry escalator)
        _provider_cooldowns["anthropic"] = (time.time() + 60, 1)
        try:
            assert current_state() == LifeboatState.DEGRADED
        finally:
            _provider_cooldowns.clear()

    def test_current_state_never_raises(
        self, monkeypatch, tmp_path,
    ):
        """Observability path must be crash-proof."""
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        # Even if cooldown dict is corrupted, current_state should
        # fall back to HEALTHY cleanly.
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns.clear()
        _provider_cooldowns["bad"] = "not_a_tuple"  # type: ignore[assignment]
        try:
            state = current_state()
            # Doesn't matter what — just that it didn't raise
            assert isinstance(state, LifeboatState)
        finally:
            _provider_cooldowns.clear()


@pytest.mark.parametrize("state", list(LifeboatState))
def test_enum_value_is_lowercase_string(state):
    assert isinstance(state.value, str)
    assert state.value == state.value.lower()
