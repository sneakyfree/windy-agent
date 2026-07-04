"""Phase 2.2.2 — LifeboatFSM facade class tests.

Pins the explicit FSM facade introduced as the final piece of the
Phase 2.2.2 refactor. Functions stay public for back-compat; the
class is the new canonical API for callers that want named transitions.
"""

from __future__ import annotations

from unittest.mock import patch

from windyfly.agent import resurrect as _r
from windyfly.agent.resurrect import LifeboatFSM, LifeboatState, fsm


class TestFSMSingleton:
    def test_fsm_is_LifeboatFSM_instance(self):
        assert isinstance(fsm, LifeboatFSM)

    def test_state_returns_enum(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        monkeypatch.setenv(
            "WINDY_POST_RECOVERY_GRACE", str(tmp_path / ".g"),
        )
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns.clear()
        assert fsm.state() == LifeboatState.HEALTHY


class TestEnterLifeboat:
    def test_transitions_healthy_to_lifeboat(
        self, monkeypatch, tmp_path, caplog,
    ):
        import logging as _logging
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        from windyfly.agent.models import _provider_cooldowns
        _provider_cooldowns.clear()
        assert fsm.state() == LifeboatState.HEALTHY
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            with caplog.at_level(_logging.INFO):
                result = fsm.enter_lifeboat(actor="test")
        assert result["ok"] is True
        assert fsm.state() == LifeboatState.LIFEBOAT
        # The transition log line must be emitted
        assert any(
            "fsm.transition" in rec.message and "enter_lifeboat" in rec.message
            for rec in caplog.records
        ), f"missing fsm.transition log; got: {[r.message for r in caplog.records]}"


class TestExitToHealthy:
    def test_transitions_lifeboat_to_healthy(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            fsm.enter_lifeboat(actor="test")
        assert fsm.state() == LifeboatState.LIFEBOAT
        result = fsm.exit_to_healthy()
        assert result["ok"] is True
        assert fsm.state() == LifeboatState.HEALTHY


class TestFunctionAPIPreserved:
    """Existing function-level API must still work — back-compat."""

    def test_resurrect_still_callable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            r = _r.resurrect(actor="user")
        assert r["ok"] is True

    def test_normalize_still_callable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        with patch.object(_r, "list_installed_ollama_models", return_value=[
            {"name": "llama3.2:3b", "size": 2_000_000_000},
        ]):
            _r.resurrect(actor="user")
        r = _r.normalize()
        assert r["ok"] is True

    def test_is_resurrected_still_callable(self, monkeypatch, tmp_path):
        monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".r"))
        assert _r.is_resurrected() is False
