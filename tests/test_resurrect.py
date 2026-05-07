"""Resurrect / lifeboat regression tests.

Pin the contract added in PR #133:

  - is_resurrected() reads a file flag, no DB / network / LLM
  - resurrect() probes Ollama; if not running, returns a structured
    "ollama_not_running" reason WITH the install hint (NEVER lies
    about being "back" when Ollama isn't there)
  - resurrect() picks the best installed model from the curated
    PREFERRED_MODELS list, falling back to "largest installed" when
    none of the preferred names match
  - normalize() clears the flag (idempotent — clearing when not
    set is also ok)
  - Atomic flag write (.tmp + rename) — torn flags don't bork bot
  - The agent loop's offline path honors resurrection_state().model
  - /resurrect and /normal slash-command parsers recognize the
    documented aliases AND the grandma-mode phrase entry points
    ("bring me back", "save me", "are you alive")
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from windyfly.agent import resurrect as _r


@pytest.fixture(autouse=True)
def isolated_flag(monkeypatch, tmp_path):
    """Per-test flag isolation. Same pattern as test_yolo_mode."""
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    yield tmp_path


# ── State predicates ──────────────────────────────────────────────


def test_initial_state_inactive():
    assert _r.is_resurrected() is False
    state = _r.resurrection_state()
    assert state["active"] is False


def test_resurrection_state_after_active():
    """Active state must include the chosen model + previous model."""
    with patch.object(_r, "list_installed_ollama_models", return_value=[
        {"name": "llama3.2:3b", "size": 2_000_000_000},
    ]):
        out = _r.resurrect(actor="grant", previous_model="claude-haiku-4-5")
    assert out["ok"] is True
    state = _r.resurrection_state()
    assert state["active"] is True
    assert state["model"] == "llama3.2:3b"
    assert state["previous_model"] == "claude-haiku-4-5"
    assert state["actor"] == "grant"


# ── Lifecycle ──────────────────────────────────────────────────────


def test_resurrect_writes_flag(isolated_flag):
    with patch.object(_r, "list_installed_ollama_models", return_value=[
        {"name": "llama3.2:3b", "size": 2_000_000_000},
    ]):
        _r.resurrect(actor="user")
    flag = isolated_flag / ".resurrected"
    assert flag.exists()
    data = json.loads(flag.read_text())
    assert data["model"] == "llama3.2:3b"


def test_normalize_clears_flag(isolated_flag):
    with patch.object(_r, "list_installed_ollama_models", return_value=[
        {"name": "llama3.2:3b", "size": 2_000_000_000},
    ]):
        _r.resurrect()
    assert _r.is_resurrected() is True
    out = _r.normalize()
    assert out["ok"] is True
    assert out["was_resurrected"] is True
    assert out["prior_model"] == "llama3.2:3b"
    assert _r.is_resurrected() is False


def test_normalize_idempotent_when_not_active():
    out = _r.normalize()
    assert out["ok"] is True
    assert out["was_resurrected"] is False


def test_torn_flag_treated_as_active(isolated_flag):
    """A garbled flag file (mid-write or hand-edited) → still treat
    as active. Better to keep lifeboat ON with no metadata than to
    silently drop back to a dead-cred frontier model mid-emergency."""
    flag = isolated_flag / ".resurrected"
    flag.write_text("not valid json {{{")
    assert _r.is_resurrected() is True
    state = _r.resurrection_state()
    assert state["active"] is True
    assert state["model"] is None


# ── Failure modes (NEVER silently fail) ───────────────────────────


def test_resurrect_when_ollama_not_running():
    """Ollama not installed/running → return structured reason WITH
    the install hint. Channel adapter renders the hint into a
    user-readable message."""
    with patch.object(_r, "list_installed_ollama_models", return_value=[]):
        out = _r.resurrect()
    assert out["ok"] is False
    assert out["reason"] == "ollama_not_running"
    assert "install_hint" in out
    assert "ollama.com/install.sh" in out["install_hint"]


def test_resurrect_when_ollama_has_no_models(isolated_flag):
    """Ollama running but empty — also a structured failure with hint."""
    # Mock list to return non-empty but with no usable names: edge
    # case where the API returns malformed entries.
    with patch.object(_r, "list_installed_ollama_models", return_value=[
        {"size": 0},  # no name field
    ]):
        out = _r.resurrect()
    assert out["ok"] is False
    assert out["reason"] == "no_models_installed"


def test_resurrect_does_not_lie_when_ollama_dead(isolated_flag):
    """Critical: when Ollama isn't there, the FLAG MUST NOT be
    written. Otherwise the bot would think it's resurrected and try
    to call a dead Ollama on every message."""
    with patch.object(_r, "list_installed_ollama_models", return_value=[]):
        _r.resurrect()
    assert _r.is_resurrected() is False, (
        "resurrect() must not write the flag when Ollama is unavailable"
    )


# ── Best-model picker ─────────────────────────────────────────────


def test_pick_best_picks_first_preferred():
    """When llama3.2:3b is installed, it wins (top of preference)."""
    installed = [
        {"name": "llama3.2:1b", "size": 1_000_000_000},
        {"name": "llama3.2:3b", "size": 2_000_000_000},
        {"name": "phi3:mini", "size": 2_300_000_000},
    ]
    assert _r.pick_best_model(installed) == "llama3.2:3b"


def test_pick_best_falls_back_to_largest():
    """No preferred match → largest installed wins."""
    installed = [
        {"name": "obscure-model:7b", "size": 4_000_000_000},
        {"name": "another-obscure:1b", "size": 1_000_000_000},
    ]
    assert _r.pick_best_model(installed) == "obscure-model:7b"


def test_pick_best_returns_none_on_empty():
    assert _r.pick_best_model([]) is None
    assert _r.pick_best_model([{"size": 0}]) is None  # no names


# ── current_model() — used by offline.py ──────────────────────────


def test_current_model_active(isolated_flag):
    with patch.object(_r, "list_installed_ollama_models", return_value=[
        {"name": "qwen2.5:3b", "size": 2_500_000_000},
    ]):
        _r.resurrect()
    assert _r.current_model() == "qwen2.5:3b"


def test_current_model_inactive():
    assert _r.current_model() is None


# ── Slash-command parser ──────────────────────────────────────────


class TestResurrectParser:
    @staticmethod
    def _parsers():
        from windyfly.channels.slash_commands import (
            is_resurrect_message, is_normal_message,
        )
        return is_resurrect_message, is_normal_message

    def test_resurrect_aliases(self):
        is_r, _ = self._parsers()
        for cmd in ("/resurrect", "/save-me", "/lifeboat", "/sos"):
            assert is_r(cmd), f"{cmd!r} should trigger /resurrect"

    def test_resurrect_phrases_grandma_mode(self):
        """A grandma who doesn't remember the slash — phrase match
        catches her panicked plain-English."""
        is_r, _ = self._parsers()
        for phrase in (
            "bring me back",
            "Bring Me Back Alive",
            "save me please",
            "are you alive?",
            "I can't reach you",
            "Are you there?",
        ):
            assert is_r(phrase), f"{phrase!r} should trigger /resurrect"

    def test_resurrect_does_not_trigger_on_unrelated(self):
        is_r, _ = self._parsers()
        for benign in (
            "/version",  # different command
            "hello there",
            "tell me a joke about saving the day",  # contains "saving" not "save me"
        ):
            assert not is_r(benign), f"{benign!r} should NOT trigger /resurrect"

    def test_resurrect_handles_none_and_empty(self):
        is_r, _ = self._parsers()
        assert is_r(None) is False
        assert is_r("") is False
        assert is_r("   ") is False

    def test_normal_aliases(self):
        _, is_n = self._parsers()
        for cmd in ("/normal", "/normal-mode", "/back-to-normal"):
            assert is_n(cmd)

    def test_normal_does_not_trigger_resurrect(self):
        is_r, is_n = self._parsers()
        assert is_n("/normal") is True
        assert is_r("/normal") is False
