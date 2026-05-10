"""Test-suite-wide autouse fixtures.

This file does TWO things for every test (autouse):

  1. **Isolates all production file-flag paths** to a per-test
     temp dir. Pre-conftest, tests using ``Database(":memory:")``
     would still see the live bot's ``~/.windy/.paused`` /
     ``.resurrected`` / ``.auto_resurrect_last`` etc. flags.
     This caused 21+ flaky failures in the 2026-05-07 hardening
     sweep when auto-resurrect fired on the live bot mid-test.

  2. **Disables the first-contact welcome shortcut by default**.
     PR #142's welcome short-circuits agent_respond on virgin DBs
     BEFORE the LLM call — saves a token in production but breaks
     unit tests that mock call_llm. Tests that specifically test
     first-contact behavior opt-in via
     ``@pytest.mark.virgin_db_welcome`` (or module-level
     ``pytestmark = pytest.mark.virgin_db_welcome``).

The autouse design guarantees no test sees production state and no
test gets bypassed by a feature shortcut by accident — the failure
modes ride exactly where the test author wrote them.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _isolate_production_flags(monkeypatch, tmp_path):
    """Redirect every production flag/marker file path to per-test
    tmp dirs. Without this, tests pick up state from earlier test
    runs OR from the live windy-0 bot that's running on the same
    machine.

    Surfaced 2026-05-07: live bot's auto-resurrect flag (actor=
    auto-chain-exhausted) leaked into test runs and routed mocked
    agent_respond calls through the resurrection-mode Ollama path
    instead of the LLM mock.
    """
    monkeypatch.setenv("WINDY_PAUSE_FLAG", str(tmp_path / ".paused"))
    monkeypatch.setenv("WINDY_YOLO_FLAG", str(tmp_path / ".yolo"))
    monkeypatch.setenv("WINDY_GUEST_FLAG", str(tmp_path / ".guest"))
    monkeypatch.setenv("WINDY_RESURRECT_FLAG", str(tmp_path / ".resurrected"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_DISABLED",
                       str(tmp_path / ".auto_resurrect_disabled"))
    monkeypatch.setenv("WINDY_AUTO_RESURRECT_LAST",
                       str(tmp_path / ".auto_resurrect_last"))
    monkeypatch.setenv("WINDY_RECOVERY_PROBE_LAST",
                       str(tmp_path / ".recovery_probe_last"))
    monkeypatch.setenv("WINDY_POST_RECOVERY_GRACE",
                       str(tmp_path / ".post_recovery_grace"))
    yield


@pytest.fixture(autouse=True)
def _default_skip_first_contact_welcome(request):
    """Default OFF for the first-contact welcome shortcut so unit
    tests that mock call_llm aren't bypassed.

    Tests that need real first-contact behavior opt-in via the
    ``virgin_db_welcome`` marker. ``test_first_contact_welcome.py``
    already does this at the module level::

        pytestmark = pytest.mark.virgin_db_welcome
    """
    if "virgin_db_welcome" in request.keywords:
        # Test explicitly wants the real welcome behavior — let it
        # through.
        yield
        return
    # Default: prevent welcome from firing.
    with patch("windyfly.agent.welcome.is_first_contact", return_value=False):
        yield


@pytest.fixture(autouse=True)
def _default_skip_real_ollama(request):
    """Default OFF for real Ollama calls. Once Ollama is installed
    on the host (PR #148), tests that don't explicitly mock the
    Ollama probe path otherwise hit the real local server, with
    30s timeouts per call. A test file with 10 LLM-mock tests that
    each fall through to chain-exhaustion would take 300+ seconds.

    Tests that EXERCISE the Ollama integration opt-in via the
    ``real_ollama`` marker.
    """
    if "real_ollama" in request.keywords:
        yield
        return
    with patch("windyfly.agent.offline.is_ollama_available", return_value=False), \
         patch("windyfly.agent.resurrect.list_installed_ollama_models", return_value=[]):
        yield


@pytest.fixture(autouse=True)
def _default_skip_state_emoji_prefix(request):
    """Default OFF for the gas-tank panel + always-on state emoji
    prefix (PR #144) so unit tests that check exact LLM-mock output
    don't have to account for header bytes that were never the
    behavior they were testing.

    Tests that EXERCISE the prefix behavior (test_context_header.py,
    test_context_header_per_session.py) opt-in via the
    ``state_emoji_prefix`` marker.
    """
    if "state_emoji_prefix" in request.keywords:
        yield
        return
    # Default: identity-passthrough. The agent loop calls
    # maybe_prepend_header(text, tokens) and expects a string back —
    # make it the original text untouched.
    with patch(
        "windyfly.agent.context_header.maybe_prepend_header",
        side_effect=lambda text, tokens: text,
    ), patch(
        "windyfly.agent.loop.maybe_prepend_header",
        side_effect=lambda text, tokens: text,
    ):
        yield
