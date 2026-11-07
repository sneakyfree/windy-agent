"""No test may load a resident .env (fleet-caught 2026-07-18, OC5 Mac).

The autouse conftest guard must neutralize load_dotenv at the source AND
on every already-imported windyfly module alias, so a populated
repo-root .env on a standing checkout can never bleed real keys (or fire
a live API call) into the suite.
"""
from __future__ import annotations


def test_dotenv_source_is_neutralized():
    import dotenv
    # Under the autouse fixture, the source function is a no-op.
    assert dotenv.load_dotenv() is False


def test_config_holds_no_dotenv_alias():
    # config.py no longer calls dotenv itself: it goes through env_file.load_agent_env (Boss 2026-11-07T16:13:24Z).
    import windyfly.config as cfg
    assert not hasattr(cfg, "load_dotenv")


def test_env_file_alias_neutralized():
    # The ONE module that calls dotenv for the agent runtime; its alias is neutralized like any other.
    import windyfly.env_file as ef
    assert ef.load_dotenv() is False


def test_main_module_holds_no_dotenv_alias():
    # main.py once captured a MODULE-LEVEL alias at import time (the call site the first fix missed); it now loads its
    # env file through env_file.load_agent_env, so no alias is left to neutralize.
    import windyfly.main as m
    assert not hasattr(m, "load_dotenv")


def test_lazy_import_call_site_gets_noop():
    # A fresh `from dotenv import load_dotenv` (the lazy pattern in
    # cli.py / bridge / hatching) resolves to the patched source.
    from dotenv import load_dotenv
    assert load_dotenv() is False


def test_resident_env_file_not_read(tmp_path, monkeypatch):
    # Even with a real .env in CWD, nothing leaks.
    env = tmp_path / ".env"
    env.write_text("LEAKED_SENTINEL_KEY=sk-should-never-load\n")
    monkeypatch.chdir(tmp_path)
    import windyfly.env_file as ef
    ef.load_dotenv()  # neutralized
    ef.load_agent_env("windyfly.toml")  # the agent's loader: its dotenv call is neutralized too
    import os
    assert os.environ.get("LEAKED_SENTINEL_KEY") is None
