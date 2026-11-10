"""Boss's hardening list, item 'shell.exec roots': inside the shell sandbox, every always-deny path under a mounted root
is masked (folder = empty read-only tmpfs, file = /dev/null), and host_rw takes the turn's real band."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from windyfly.agent.capabilities.filesystem import _ALWAYS_DENY, _resolve_and_check
from windyfly.agent.capabilities.sandbox.docker import DockerDispatcher


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "Home"
    for d in (".ssh", ".claude", ".config/gh", "Library/Application Support/Google/Chrome", "projects"):
        (home / d).mkdir(parents=True)
    (home / ".ssh" / "id_test").write_text("SECRET-KEY-MATERIAL\n", encoding="utf-8")
    (home / ".netrc").write_text("machine x login y password z\n", encoding="utf-8")
    (home / "projects" / "notes.txt").write_text("ok\n", encoding="utf-8")
    return home


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))


def _empty() -> str:
    from windyfly.agent.capabilities.sandbox.docker import _empty_mask_file
    return str(_empty_mask_file())


def test_every_deny_path_under_the_root_is_masked(tmp_path):
    home = _home(tmp_path)
    flags = DockerDispatcher().build_mounts([str(home)])
    assert flags[:2] == ["-v", f"{home.resolve()}:/mnt/Home:ro"]
    joined = "\n".join(flags)
    for d in (".ssh", ".claude", ".config/gh", "Library/Application Support/Google/Chrome"):
        assert f"/mnt/Home/{d}:ro,nosuid,nodev,noexec,size=1k" in joined, d
    assert f"{_empty()}:/mnt/Home/.netrc:ro" in joined and "/dev/null" not in joined
    assert "/mnt/Home/projects:" not in joined           # ordinary folders stay visible


def test_deny_matches_at_any_depth_and_inside_a_config_root(tmp_path):
    home = _home(tmp_path)
    app = home / "projects" / "app"
    app.mkdir()
    (app / ".env").write_text("K=v\n", encoding="utf-8")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/projects/app/.env:ro" in joined
    cfg = "\n".join(DockerDispatcher().build_mounts([str(home / ".config")]))   # the root IS ~/.config
    assert "/mnt/.config/gh:ro," in cfg


def test_a_root_holding_several_homes_masks_each_one(tmp_path):
    base = tmp_path / "home"
    for user in ("ann", "bob"):
        (base / user / ".ssh").mkdir(parents=True)
    joined = "\n".join(DockerDispatcher().build_mounts([str(base)]))
    assert "/mnt/home/ann/.ssh:ro," in joined and "/mnt/home/bob/.ssh:ro," in joined


def test_a_deny_symlink_masks_its_real_target_inside_the_root_and_the_link(tmp_path):
    home = tmp_path / "Home"
    (home / "dot" / "ssh").mkdir(parents=True)
    (home / ".ssh").symlink_to(home / "dot" / "ssh")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert "/mnt/Home/dot/ssh:ro," in joined and "/mnt/Home/.ssh:ro," in joined


def test_a_deny_symlink_out_of_the_root_masks_the_link_path(tmp_path):
    outside = tmp_path / "elsewhere" / "keys"
    outside.mkdir(parents=True)
    home = tmp_path / "Home"
    home.mkdir()
    (home / ".ssh").symlink_to(outside)
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert "/mnt/Home/.ssh:ro," in joined and "elsewhere" not in joined


def test_a_root_too_large_to_check_is_not_mounted(tmp_path, monkeypatch):
    import windyfly.agent.capabilities.sandbox.docker as dk
    home = _home(tmp_path)
    monkeypatch.setattr(dk, "_MASK_WALK_MAX_ENTRIES", 3)
    assert DockerDispatcher().build_mounts([str(home)]) == []


def test_case_folding_on_case_insensitive_systems(tmp_path, monkeypatch):
    import windyfly.agent.capabilities.filesystem as fs
    monkeypatch.setattr(fs, "_DENY_CASE_FOLD", True)
    assert fs.deny_match("/Users/x/.SSH/id") == ".ssh"
    assert fs.deny_match("/Users/x/library/keychains/login.keychain") == "Library/Keychains"
    monkeypatch.setattr(fs, "_DENY_CASE_FOLD", False)
    assert fs.deny_match("/home/x/.SSH/id") is None


def test_the_new_deny_entries_close_the_file_tools_too(tmp_path):
    home = _home(tmp_path)
    for rel in (".claude", ".config/gh", "Library/Application Support/Google/Chrome"):
        with pytest.raises(PermissionError):
            _resolve_and_check(str(home / rel), [str(home)])
    assert {".claude", ".secrets", ".config/gh", ".mozilla", ".npmrc", ".pypirc", ".git-credentials",
            ".config/gcloud", ".local/share/keyrings", ".password-store", "Library/Cookies"} <= set(_ALWAYS_DENY)


def test_host_rw_uses_the_turn_band_and_refuses_an_unknown_one():
    from windyfly.agent.capabilities.shell import _shell_exec_handler
    from windyfly.agent.capabilities.descriptor import Band
    for band in (None, Band.TRUSTED):
        with pytest.raises(PermissionError, match="host_rw requires OWNER"):
            _shell_exec_handler(command="true", sandbox="host_rw", _allowed_roots=[], _dispatcher=None, _band=band)


def test_the_registered_shell_passes_the_real_band():
    src = (Path(__file__).resolve().parents[1] / "src/windyfly/agent/capabilities/shell.py").read_text(encoding="utf-8")
    assert "_band=current_band()" in src and "_band=Band.OWNER" not in src


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")
def test_live_container_cannot_read_a_masked_folder(tmp_path):
    """A real container: the mounted root is readable, the masked .ssh is empty, the masked .netrc is empty."""
    home = _home(tmp_path)
    flags = DockerDispatcher().build_mounts([str(home)])
    try:
        out = subprocess.run(["docker", "run", "--rm", "--network=none", *flags, "alpine:3",
                              "sh", "-c", "cat /mnt/Home/projects/notes.txt; ls -A /mnt/Home/.ssh | wc -l; "
                                          "wc -c < /mnt/Home/.netrc; cat /mnt/Home/.ssh/id_test 2>&1 || true"],
                             capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.skip("docker too slow here")
    if out.returncode != 0 and "Cannot connect" in out.stderr:
        pytest.skip("docker daemon not reachable")
    lines = out.stdout.split()
    assert lines[:3] == ["ok", "0", "0"], out.stdout + out.stderr
    assert "SECRET-KEY-MATERIAL" not in out.stdout


# ── A repo's git config with a credential in it (Hub's note on #755) ─────────────────────────────────────────────────
# The walk never enters .git (it can be huge); its config files are checked on their own instead.

_TOKEN = "ghp_FAKEFAKEFAKEFAKE"


def _repo(home: Path, name: str, config: str) -> Path:
    git = home / "projects" / name / ".git"
    git.mkdir(parents=True)
    (git / "config").write_text(config, encoding="utf-8")
    (git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return git


@pytest.mark.parametrize("config", [
    f'[remote "origin"]\n\turl = https://{_TOKEN}@github.com/o/r.git\n',                  # a token as the user part
    f'[remote "origin"]\n\turl = https://me:{_TOKEN}@example.com/r.git\n',                # user:password
    f'[remote "origin"]\n\tURL = ssh://me:{_TOKEN}@example.com/r.git\n',                  # user:password, any scheme
    f'[url "https://{_TOKEN}@github.com/"]\n\tinsteadOf = https://github.com/\n',         # in a section header
    f'[http]\n\textraheader = AUTHORIZATION: basic {_TOKEN}\n',                           # an auth header
    f'[http "https://example.com"]\n\textraHeader = Authorization: Bearer {_TOKEN}\n',
])
def test_a_repo_config_with_a_credential_is_masked(tmp_path, config):
    home = _home(tmp_path)
    _repo(home, "app", config)
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/projects/app/.git/config:ro" in joined
    assert "/mnt/Home/projects/app/.git/HEAD" not in joined            # the rest of the repo stays as it was


@pytest.mark.parametrize("config", [
    '[remote "origin"]\n\turl = git@github.com:o/r.git\n',
    '[remote "origin"]\n\turl = ssh://git@github.com/o/r.git\n',
    '[remote "origin"]\n\turl = https://github.com/o/r.git\n',
    '[core]\n\tbare = false\n',
])
def test_a_repo_config_without_a_credential_stays_readable(tmp_path, config):
    home = _home(tmp_path)
    _repo(home, "app", config)
    assert "/.git/config" not in "\n".join(DockerDispatcher().build_mounts([str(home)]))


def test_a_submodule_config_with_a_credential_is_masked(tmp_path):
    home = _home(tmp_path)
    git = _repo(home, "app", '[core]\n\tbare = false\n')
    (git / "modules" / "lib").mkdir(parents=True)
    (git / "modules" / "lib" / "config").write_text(f"[remote \"o\"]\n\turl = https://{_TOKEN}@h/x\n", encoding="utf-8")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/projects/app/.git/modules/lib/config:ro" in joined
    assert "/mnt/Home/projects/app/.git/config:" not in joined


def test_a_repo_config_that_cannot_be_read_whole_is_masked(tmp_path, monkeypatch):
    home = _home(tmp_path)
    _repo(home, "app", "[core]\n\tbare = false\n" + "#" * 64)
    monkeypatch.setattr("windyfly.agent.capabilities.git_secrets.MAX_BYTES", 16)
    assert f"{_empty()}:/mnt/Home/projects/app/.git/config:ro" in "\n".join(DockerDispatcher().build_mounts([str(home)]))


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")
def test_live_container_cannot_read_a_credentialed_repo_config(tmp_path):
    home = _home(tmp_path)
    _repo(home, "app", f'[remote "origin"]\n\turl = https://{_TOKEN}@github.com/o/r.git\n')
    flags = DockerDispatcher().build_mounts([str(home)])
    try:
        out = subprocess.run(["docker", "run", "--rm", "--network=none", *flags, "alpine:3", "sh", "-c",
                              "wc -c < /mnt/Home/projects/app/.git/config; cat /mnt/Home/projects/app/.git/HEAD"],
                             capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.skip("docker too slow here")
    if out.returncode != 0 and "Cannot connect" in out.stderr:
        pytest.skip("docker daemon not reachable")
    assert out.stdout.split()[:3] == ["0", "ref:", "refs/heads/main"], out.stdout + out.stderr
    assert _TOKEN not in out.stdout


# ── Hub's three cases on #761 ────────────────────────────────────────────────────────────────────────────────────────

_CRED_CFG = f'[remote "origin"]\n\turl = https://{_TOKEN}@github.com/o/r.git\n'


def test_a_bare_or_mirror_clone_config_is_masked(tmp_path):
    """name.git/config next to a HEAD: the walk enters a bare clone like any folder, and checks its config."""
    home = _home(tmp_path)
    bare = home / "projects" / "mirror.git"
    bare.mkdir()
    (bare / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (bare / "config").write_text("[core]\n\tbare = true\n" + _CRED_CFG, encoding="utf-8")
    plain = home / "projects" / "other"            # a file named config with no HEAD beside it is not a git config
    plain.mkdir()
    (plain / "config").write_text(_CRED_CFG, encoding="utf-8")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/projects/mirror.git/config:ro" in joined
    assert "/mnt/Home/projects/other/config" not in joined


def test_a_symlinked_repo_config_masks_the_link_and_its_target(tmp_path):
    home = _home(tmp_path)
    git = _repo(home, "app", "")
    (git / "config").unlink()
    (git / "config.real").write_text(_CRED_CFG, encoding="utf-8")
    (git / "config").symlink_to(git / "config.real")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/projects/app/.git/config:ro" in joined
    assert f"{_empty()}:/mnt/Home/projects/app/.git/config.real:ro" in joined


def test_a_symlinked_submodule_folder_masks_the_link_and_checks_its_target(tmp_path):
    home = _home(tmp_path)
    git = _repo(home, "app", "[core]\n\tbare = false\n")
    real = home / "projects" / "lib-gitdir"
    real.mkdir()
    (real / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (real / "config").write_text(_CRED_CFG, encoding="utf-8")
    (git / "modules").mkdir()
    (git / "modules" / "lib").symlink_to(real, target_is_directory=True)
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert "/mnt/Home/projects/app/.git/modules/lib:ro,nosuid,nodev,noexec,size=1k" in joined
    assert f"{_empty()}:/mnt/Home/projects/lib-gitdir/config:ro" in joined
    assert joined.count("/mnt/Home/projects/lib-gitdir/config:") == 1        # once, though two routes reach it


@pytest.mark.parametrize("where", ["config.worktree", "worktrees/feature/config.worktree"])
def test_worktree_configs_are_checked(tmp_path, where):
    home = _home(tmp_path)
    git = _repo(home, "app", "[core]\n\tbare = false\n")
    cfg = git / where
    cfg.parent.mkdir(parents=True, exist_ok=True)
    (cfg.parent / "HEAD").write_text("ref: refs/heads/feature\n", encoding="utf-8")
    cfg.write_text(_CRED_CFG, encoding="utf-8")
    assert f"{_empty()}:/mnt/Home/projects/app/.git/{where}:ro" in "\n".join(DockerDispatcher().build_mounts([str(home)]))


def test_the_shared_check_is_the_one_the_sandbox_uses():
    """One check for the sandbox and (next) the file tools: docker.py keeps no copy of its own."""
    import inspect

    from windyfly.agent.capabilities.sandbox import docker

    src = inspect.getsource(docker)
    assert "git_secrets.has_credential" in src and "extraheader" not in src


@pytest.mark.skipif(shutil.which("docker") is None, reason="needs docker")
def test_live_container_starts_and_hides_symlinked_git_configs(tmp_path):
    """Masks over symlink paths must not stop the container from starting, and the token never shows."""
    home = _home(tmp_path)
    git = _repo(home, "app", "")
    (git / "config").unlink()
    (git / "config.real").write_text(_CRED_CFG, encoding="utf-8")
    (git / "config").symlink_to(git / "config.real")
    flags = DockerDispatcher().build_mounts([str(home)])
    try:
        out = subprocess.run(["docker", "run", "--rm", "--network=none", *flags, "alpine:3", "sh", "-c",
                              "wc -c < /mnt/Home/projects/app/.git/config.real; cat /mnt/Home/projects/app/.git/config"
                              " 2>/dev/null; cat /mnt/Home/projects/notes.txt"],
                             capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        pytest.skip("docker too slow here")
    if out.returncode != 0 and "Cannot connect" in out.stderr:
        pytest.skip("docker daemon not reachable")
    assert out.stdout.split()[:2] == ["0", "ok"], out.stdout + out.stderr
    assert _TOKEN not in out.stdout


def test_user_level_git_configs_and_their_includes_are_masked(tmp_path, monkeypatch):
    """The same shared check: ~/.gitconfig (and its variants) and a file it includes are masked when they carry one."""
    home = _home(tmp_path)
    monkeypatch.setenv("HOME", str(home))
    (home / ".gitconfig").write_text(f"[include]\n\tpath = ~/work.inc\n{_CRED_CFG}", encoding="utf-8")
    (home / "work.inc").write_text(_CRED_CFG, encoding="utf-8")
    (home / ".gitconfig-plain").write_text("[user]\n\tname = A\n", encoding="utf-8")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/.gitconfig:ro" in joined
    assert f"{_empty()}:/mnt/Home/work.inc:ro" in joined
    assert "/mnt/Home/.gitconfig-plain" not in joined


def test_a_file_a_repo_config_includes_is_masked_when_it_carries_one(tmp_path):
    home = _home(tmp_path)
    _repo(home, "app", "[include]\n\tpath = ../../secret-remotes.inc\n[core]\n\tbare = false\n")
    (home / "projects" / "secret-remotes.inc").write_text(_CRED_CFG, encoding="utf-8")
    _repo(home, "other", "[include]\n\tpath = ../../plain.inc\n")
    (home / "projects" / "plain.inc").write_text("[user]\n\tname = A\n", encoding="utf-8")
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert f"{_empty()}:/mnt/Home/projects/secret-remotes.inc:ro" in joined
    assert "/mnt/Home/projects/plain.inc" not in joined
    assert "/mnt/Home/projects/app/.git/config:" not in joined          # the config itself holds no credential
