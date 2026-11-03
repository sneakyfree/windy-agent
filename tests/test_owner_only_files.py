"""Credential files are owner-only on EVERY platform (#687 F, the Windows credentials ACL; Agent owes it by Fri 11-13).

On Windows, os.chmod only toggles read-only (NTFS ACLs are the access control), and os.fchmod does not exist at all,
so the agent's key file inherited its folder's ACL (other Administrators could read it) and two writers would have
crashed. Now one helper (state_files.restrict_to_owner / ensure_private_dir) is used by every credential writer.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from windyfly import state_files as sf


@pytest.fixture
def windows(monkeypatch):
    """Pretend to be Windows for the ACL calls only; record every icacls command."""
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sf, "_on_windows", lambda: True)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("USERNAME", "robin")
    monkeypatch.setenv("USERDOMAIN", "LAPTOP")
    sf._private_dirs.clear()
    yield calls
    sf._private_dirs.clear()


def test_posix_files_are_0600(tmp_path):
    f = tmp_path / "secret"
    f.write_text("x")
    os.chmod(f, 0o644)
    assert sf.restrict_to_owner(f)
    assert stat.S_IMODE(f.stat().st_mode) == 0o600


def test_windows_drops_inherited_access_and_grants_only_the_account(tmp_path, windows):
    f = tmp_path / "secret"
    f.write_text("x")
    assert sf.restrict_to_owner(f)
    assert windows == [["icacls", str(f), "/inheritance:r", "/grant:r", "LAPTOP\\robin:F"]]


def test_a_private_dir_gets_an_inheritable_grant_once_per_process(tmp_path, windows):
    d = tmp_path / ".windy"
    sf.ensure_private_dir(d)
    sf.ensure_private_dir(d)
    assert windows == [["icacls", str(d), "/inheritance:r", "/grant:r", "LAPTOP\\robin:(OI)(CI)F"]]


def test_a_bare_username_is_used_when_there_is_no_domain(tmp_path, windows, monkeypatch):
    monkeypatch.delenv("USERDOMAIN")
    f = tmp_path / "s"
    f.write_text("x")
    sf.restrict_to_owner(f)
    assert windows[-1][-1] == "robin:F"


@pytest.mark.parametrize("failure", ["no_user", "exit_code", "exception"])
def test_a_failed_acl_is_logged_never_raised(tmp_path, monkeypatch, caplog, failure):
    monkeypatch.setattr(sf, "_on_windows", lambda: True)
    monkeypatch.setenv("USERNAME", "robin")
    if failure == "no_user":
        monkeypatch.delenv("USERNAME")
    elif failure == "exit_code":
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 5, "", "denied"))
    else:
        monkeypatch.setattr(subprocess, "run", MagicMock(side_effect=FileNotFoundError("icacls")))
    f = tmp_path / "s"
    f.write_text("x")
    assert sf.restrict_to_owner(f) is False
    assert "restrict" in caplog.text


# ── every credential writer restricts the temp file BEFORE the secret is in it ──────────────────────────────────────

def _spy(monkeypatch):
    seen: list[tuple[str, int]] = []
    real = sf.restrict_to_owner

    def spy(path):
        p = Path(path)
        seen.append((p.name, p.stat().st_size if p.exists() else -1))
        return real(p)

    monkeypatch.setattr(sf, "restrict_to_owner", spy)
    return seen


def _writers(tmp_path, monkeypatch):
    from windyfly import bring_home, hub_hatch, hub_login
    from windyfly.eternitas import agent_keys

    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    return {
        "agent_keys": lambda: agent_keys.save_credentials({"k": "SECRET"}, tmp_path / "state" / "credentials.json"),
        "bring_home": lambda: bring_home._atomic_write(tmp_path / "state" / "seal.json", b"SECRET"),
        "hub_login": lambda: hub_login._write_session({"access_token": "SECRET"}),
        "hub_hatch": lambda: hub_hatch._write_private_json(tmp_path / "state" / "cloud.json", {"t": "SECRET"}),
    }


@pytest.mark.parametrize("name", ["agent_keys", "bring_home", "hub_login", "hub_hatch"])
def test_each_credential_writer_restricts_the_empty_temp_file_first(tmp_path, monkeypatch, name):
    seen = _spy(monkeypatch)
    _writers(tmp_path, monkeypatch)[name]()
    assert seen, f"{name} never restricted its file"
    first_name, first_size = seen[0]
    assert first_size == 0, f"{name} restricted {first_name} only after the secret was written"


@pytest.mark.parametrize("name", ["agent_keys", "bring_home", "hub_login", "hub_hatch"])
def test_each_credential_writer_works_without_fchmod_as_on_windows(tmp_path, monkeypatch, name):
    monkeypatch.delattr(os, "fchmod", raising=False)
    _writers(tmp_path, monkeypatch)[name]()


@pytest.mark.parametrize("name", ["agent_keys", "bring_home", "hub_login", "hub_hatch"])
def test_each_credential_file_ends_up_0600(tmp_path, monkeypatch, name):
    _writers(tmp_path, monkeypatch)[name]()
    for f in (tmp_path / "state").rglob("*"):
        if f.is_file():
            assert stat.S_IMODE(f.stat().st_mode) == 0o600, f.name


# ── never rewrite a folder the agent does not own (Hub on #697) ─────────────────────────────────────────────────────

def _targets(calls):
    return [c[1] for c in calls]


def test_an_existing_foreign_folder_is_never_hardened_only_the_file(tmp_path, windows, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    documents = tmp_path / "Documents"
    documents.mkdir()                       # exists already, and is not the agent's
    target = documents / "creds.json"
    sf.write_private(target, b"SECRET")
    assert str(documents) not in _targets(windows)              # Documents' ACL is never touched
    assert any(t.startswith(str(documents / ".creds.json")) for t in _targets(windows))  # the file itself is


def test_the_default_state_root_is_hardened_once(tmp_path, windows, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("WINDY_STATE_DIR", str(state))
    sf.write_private(state / "a.json", b"1")
    sf.write_private(state / "b.json", b"2")
    assert _targets(windows).count(str(state)) == 1
    assert not any(".a.json" in t or ".b.json" in t for t in _targets(windows))  # inherited: no per-file icacls


def test_a_new_folder_under_the_state_root_is_hardened(tmp_path, windows, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setenv("WINDY_STATE_DIR", str(state))
    (state / "sub").mkdir()                 # existing, but under the agent's root
    sf.write_private(state / "sub" / "x.json", b"1")
    assert str(state / "sub") in _targets(windows)


def test_a_folder_this_call_creates_is_hardened(tmp_path, windows, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    fresh = tmp_path / "elsewhere" / "new-folder"   # did not exist: the agent made it
    sf.write_private(fresh / "x.json", b"1")
    assert str(fresh) in _targets(windows)
