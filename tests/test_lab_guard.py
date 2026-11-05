"""The lab guard refuses any lab run that would act as a real agent or on a person's keys (11-04: the weekly QA battery
minted AS Zero with Zero's key from ~/.windy and spent Grant's own provider keys). Each refusal is proven here."""

from __future__ import annotations

import base64
import json

import pytest

from windyfly import lab_guard
from windyfly.eternitas import agent_keys as ak

LAB = "ET26-TEST-M0NF"
REAL = "ET26-T11V-NPD1"


def _token(sub: str) -> str:
    enc = lambda d: base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()  # noqa: E731
    return f"{enc({'alg': 'ES256'})}.{enc({'sub': sub})}.SIG"


@pytest.fixture
def lab(tmp_path, monkeypatch):
    """A correctly isolated lab: its own home, state folder, env file and key; the real agent's files elsewhere."""
    real_home = tmp_path / "grant"
    (real_home / ".windy").mkdir(parents=True)          # the real files are HIDDEN from the lab (InaccessiblePaths):
    #                                                     here they are simply absent (chmod can't hide from root)
    lab_home = tmp_path / "zero-lab"
    (lab_home / ".windy").mkdir(parents=True)
    (lab_home / ".windy" / "agent.env").write_text(f"ETERNITAS_PASSPORT={LAB}\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(lab_home))
    monkeypatch.setenv("WINDY_LAB_GUARD_REAL_ENV", str(real_home / ".windy" / "windy-0.env"))
    monkeypatch.setenv("WINDY_LAB_GUARD_REAL_STATE", str(real_home / ".windy"))
    monkeypatch.setenv("WINDY_STATE_DIR", str(lab_home / ".windy"))
    monkeypatch.setenv("WINDY_ENV_FILE", str(lab_home / ".windy" / "agent.env"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(lab_home / ".windy" / "credentials.json"))
    monkeypatch.setenv("WINDY_CLAUDE_CREDENTIALS_PATH", str(lab_home / ".claude" / ".credentials.json"))
    monkeypatch.setenv("ETERNITAS_PASSPORT", LAB)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _token(LAB))
    for name in lab_guard.PROVIDER_KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("WINDY_PASSPORT_EPT", raising=False)
    return {"real_home": real_home, "lab_home": lab_home}


def _register(passport: str) -> None:
    ak.local_kid()
    data = ak.load_credentials()
    ak.active_entry(data).update(registered_at="2026-11-01T00:00:00Z", passport=passport)
    ak.save_credentials(data)


def test_a_correctly_isolated_lab_passes(lab, capsys):
    _register(LAB)
    assert lab_guard.problems() == []
    assert lab_guard.main([]) == 0 and "acting as ET26-TEST-M0NF" in capsys.readouterr().out


def test_the_real_agents_passport_is_refused(lab, monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT", REAL)
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _token(REAL))
    assert any("not a test passport" in p for p in lab_guard.problems())


def test_a_test_passport_with_the_real_agents_token_is_refused(lab, monkeypatch):
    monkeypatch.setenv("ETERNITAS_PASSPORT_TOKEN", _token(REAL))
    assert lab_guard.problems() == ["the passport token is for another passport"]


def test_the_11_01_case_the_real_agents_key_folder_is_refused(lab, monkeypatch):
    """The battery had the lab's passport but windyfly's key file was the real agent's (~/.windy)."""
    monkeypatch.setenv("WINDY_STATE_DIR", str(lab["real_home"] / ".windy"))
    monkeypatch.setenv("WINDY_CREDENTIALS_FILE", str(lab["real_home"] / ".windy" / "credentials.json"))
    _register(REAL)
    found = lab_guard.problems()
    assert "the agent key file is the real agent's (its state folder)" in found
    assert "the registered agent key belongs to another passport" in found


def test_a_key_registered_for_another_passport_is_refused(lab):
    _register(REAL)
    assert lab_guard.problems() == ["the registered agent key belongs to another passport"]


def _real_env(lab):
    path = lab["real_home"] / ".windy" / "windy-0.env"
    path.write_text(f"ETERNITAS_PASSPORT={REAL}\n", encoding="utf-8")
    return path


def test_the_real_files_must_be_hidden_from_the_lab(lab):
    """Hub on #711: the lab runs as the same OS user, so the real login and env must be HIDDEN from it (the unit's
    InaccessiblePaths); still readable = refused."""
    real_env = _real_env(lab)
    login = lab["real_home"] / ".claude" / ".credentials.json"
    login.parent.mkdir(parents=True)
    login.write_text("{}", encoding="utf-8")
    found = lab_guard.problems()
    assert "the real agent's env file is readable by this process" in found
    assert "the real person's Claude login is readable by this process" in found
    real_env.unlink()
    login.unlink()
    assert lab_guard.problems() == []


def test_the_real_agents_env_file_is_refused(lab, monkeypatch):
    monkeypatch.setenv("WINDY_ENV_FILE", str(_real_env(lab)))
    found = lab_guard.problems()
    assert "WINDY_ENV_FILE is the real agent's" in found
    assert "the env file windyfly would write is the real agent's" in found


@pytest.mark.parametrize("name", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY", "GEMINI_API_KEY"])
def test_a_persons_provider_key_is_refused_by_name_never_value(lab, monkeypatch, capsys, name):
    monkeypatch.setenv(name, "sk-SECRET-VALUE")
    assert lab_guard.main([]) == 2
    err = capsys.readouterr().err
    assert f"{name} is set" in err and "SECRET" not in err


def test_a_readable_claude_login_is_refused(lab):
    login = lab["lab_home"] / ".claude" / ".credentials.json"
    login.parent.mkdir(parents=True)
    login.write_text("{}", encoding="utf-8")
    assert lab_guard.problems() == ["a Claude login file is readable"]


def test_the_dry_run_mint_must_come_back_as_the_test_passport(lab, monkeypatch):
    _register(LAB)
    monkeypatch.setattr(ak, "request_agent_token", lambda aud, **kw: {"token": _token(REAL)})
    assert lab_guard.main(["--mint"]) == 2
    monkeypatch.setattr(ak, "request_agent_token", lambda aud, **kw: {"token": _token(LAB)})
    assert lab_guard.main(["--mint"]) == 0


def test_a_failed_dry_run_mint_refuses(lab, monkeypatch):
    _register(LAB)

    def refuse(aud, **kw):
        raise ak.AgentTokenError("passport_suspended", "", 403)

    monkeypatch.setattr(ak, "request_agent_token", refuse)
    assert lab_guard.main(["--mint"]) == 2
