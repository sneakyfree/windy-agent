"""Config-loader resilience contract (2026-07-04 audit).

A corrupt windyfly.toml previously raised an uncaught TOMLDecodeError at
boot; under systemd Restart=always that is an invisible crash-loop with
the reason buried in journalctl. The contract now: corrupt file → boot
on DEFAULT_CONFIG + env overrides, record ``_config_error`` for /status
and boot logging. Missing file still raises (clean actionable exit).
"""

from __future__ import annotations

import pytest

from windyfly.config import DEFAULT_CONFIG, load_config


VALID_TOML = """
[agent]
name = "Testy"
default_model = "claude-sonnet-5"

[memory]
db_path = "data/test.db"
"""

CORRUPT_TOML = """
[agent
name = "broken
"""


def test_valid_config_loads_normally(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    cfg_file = tmp_path / "windyfly.toml"
    cfg_file.write_text(VALID_TOML)
    cfg = load_config(str(cfg_file))
    assert cfg["agent"]["name"] == "Testy"
    assert "_config_error" not in cfg


def test_missing_config_still_raises():
    with pytest.raises(FileNotFoundError):
        load_config("/nonexistent/dir/windyfly.toml")


def test_corrupt_config_falls_back_to_defaults(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    cfg_file = tmp_path / "windyfly.toml"
    cfg_file.write_text(CORRUPT_TOML)
    with caplog.at_level("ERROR", logger="windyfly.config"):
        cfg = load_config(str(cfg_file))
    assert cfg["_config_error"].startswith("TOMLDecodeError")
    assert cfg["agent"]["name"] == DEFAULT_CONFIG["agent"]["name"]
    assert cfg["memory"]["db_path"] == DEFAULT_CONFIG["memory"]["db_path"]
    assert any("safe defaults" in r.message for r in caplog.records)


def test_corrupt_config_does_not_mutate_default_config(tmp_path):
    cfg_file = tmp_path / "windyfly.toml"
    cfg_file.write_text(CORRUPT_TOML)
    cfg = load_config(str(cfg_file))
    cfg["agent"]["name"] = "mutated"
    cfg["ecosystem"]["injected"] = True
    assert DEFAULT_CONFIG["agent"]["name"] == "Windy Fly"
    assert "injected" not in DEFAULT_CONFIG["ecosystem"]


def test_env_overrides_still_apply_on_corrupt_config(tmp_path, monkeypatch):
    cfg_file = tmp_path / "windyfly.toml"
    cfg_file.write_text(CORRUPT_TOML)
    monkeypatch.setenv("DEFAULT_MODEL", "claude-opus-4-8")
    cfg = load_config(str(cfg_file))
    assert cfg["agent"]["default_model"] == "claude-opus-4-8"


def test_windyfly_config_env_honored_when_path_omitted(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    cfg_file = tmp_path / "custom.toml"
    cfg_file.write_text(VALID_TOML)
    monkeypatch.setenv("WINDYFLY_CONFIG", str(cfg_file))
    cfg = load_config()
    assert cfg["agent"]["name"] == "Testy"


def test_explicit_path_beats_windyfly_config_env(tmp_path, monkeypatch):
    monkeypatch.delenv("DEFAULT_MODEL", raising=False)
    env_file = tmp_path / "env.toml"
    env_file.write_text('[agent]\nname = "FromEnv"\n')
    explicit_file = tmp_path / "explicit.toml"
    explicit_file.write_text('[agent]\nname = "Explicit"\n')
    monkeypatch.setenv("WINDYFLY_CONFIG", str(env_file))
    cfg = load_config(str(explicit_file))
    assert cfg["agent"]["name"] == "Explicit"
