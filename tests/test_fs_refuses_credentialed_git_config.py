"""The file tools refuse a git config that carries a credential, with the deny list's own words, using the SAME check
the shell sandbox masks with (capabilities/git_secrets.py; Hub on #761: a separate PR, one shared check)."""

from __future__ import annotations

from pathlib import Path

import pytest

from windyfly.agent.capabilities.filesystem import (
    GIT_CREDENTIAL_NEEDLE,
    _edit_file_handler,
    _grep_files_handler,
    _move_file_handler,
    _read_file_handler,
    _resolve_and_check,
)

_TOKEN = "ghp_FAKEFAKEFAKEFAKE"
_CRED = f'[remote "origin"]\n\turl = https://{_TOKEN}@github.com/o/r.git\n'
_WORDS = f"under the always-deny list ({GIT_CREDENTIAL_NEEDLE!r})"


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    """Global git configs are read from HOME: a test's own, never the real one."""
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


def _git(root: Path, rel: str = "app/.git") -> Path:
    g = root / rel
    g.mkdir(parents=True)
    (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return g


@pytest.mark.parametrize("rel,name", [
    ("app/.git", "config"),                                  # a repo
    ("app/.git", "config.worktree"),
    ("mirror.git", "config"),                                # a bare or mirror clone
    ("app/.git/modules/lib", "config"),                      # a submodule
    ("app/.git/worktrees/feature", "config.worktree"),       # a linked worktree
])
def test_a_credentialed_git_config_is_refused_with_the_deny_words(tmp_path, rel, name):
    g = _git(tmp_path, rel)
    (g / name).write_text(_CRED, encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _resolve_and_check(str(g / name), [str(tmp_path)])
    assert _WORDS in str(e.value) and _TOKEN not in str(e.value)


def test_read_file_never_returns_the_token(tmp_path):
    g = _git(tmp_path)
    (g / "config").write_text(_CRED, encoding="utf-8")
    with pytest.raises(PermissionError):
        _read_file_handler(path=str(g / "config"), _allowed_roots=[str(tmp_path)])


@pytest.mark.parametrize("config", [
    '[remote "origin"]\n\turl = git@github.com:o/r.git\n',
    '[remote "origin"]\n\turl = https://github.com/o/r.git\n',
])
def test_a_plain_git_config_stays_readable(tmp_path, config):
    g = _git(tmp_path)
    (g / "config").write_text(config, encoding="utf-8")
    assert _resolve_and_check(str(g / "config"), [str(tmp_path)]) == (g / "config").resolve()


def test_a_config_without_a_head_beside_it_is_not_a_git_config(tmp_path):
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "config").write_text(_CRED, encoding="utf-8")
    assert _resolve_and_check(str(tmp_path / "notes" / "config"), [str(tmp_path)])


def test_a_symlinked_config_and_its_renamed_target_are_both_refused(tmp_path):
    g = _git(tmp_path)
    (g / "config.real").write_text(_CRED, encoding="utf-8")
    (g / "config").symlink_to(g / "config.real")
    for asked in ("config", "config.real"):
        with pytest.raises(PermissionError) as e:
            _resolve_and_check(str(g / asked), [str(tmp_path)])
        assert _WORDS in str(e.value)


def test_a_big_binary_file_in_a_git_folder_is_not_refused_for_its_size(tmp_path, monkeypatch):
    g = _git(tmp_path)
    (g / "index").write_bytes(b"\0" * 64)
    monkeypatch.setattr("windyfly.agent.capabilities.git_secrets.MAX_BYTES", 16)
    assert _resolve_and_check(str(g / "index"), [str(tmp_path)])


def test_a_git_config_too_big_to_check_is_refused(tmp_path, monkeypatch):
    g = _git(tmp_path)
    (g / "config").write_text("[core]\n" + "#" * 64, encoding="utf-8")
    monkeypatch.setattr("windyfly.agent.capabilities.git_secrets.MAX_BYTES", 16)
    with pytest.raises(PermissionError):
        _resolve_and_check(str(g / "config"), [str(tmp_path)])


def test_one_check_for_both_the_sandbox_and_the_file_tools():
    import inspect

    from windyfly.agent.capabilities import filesystem
    from windyfly.agent.capabilities.sandbox import docker

    for mod in (filesystem, docker):
        src = inspect.getsource(mod)
        assert "git_secrets" in src and "extraheader" not in src, mod.__name__


# ── User-level git configs and what they include (Boss on #761) ──────────────────────────────────────────────────────


@pytest.mark.parametrize("rel", [".gitconfig", ".gitconfig-work", ".config/git/config", "conf/work.gitconfig"])
def test_a_user_level_git_config_with_a_credential_is_refused(_own_home, rel):
    cfg = _own_home / rel
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(f'[url "https://{_TOKEN}@github.com/"]\n\tinsteadOf = https://github.com/\n', encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _resolve_and_check(str(cfg), [str(_own_home)])
    assert _WORDS in str(e.value)


def test_a_plain_user_level_git_config_stays_readable(_own_home):
    (_own_home / ".gitconfig").write_text("[user]\n\tname = A\n", encoding="utf-8")
    assert _resolve_and_check(str(_own_home / ".gitconfig"), [str(_own_home)])


@pytest.mark.parametrize("include", ["~/secrets/work.inc", "secrets/work.inc", '"~/secrets/work.inc"'])
def test_a_file_the_global_config_includes_is_checked_whatever_its_name(_own_home, include):
    (_own_home / ".gitconfig").write_text(
        f'[includeIf "gitdir:~/work/"]\n\tpath = {include}\n[user]\n\tname = A\n', encoding="utf-8")
    (_own_home / "secrets").mkdir()
    inc = _own_home / "secrets" / "work.inc"
    inc.write_text(_CRED, encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _resolve_and_check(str(inc), [str(_own_home)])
    assert _WORDS in str(e.value)
    inc.write_text("[user]\n\temail = a@b\n", encoding="utf-8")       # the same file, no credential: readable
    assert _resolve_and_check(str(inc), [str(_own_home)])


# ── Every tool that could show or move the content (Hub on #761) ─────────────────────────────────────────────────────


def _cred_repo(tmp_path: Path) -> Path:
    g = _git(tmp_path)
    (g / "config").write_text(_CRED, encoding="utf-8")
    (tmp_path / "app" / "README").write_text("hello ghp_ is a prefix\n", encoding="utf-8")
    return g / "config"


def test_grep_never_prints_the_token_line(tmp_path):
    _cred_repo(tmp_path)
    out = _grep_files_handler(pattern="ghp_", root=str(tmp_path), _allowed_roots=[str(tmp_path)])
    assert _TOKEN not in str(out)
    assert out["files_denied_by_allowlist"] >= 1
    assert any("README" in str(m) for m in out.get("matches", out.get("results", [])))   # grep still works


def test_grep_on_the_config_itself_is_refused(tmp_path):
    cfg = _cred_repo(tmp_path)
    try:
        out = _grep_files_handler(pattern="ghp_", root=str(cfg.parent), _allowed_roots=[str(tmp_path)])
    except PermissionError:
        return
    assert _TOKEN not in str(out)


@pytest.mark.parametrize("old", [_TOKEN, "not-in-the-file"])
def test_edit_is_no_yes_no_test_on_the_contents(tmp_path, old):
    """The same refusal whether or not old_string is in the file: no oracle."""
    cfg = _cred_repo(tmp_path)
    with pytest.raises(PermissionError) as e:
        _edit_file_handler(path=str(cfg), old_string=old, new_string="x", dry_run=True,
                           _allowed_roots=[str(tmp_path)], _journal_path=str(tmp_path / "j.ndjson"))
    assert _WORDS in str(e.value)
    assert cfg.read_text(encoding="utf-8") == _CRED


def test_move_never_lands_it_under_a_readable_name(tmp_path):
    cfg = _cred_repo(tmp_path)
    with pytest.raises(PermissionError):
        _move_file_handler(source=str(cfg), destination=str(tmp_path / "app" / "notes.txt"),
                           _allowed_roots=[str(tmp_path)], _journal_path=str(tmp_path / "j.ndjson"))
    assert cfg.read_text(encoding="utf-8") == _CRED and not (tmp_path / "app" / "notes.txt").exists()


def test_the_cache_knows_only_which_files_never_whether_they_hold_one(_own_home):
    """Hub on #761: a token written later into an include target that is already cached is still refused."""
    (_own_home / ".gitconfig").write_text("[include]\n\tpath = ~/work.inc\n", encoding="utf-8")
    inc = _own_home / "work.inc"
    inc.write_text("[user]\n\tname = A\n", encoding="utf-8")
    assert _resolve_and_check(str(inc), [str(_own_home)])                 # read once: the target list is now cached
    inc.write_text(_CRED, encoding="utf-8")                                # the global config is unchanged
    with pytest.raises(PermissionError) as e:
        _resolve_and_check(str(inc), [str(_own_home)])
    assert _WORDS in str(e.value)
