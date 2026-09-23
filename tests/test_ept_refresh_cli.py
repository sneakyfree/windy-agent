"""`windy ept refresh` output: every refresh_ept status gets an honest line.

The 0.7.2 pre-release run printed "Refresh failed (None)" for status
'adopted'. Only a real HTTP error/refusal or an unreachable issuer is a
failure; anything the CLI doesn't recognise means Eternitas kept the
current, still-valid token.
"""

from __future__ import annotations

import argparse
import os

import pytest

from windyfly import cli
from windyfly.eternitas import ept_refresh


def _run(monkeypatch, capsys, result: dict) -> str:
    monkeypatch.setattr(ept_refresh, "refresh_ept", lambda force=False: result)
    monkeypatch.setattr(ept_refresh, "resolve_env_file", lambda: None)
    cli._cmd_ept(argparse.Namespace(ept_command="refresh", force=False))
    return capsys.readouterr().out


@pytest.mark.parametrize("result", [
    {"status": "current"},
    {"status": "adopted", "env_file": "/x/.env"},
    {"status": "unchanged", "reissued": False, "reason": "not_due"},
    {"status": "some_future_status", "reason": "claims_current"},
    {"status": "failed", "error": "no_token"},
])
def test_kept_statuses_are_success_lines(monkeypatch, capsys, result):
    out = _run(monkeypatch, capsys, result)
    assert "✓" in out
    assert "failed" not in out.lower()
    assert "None" not in out


@pytest.mark.parametrize("result, shown", [
    ({"status": "failed", "http": 401, "hint": "run `windy login`"}, "HTTP 401"),
    ({"status": "failed", "http": 403}, "HTTP 403"),
    ({"status": "failed", "error": "unreachable"}, "unreachable"),
])
def test_real_failures_say_what_failed(monkeypatch, capsys, result, shown):
    out = _run(monkeypatch, capsys, result)
    assert "Refresh failed" in out
    assert shown in out
    assert "None" not in out


def test_loads_the_env_file_ept_refresh_persists_to(monkeypatch, tmp_path, capsys):
    """Run from a directory with no .env: the token must still come from
    WINDY_ENV_FILE (ept_refresh's resolution), not the CWD."""
    agent_env = tmp_path / "agent.env"
    agent_env.write_text("ETERNITAS_PASSPORT_TOKEN=tok-from-agent-env\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("WINDY_ENV_FILE", str(agent_env))
    monkeypatch.delenv("ETERNITAS_PASSPORT_TOKEN", raising=False)
    # conftest neutralises load_dotenv suite-wide; this test needs the real one.
    import dotenv
    import dotenv.main
    monkeypatch.setattr(dotenv, "load_dotenv", dotenv.main.load_dotenv)
    seen = {}

    def fake_refresh(force=False):
        seen["token"] = os.environ.get("ETERNITAS_PASSPORT_TOKEN")
        return {"status": "current"}

    monkeypatch.setattr(ept_refresh, "refresh_ept", fake_refresh)
    cli._cmd_ept(argparse.Namespace(ept_command="refresh", force=False))
    assert seen["token"] == "tok-from-agent-env"
    os.environ.pop("ETERNITAS_PASSPORT_TOKEN", None)
