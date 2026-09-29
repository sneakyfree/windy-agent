"""A fallback reply is never unmarked (Windy Zero ran 4 days on llama3.2:3b
behind a tripped Mind breaker with no notice, 2026-09-25..29)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from windyfly.agent import resurrect as _r
from windyfly.agent.loop import _auto_resurrect_banner
from tests.test_no_resurrect_on_401 import _make_config, isolated_flags, stack  # noqa: F401

MIND_403 = (
    "LLM call failed across all providers in chain (attempted=['mind(claude-opus-5-5)']): "
    "Mind HTTP 403: your circuit breaker 'model:claude-opus-5-5' is tripped"
)


def test_should_show_fallback_notice_once_per_session():
    _r.clear_fallback_notice("s-once")
    assert _r.should_show_fallback_notice("s-once") is True
    assert _r.should_show_fallback_notice("s-once") is False
    _r.clear_fallback_notice("s-once")
    assert _r.should_show_fallback_notice("s-once") is True


def test_banner_names_a_403_breaker():
    assert "switched off upstream" in _auto_resurrect_banner("llama3.2:3b", MIND_403)


@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_grace_path_reply_is_never_plain(mock_llm, _online, stack, isolated_flags):  # noqa: F811
    config, db, wq = stack
    from windyfly.agent.loop import agent_respond

    _r.clear_fallback_notice("grace-test")
    mock_llm.side_effect = RuntimeError(MIND_403)
    with patch.object(_r, "auto_resurrect_attempt",
                      return_value={"ok": False, "reason": "post_recovery_grace"}), \
         patch("windyfly.agent.offline._call_ollama", return_value="local reply"), \
         patch("windyfly.agent.offline.is_ollama_available", return_value=True):
        first = agent_respond(config, db, wq, "hi", "grace-test")
        second = agent_respond(config, db, wq, "hi again", "grace-test")

    assert "backup brain" in first and first.lstrip().startswith("🛟")
    assert second.startswith("🛟 ") and "backup brain" not in second
