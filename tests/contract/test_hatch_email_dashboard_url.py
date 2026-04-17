"""Contract tests for P3-D6 — overridable dashboard URL in the
hatch-announcement email.

Before: the dashboard link was hardcoded to
https://windyword.ai/app/fly. On internal betas or local dev the
welcome email pointed newcomers at an unreachable URL.

Fix: accept dashboard_url param first, WINDYFLY_DASHBOARD_URL env
second, public default last.
"""

from __future__ import annotations

import pytest

from windyfly.hatch_email import format_hatch_email


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("WINDYFLY_DASHBOARD_URL", raising=False)


def _email(**overrides):
    params = {
        "agent_name": "test",
        "passport_id": "ET26-X",
        "agent_email": "t@windymail.test",
        "agent_phone": "+15550001111",
        "model_id": "m",
        "hatch_time": "now",
        "certificate_number": "WF-000",
        "neural_fingerprint": "f",
    }
    params.update(overrides)
    return format_hatch_email(**params)


def test_default_when_no_override():
    out = _email()
    assert "https://windyword.ai/app/fly" in (out["text"] + out["html"])


def test_explicit_param_wins_over_env(monkeypatch):
    monkeypatch.setenv("WINDYFLY_DASHBOARD_URL", "https://env.example/app")
    out = _email(dashboard_url="https://explicit.example/app")
    body = out["text"] + out["html"]
    assert "https://explicit.example/app" in body
    assert "https://env.example/app" not in body


def test_env_wins_when_no_param(monkeypatch):
    monkeypatch.setenv("WINDYFLY_DASHBOARD_URL", "http://localhost:3000/app")
    out = _email()
    assert "http://localhost:3000/app" in (out["text"] + out["html"])
    assert "https://windyword.ai/app/fly" not in (out["text"] + out["html"])
