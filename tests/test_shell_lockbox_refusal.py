"""shell.exec refuses a command that names the owner's lockbox, at every band and in both sandboxes (Boss, BOARD
2026-11-13T01:38:19Z), and the Docker sandbox masks the lockbox under a mounted root.

The command check is a FLOOR (a glob or a variable gets past a text match); the Docker mask is the wall for the
sandbox. Each refusal has a control: the same call with a plain name goes through.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.sandbox.blocklist import (
    LOCKBOX_REASON,
    BlockedCommand,
    check_blocklist,
    names_the_lockbox,
)
from windyfly.agent.capabilities.sandbox.docker import DockerDispatcher
from windyfly.agent.capabilities.shell import _shell_exec_handler


@pytest.fixture(autouse=True)
def _state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))


NAMING = [
    "cat ~/kit-army-config/ACCESS_LOCKBOX.md",
    "grep -i token /home/x/KIT-ARMY-CONFIG/shared/notes.md | head",
    "cd ~/kit-army-config && git log -1",
    "head -n 5 \"$HOME/.kit-army-config/ACCESS_LOCKBOX.md\"",
    "curl -s https://raw.githubusercontent.com/o/kit-army-config/main/ACCESS_LOCKBOX.md",
    "curl -s https://api.github.com/repos/o/kit-army-config/contents/README.md",
    "awk '{print}' docs/access_lockbox.md",
    "ls kit-army-config-wt;echo",
    "python3 -c 'open(\"ACCESS_LOCKBOX.md\").read()'",
    "tar czf /tmp/b.tgz kit-army-config.bak-1112",
    # the backup's bare mirror (Boss RULED 2026-11-13T01:41:13Z): git can decode its objects, so the shell must refuse it
    "git --git-dir=$HOME/.local/share/windy-backup/kit-army-config.mirror show HEAD:ACCESS_LOCKBOX.md",
    "git -C ~/.local/share/windy-backup/kit-army-config.mirror cat-file -p HEAD",
]

PLAIN = [
    "cat ~/windy-agent/README.md",
    "ls /home/x/my-kit-army-config",          # not the name: the matcher is a prefix of a path part
    "grep -r lockbox docs/",                   # the bare word is not the store's name
    "git log -1 --format=%h",
    "echo kit-army",
]


@pytest.mark.parametrize("command", NAMING)
def test_a_command_naming_the_lockbox_is_blocked(command: str) -> None:
    assert names_the_lockbox(command)
    with pytest.raises(BlockedCommand, match="lockbox"):
        check_blocklist(command)


@pytest.mark.parametrize("command", PLAIN)
def test_the_control_plain_commands_pass(command: str) -> None:
    assert not names_the_lockbox(command)
    check_blocklist(command)                   # no raise


@pytest.mark.parametrize("sandbox", ["docker", "host_rw"])
@pytest.mark.parametrize("band", [Band.SANDBOX, Band.USER, Band.TRUSTED, Band.OWNER, None])
def test_shell_exec_refuses_it_before_any_run_at_every_band(sandbox: str, band) -> None:
    """The refusal comes before the band gate and before Docker or the host shell: nothing runs."""
    class _NoRun:
        def run(self, *a, **k):  # pragma: no cover - reaching it is the failure
            raise AssertionError("the command ran")

    with pytest.raises(PermissionError, match="command blocked: .*lockbox"):
        _shell_exec_handler(command="cat ~/kit-army-config/ACCESS_LOCKBOX.md", sandbox=sandbox,
                            _allowed_roots=[], _dispatcher=_NoRun(), _band=band)


def test_the_refusal_says_why_and_what_to_do() -> None:
    assert "even for the owner" in LOCKBOX_REASON and "by name" in LOCKBOX_REASON


def test_host_rw_still_runs_a_plain_command_for_the_owner() -> None:
    """The control for host_rw: the same handler, band and sandbox run a command that names nothing."""
    out = _shell_exec_handler(command="echo plain-ok", sandbox="host_rw", _allowed_roots=[], _dispatcher=None,
                              _band=Band.OWNER)
    assert out["exit_code"] == 0 and "plain-ok" in str(out["stdout"])


def _home(tmp_path: Path) -> Path:
    home = tmp_path / "Home"
    (home / "kit-army-config" / "shared").mkdir(parents=True)
    (home / "kit-army-config" / "ACCESS_LOCKBOX.md").write_text("LOCKBOX-LINE\n", encoding="utf-8")
    (home / "docs").mkdir()
    (home / "docs" / "ACCESS_LOCKBOX.md").write_text("LOCKBOX-LINE\n", encoding="utf-8")
    (home / "projects").mkdir()
    (home / "projects" / "notes.txt").write_text("ok\n", encoding="utf-8")
    return home


def test_docker_masks_the_lockbox_folder_and_a_loose_copy(tmp_path: Path) -> None:
    home = _home(tmp_path)
    joined = "\n".join(DockerDispatcher().build_mounts([str(home)]))
    assert "/mnt/Home/kit-army-config:ro,nosuid,nodev,noexec,size=1k" in joined     # the folder: an empty tmpfs
    assert ":/mnt/Home/docs/ACCESS_LOCKBOX.md:ro" in joined                       # a loose copy: an empty file
    assert "/mnt/Home/projects:" not in joined and "/mnt/Home/docs:" not in joined  # the control: the rest stays


def test_docker_never_mounts_a_root_that_is_the_lockbox(tmp_path: Path) -> None:
    home = _home(tmp_path)
    assert DockerDispatcher().build_mounts([str(home / "kit-army-config")]) == []
    assert DockerDispatcher().build_mounts([str(home / "projects")])[:2] == [    # the control
        "-v", f"{(home / 'projects').resolve()}:/mnt/projects:ro"]
