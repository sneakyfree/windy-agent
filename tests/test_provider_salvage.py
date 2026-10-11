"""Wave 3: provider keys move from the retired provider files into the env file, once (Hub 10-11).

Salvage, then scrub: a key that sits only in one of those files is written into the private env
file first; the files are rewritten (brain) or deleted (old dashboard) only after every write
worked. Env wins on a conflict. A failure keeps every file and the key works for this run. No
key value ever appears in a log line or in the report.
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.agent import provider_salvage, providers
from windyfly.agent.provider_salvage import default_env_var, salvage_provider_keys

K_STATE = "sk-statefile-AAAAAAAAAAAAAAAAAAAAAAAA"
K_ACTIVE = "sk-active-BBBBBBBBBBBBBBBBBBBBBBBBBB"
K_INACTIVE = "sk-inactive-CCCCCCCCCCCCCCCCCCCCCCCC"
K_GWPROV = "gsk_DDDDDDDDDDDDDDDDDDDDDDDDDDDD"
K_NOTE = "xai-EEEEEEEEEEEEEEEEEEEEEEEEEEEE"
K_CUSTOM = "sk-custom-FFFFFFFFFFFFFFFFFFFFFFFFF"
ALL_KEYS = (K_STATE, K_ACTIVE, K_INACTIVE, K_GWPROV, K_NOTE, K_CUSTOM)


@pytest.fixture
def box(tmp_path):
    state_file = tmp_path / "state" / "providers.json"
    gw = tmp_path / "repo" / "data"
    gw.mkdir(parents=True)
    env_file = tmp_path / "repo" / ".env"
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    os.chmod(env_file, 0o600)
    return {"state": state_file, "gw": gw, "env_file": env_file, "environ": {}}


def run(box):
    return salvage_provider_keys(state_file=box["state"], gateway_dirs=[box["gw"]],
                                 env_file=box["env_file"], environ=box["environ"])


def write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def env_lines(box) -> dict[str, str]:
    out = {}
    for line in box["env_file"].read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        out[name] = value
    return out


def assert_no_key_values(caplog, report) -> None:
    text = caplog.text + repr(report)
    for k in ALL_KEYS:
        assert k not in text


def all_sources(box) -> None:
    write(box["state"], {"anthropic": {"api_key": K_STATE, "base_url": "https://api.anthropic.com"}})
    write(box["gw"] / "provider_keys.json", {"openai": [
        {"id": "1", "key_full": K_INACTIVE, "active": False},
        {"id": "2", "key_full": K_ACTIVE, "active": True},
    ]})
    write(box["gw"] / "providers.json", {"groq": {"api_key": K_GWPROV}, "_settings": {"active_model": "x"}})
    write(box["gw"] / "provider_notes.json", {"grok": f"my key is {K_NOTE} keep it safe"})


def test_keys_from_every_source_move_into_the_env_file_then_files_are_scrubbed(box, caplog):
    caplog.set_level(logging.INFO)
    all_sources(box)

    report = run(box)

    assert sorted(report["moved"]) == ["anthropic", "grok", "groq", "openai"]
    lines = env_lines(box)
    assert lines["ANTHROPIC_API_KEY"] == K_STATE          # the brain's own file
    assert lines["OPENAI_API_KEY"] == K_ACTIVE            # the ACTIVE dashboard key
    assert lines["GROQ_API_KEY"] == K_GWPROV              # the dashboard's providers.json
    assert lines["GROK_API_KEY"] == K_NOTE                # a key pasted into a note
    assert lines["EXISTING"] == "1"
    assert stat.S_IMODE(box["env_file"].stat().st_mode) == 0o600
    assert box["environ"]["OPENAI_API_KEY"] == K_ACTIVE   # hot-loaded for this run
    # Scrub: the brain's file keeps its settings, loses the key, is 0600.
    state = json.loads(box["state"].read_text())
    assert state == {"anthropic": {"base_url": "https://api.anthropic.com"}}
    assert stat.S_IMODE(box["state"].stat().st_mode) == 0o600
    for name in provider_salvage.GATEWAY_FILES:
        assert not (box["gw"] / name).exists()
    assert sorted(report["removed"]) == sorted(provider_salvage.GATEWAY_FILES)
    assert_no_key_values(caplog, report)


def test_env_wins_and_the_file_copy_is_dropped(box, caplog):
    caplog.set_level(logging.INFO)
    all_sources(box)
    box["environ"]["OPENAI_API_KEY"] = "sk-env-already"
    box["env_file"].write_text("EXISTING=1\nGROQ_API_KEY=sk-in-env-file\n", encoding="utf-8")

    report = run(box)

    assert sorted(report["dropped"]) == ["groq", "openai"]
    assert box["environ"]["OPENAI_API_KEY"] == "sk-env-already"
    lines = env_lines(box)
    assert "OPENAI_API_KEY" not in lines
    assert lines["GROQ_API_KEY"] == "sk-in-env-file"
    assert not (box["gw"] / "provider_keys.json").exists()
    assert "openai, groq" in caplog.text or "groq, openai" in caplog.text
    assert_no_key_values(caplog, report)


def test_a_failed_env_write_keeps_every_file_and_the_key_works_this_run(box, caplog):
    caplog.set_level(logging.INFO)
    all_sources(box)
    before = {p: p.read_bytes() for p in [box["state"], *[box["gw"] / n for n in provider_salvage.GATEWAY_FILES]]}

    with patch("windyfly.agent.capabilities.setup._atomic_upsert_env_var",
               side_effect=OSError("read-only")):
        report = run(box)

    assert sorted(report["failed"]) == ["anthropic", "grok", "groq", "openai"]
    for path, data in before.items():
        assert path.read_bytes() == data
    assert box["environ"]["OPENAI_API_KEY"] == K_ACTIVE
    assert box["environ"]["ANTHROPIC_API_KEY"] == K_STATE
    assert "retry at next start" in caplog.text
    assert_no_key_values(caplog, report)

    # Next start, with a working disk: it completes.
    box["environ"].clear()
    report = run(box)
    assert sorted(report["moved"]) == ["anthropic", "grok", "groq", "openai"]
    assert not (box["gw"] / "providers.json").exists()


def test_a_value_unsafe_for_an_env_line_is_not_written_and_nothing_is_scrubbed(box, caplog):
    write(box["gw"] / "providers.json", {"openai": {"api_key": "two words"}})
    report = run(box)
    assert report["failed"] == ["openai"]
    assert "OPENAI_API_KEY" not in env_lines(box)
    assert (box["gw"] / "providers.json").exists()
    assert box["environ"]["OPENAI_API_KEY"] == "two words"
    assert "two words" not in caplog.text


def test_a_custom_provider_keeps_its_definition_and_gets_a_sanitised_env_name(box, monkeypatch):
    write(box["gw"] / "providers.json", {"my-lab 2!": {
        "api_key": K_CUSTOM, "base_url": "https://lab.example/v1", "models": ["lab-1"], "name": "My Lab"}})
    write(box["state"], {"mistral": {"base_url": "https://kept.example"}})

    report = run(box)

    assert report["moved"] == ["my-lab 2!"]
    var = default_env_var("my-lab 2!")
    assert var == "WINDY_PROVIDER_MY_LAB_2_API_KEY"
    assert env_lines(box)[var] == K_CUSTOM
    state = json.loads(box["state"].read_text())
    assert state["my-lab 2!"] == {"name": "My Lab", "base_url": "https://lab.example/v1",
                                  "models": ["lab-1"], "type": "openai", "api_key_env": var}
    assert state["mistral"] == {"base_url": "https://kept.example"}
    # The brain finds the key through the env var.
    monkeypatch.setenv("WINDYFLY_PROVIDERS_PATH", str(box["state"]))
    monkeypatch.setenv(var, K_CUSTOM)
    prov = providers.get_provider_for_model("lab-1")
    assert prov["provider_key"] == "my-lab 2!" and prov["api_key"] == K_CUSTOM


def test_an_existing_entry_is_never_overwritten_by_the_old_dashboard(box):
    write(box["state"], {"lab": {"base_url": "https://mine.example", "api_key_env": "LAB_KEY"}})
    write(box["gw"] / "providers.json", {"lab": {"base_url": "https://old.example", "api_key": K_CUSTOM}})

    run(box)

    assert json.loads(box["state"].read_text()) == {"lab": {"base_url": "https://mine.example",
                                                            "api_key_env": "LAB_KEY"}}
    assert env_lines(box)["LAB_KEY"] == K_CUSTOM


@pytest.mark.parametrize("make_bad", ["symlink", "garbage"])
def test_a_file_it_cannot_check_leaves_everything_in_place(box, tmp_path, make_bad, caplog):
    write(box["gw"] / "provider_keys.json", {"openai": [{"key_full": K_ACTIVE, "active": True}]})
    if make_bad == "symlink":
        outside = tmp_path / "outside.json"
        write(outside, {"groq": {"api_key": K_GWPROV}})
        (box["gw"] / "providers.json").symlink_to(outside)
    else:
        (box["gw"] / "providers.json").write_text("{not json", encoding="utf-8")

    run(box)

    assert (box["gw"] / "provider_keys.json").exists()
    assert os.path.lexists(box["gw"] / "providers.json")
    if make_bad == "symlink":
        assert json.loads((tmp_path / "outside.json").read_text()) == {"groq": {"api_key": K_GWPROV}}
    assert "leaving every provider file as it is" in caplog.text


def test_nothing_to_do_is_a_quiet_no_op(box, caplog):
    caplog.set_level(logging.INFO)
    report = run(box)
    assert report == {"moved": [], "dropped": [], "failed": [], "removed": [], "left": []}
    assert not box["state"].exists()
    assert "provider salvage" not in caplog.text


def test_the_overrides_file_never_supplies_a_key(tmp_path, monkeypatch, caplog):
    path = tmp_path / "providers.json"
    write(path, {"openai": {"api_key": K_STATE, "base_url": "https://proxy.example/v1"}})
    monkeypatch.setenv("WINDYFLY_PROVIDERS_PATH", str(path))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    providers._warned_file_keys.clear()

    all_provs = providers.get_all_providers()
    providers.get_all_providers()

    assert all_provs["openai"]["base_url"] == "https://proxy.example/v1"
    assert all_provs["openai"]["api_key"] == ""
    assert caplog.text.count("the api_key in") == 1      # warned once, not per call
    assert K_STATE not in caplog.text


def test_main_runs_the_salvage_before_the_channel_starts():
    src = (Path(providers.__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    salvage = src.index("salvage_provider_keys()")
    assert src.index("install_root_redaction()") < salvage < src.index("_start_decay_scheduler(config")
