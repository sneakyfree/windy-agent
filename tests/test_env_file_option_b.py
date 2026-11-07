"""Which env file an agent reads (Boss BOARD 2026-11-07T16:13:24Z, option B): WINDY_ENV_FILE, else the project .env only
when the agent's own config lives in that project; never a bare load_dotenv(); an existing Mac install keeps its keys."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

import windyfly.env_file as env_file

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"
KEY = "WINDY_TEST_OPTION_B_KEY"


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "agent-project"
    root.mkdir()
    (root / ".env").write_text(f"{KEY}=from-project-env\n", encoding="utf-8")
    (root / "windyfly.toml").write_text("[agent]\nname = 'x'\n", encoding="utf-8")
    # conftest stubs load_dotenv suite-wide (it walks up from CWD); these tests need the real one, in env_file only.
    from dotenv.main import load_dotenv as real_load_dotenv
    monkeypatch.setattr(env_file, "load_dotenv", real_load_dotenv)
    monkeypatch.setattr(env_file, "_project_root", lambda: root)
    monkeypatch.setattr(env_file, "_said_implicit", False)
    monkeypatch.delenv("WINDY_ENV_FILE", raising=False)
    monkeypatch.delenv("WINDYFLY_CONFIG", raising=False)
    monkeypatch.delenv(KEY, raising=False)
    return root


def test_explicit_file_wins_and_is_the_only_one_read(project, tmp_path, monkeypatch):
    own = tmp_path / "own.env"
    own.write_text(f"{KEY}=from-explicit\n", encoding="utf-8")
    monkeypatch.setenv("WINDY_ENV_FILE", str(own))
    assert env_file.load_agent_env(project / "windyfly.toml") == own
    import os
    assert os.environ[KEY] == "from-explicit"


def test_a_missing_explicit_file_reads_nothing(project, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("WINDY_ENV_FILE", str(tmp_path / "nope.env"))
    with caplog.at_level(logging.WARNING):
        assert env_file.load_agent_env(project / "windyfly.toml") is None
    import os
    assert KEY not in os.environ                      # NOT the project .env as a fallback
    assert any("does not exist" in r.getMessage() for r in caplog.records)


def test_config_in_the_project_reads_its_env_and_says_so_once(project, caplog):
    import os
    with caplog.at_level(logging.INFO, logger="windyfly.env_file"):
        assert env_file.load_agent_env(project / "windyfly.toml") == project / ".env"
        env_file.load_agent_env(project / "windyfly.toml")
    assert os.environ[KEY] == "from-project-env"
    said = [r for r in caplog.records if "implicitly" in r.getMessage()]
    assert len(said) == 1 and "from-project-env" not in said[0].getMessage()   # one line, names only


def test_a_second_agent_with_its_config_elsewhere_never_reads_it(project, tmp_path):
    """The 11-05 lab case: same code checkout, its own config in another folder."""
    import os
    lab_cfg = tmp_path / "lab" / "config.toml"
    lab_cfg.parent.mkdir()
    lab_cfg.write_text("", encoding="utf-8")
    assert env_file.load_agent_env(lab_cfg) is None
    assert KEY not in os.environ


def test_an_existing_mac_install_still_starts_with_its_keys(project, monkeypatch):
    """Boss: existing Mac agents must not start with no keys after an upgrade. The old plist sets only PATH and runs
    the bridge in the project folder with the default config, so the bridge's load must still find the project .env."""
    import os
    monkeypatch.chdir(project)
    assert env_file.load_agent_env("windyfly.toml") == project / ".env"   # exactly what uds_server calls
    assert os.environ[KEY] == "from-project-env"


def test_update_names_the_env_file_in_an_old_plist_once(project, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    plist = tmp_path / "Library" / "LaunchAgents" / "com.windyfly.agent.plist"
    plist.parent.mkdir(parents=True)
    plist.write_text("<plist>\n    <key>EnvironmentVariables</key>\n    <dict>\n        <key>PATH</key>\n"
                     "        <string>/usr/bin</string>\n    </dict>\n</plist>\n", encoding="utf-8")
    assert env_file.name_env_file_in_services() == [plist]
    text = plist.read_text(encoding="utf-8")
    assert "<key>WINDY_ENV_FILE</key>" in text and f"<string>{project / '.env'}</string>" in text
    assert env_file.name_env_file_in_services() == []                      # idempotent
    assert text.count("<key>WINDY_ENV_FILE</key>") == 1


def test_update_names_the_env_file_in_an_old_systemd_unit(project, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    unit = tmp_path / ".config" / "systemd" / "user" / "windyfly.service"
    unit.parent.mkdir(parents=True)
    unit.write_text(f"[Unit]\nDescription=x\n\n[Service]\nType=simple\nEnvironmentFile={project / '.env'}\n",
                    encoding="utf-8")
    assert env_file.name_env_file_in_services() == [unit]
    assert f"Environment=WINDY_ENV_FILE={project / '.env'}" in unit.read_text(encoding="utf-8")


def test_no_bare_load_dotenv_anywhere():
    for p in SRC.rglob("*.py"):
        code = "\n".join(ln for ln in p.read_text(encoding="utf-8").splitlines()
                         if not ln.lstrip().startswith(("#", '"', "'")) and "``" not in ln)
        assert "load_dotenv()" not in code, f"{p.relative_to(SRC)} has a bare load_dotenv()"
