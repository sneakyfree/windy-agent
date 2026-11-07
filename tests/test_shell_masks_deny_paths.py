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
