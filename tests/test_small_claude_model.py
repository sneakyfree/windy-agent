"""The agent's own background calls name Haiku 5.5, from ONE constant (Boss, 10-13).

Windy Mind will serve explicit picks as named. The journal, failure lessons, goal checks and the
vision fallback named claude-haiku-4-5 in code, so that switch would have moved them to the older,
pricier Haiku 4.5 without anyone choosing it. Every model call is stubbed; no network.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from windyfly.agent import models

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"


def test_the_small_claude_model_is_haiku_5_5():
    assert models.SMALL_CLAUDE_MODEL == "claude-haiku-5-5"


def _journal_model(config):
    from windyfly.agent.maintenance import _journal_model_caller

    seen = []
    with patch("windyfly.agent.models.call_llm",
               side_effect=lambda msgs, **kw: seen.append(kw["model"]) or {"content": "x"}):
        _journal_model_caller(config)([{"role": "user", "content": "day"}])
    return seen


def test_the_journal_names_haiku_5_5_unless_configured():
    assert _journal_model({}) == ["claude-haiku-5-5"]
    assert _journal_model({"agent": {"journal_model": "claude-sonnet-5"}}) == ["claude-sonnet-5"]


def test_a_failure_lesson_names_haiku_5_5_unless_the_env_says_otherwise(monkeypatch):
    from windyfly.agent.failure_detector import _distill_correction_code

    friction = {"user_message": "no, TUESDAY", "agent_message": "Monday", "pattern_matched": "no,"}
    monkeypatch.setenv("WINDY_LLM_CORRECTIONS", "1")
    for env, want in ((None, "claude-haiku-5-5"), ("claude-sonnet-5", "claude-sonnet-5")):
        if env is None:
            monkeypatch.delenv("WINDY_DISTILL_MODEL", raising=False)
        else:
            monkeypatch.setenv("WINDY_DISTILL_MODEL", env)
        seen = []
        with patch("windyfly.agent.models.call_llm",
                   side_effect=lambda msgs, **kw: seen.append(kw["model"]) or {"content": "ok"}):
            _distill_correction_code("wrong_date", friction)
        assert seen == [want]


def test_the_goal_check_names_haiku_5_5_unless_configured():
    from windyfly.agent import goal_evaluator

    for config, want in (({}, "claude-haiku-5-5"),
                         ({"goal": {"evaluator_model": "claude-sonnet-5"}}, "claude-sonnet-5")):
        seen = []
        with patch.object(goal_evaluator, "call_llm",
                          side_effect=lambda msgs, **kw: seen.append(kw["model"]) or {"content": "{}"}):
            goal_evaluator.evaluate_goal("ship it", [{"role": "user", "content": "go"}], config=config)
        assert seen == [want]


# Places that may still SAY claude-haiku-4-5 because they describe the model rather than call it:
# prices, context caps, the fallback menus and hand lists (their clean-up is separate, #556), and
# the lifeboat's example text.
_MAY_NAME_4_5 = {
    "agent/models.py", "agent/models_catalog.py", "agent/providers.py", "agent/resurrect.py",
    "commands/core.py", "provider_defaults.py", "setup_wizard.py",
}


def test_no_other_runtime_file_names_haiku_4_5():
    named = {p.relative_to(SRC).as_posix() for p in SRC.rglob("*.py")
             if re.search(r"claude-haiku-4-5", p.read_text(encoding="utf-8"))}
    assert named <= _MAY_NAME_4_5, sorted(named - _MAY_NAME_4_5)
