"""The owner's lockbox is refused at EVERY band, the owner's own turn included (Hub + Boss, BOARD 2026-11-13).

Its repo, any worktree or copy named after it, and its file: the file tools, the github tools and the sandbox's
deny masks all refuse it by name. Secrets are fetched by name by the people and tools that hold them, never into a
chat, so no soul text and no band opens it.
"""

from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import pytest

from windyfly.agent.capabilities import filesystem as fs
from windyfly.agent.capabilities.github import (
    _create_issue_handler,
    _fetch_file_handler,
    _list_repo_handler,
    _put_file_handler,
)
from windyfly.tools.bands import turn_band

ALL_BANDS = [0, 1, 2, 3, None]
# A port nothing listens on: a refusal must come BEFORE any network call, so a test that reached it would fail loudly.
_NOWHERE = "http://127.0.0.1:9"


@pytest.mark.parametrize("name", [
    "kit-army-config", "KIT-ARMY-CONFIG", "kit-army-config-wt", ".kit-army-config", "kit-army-config.bak-1112",
    "ACCESS_LOCKBOX.md", "access_lockbox.md", "ACCESS_LOCKBOX", "ACCESS_LOCKBOX.md.bak",
])
def test_every_spelling_of_the_lockbox_is_its_name(name: str) -> None:
    assert fs.is_lockbox_name(name)
    assert fs.deny_match(f"/home/x/{name}") == fs.LOCKBOX_NEEDLE
    assert fs.deny_match(f"/home/x/{name}/shared/notes.md") == fs.LOCKBOX_NEEDLE


@pytest.mark.parametrize("path", [
    "/home/x/kit-army", "/home/x/army-config", "/home/x/my-kit-army-config", "/home/x/LOCKBOX.md",
    "/home/x/windy-agent/README.md", "/home/x/notes/access.md",
])
def test_other_names_are_not_the_lockbox(path: str) -> None:
    assert fs.deny_match(path) is None
    assert not fs.lockbox_match(path)


def _lockbox_tree(tmp_path: Path) -> Path:
    box = tmp_path / "kit-army-config"
    (box / "shared").mkdir(parents=True)
    (box / "ACCESS_LOCKBOX.md").write_text("LOCKBOX-LINE-1\n", encoding="utf-8")
    (box / "shared" / "notes.md").write_text("LOCKBOX-LINE-2\n", encoding="utf-8")
    loose = tmp_path / "docs"
    loose.mkdir()
    (loose / "ACCESS_LOCKBOX.md").write_text("LOCKBOX-LINE-3\n", encoding="utf-8")
    (loose / "plain.md").write_text("plain words\n", encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("band", ALL_BANDS)
def test_fs_read_refuses_the_lockbox_at_every_band(tmp_path: Path, band) -> None:
    root = _lockbox_tree(tmp_path)
    with (turn_band(band) if band is not None else nullcontext()):
        for p in (root / "kit-army-config" / "ACCESS_LOCKBOX.md",
                  root / "kit-army-config" / "shared" / "notes.md",
                  root / "docs" / "ACCESS_LOCKBOX.md"):
            with pytest.raises(PermissionError, match="the lockbox"):
                fs._read_file_handler(path=str(p), _allowed_roots=[str(root)])
        with pytest.raises(PermissionError, match="the lockbox"):
            fs._list_directory_handler(path=str(root / "kit-army-config"), _allowed_roots=[str(root)])


def test_fs_read_refuses_a_link_into_the_lockbox(tmp_path: Path) -> None:
    root = _lockbox_tree(tmp_path)
    link = root / "docs" / "harmless.md"
    link.symlink_to(root / "kit-army-config" / "ACCESS_LOCKBOX.md")
    with turn_band(3):
        with pytest.raises(PermissionError, match="the lockbox"):
            fs._read_file_handler(path=str(link), _allowed_roots=[str(root)])


def test_fs_still_reads_a_plain_file_beside_it(tmp_path: Path) -> None:
    """The control: the same tool, the same roots and band, opens a file that is not the lockbox's."""
    root = _lockbox_tree(tmp_path)
    with turn_band(3):
        out = fs._read_file_handler(path=str(root / "docs" / "plain.md"), _allowed_roots=[str(root)])
    assert "plain words" in str(out)


def test_fs_grep_never_shows_lockbox_lines_to_the_owner(tmp_path: Path) -> None:
    root = _lockbox_tree(tmp_path)
    with turn_band(3):
        out = fs._grep_files_handler(pattern="LOCKBOX-LINE|plain", root=str(root), _allowed_roots=[str(root)])
    shown = str(out["matches"])
    assert "LOCKBOX-LINE" not in shown and "kit-army-config" not in shown and "ACCESS_LOCKBOX" not in shown
    assert "plain words" in shown
    assert out["files_denied_by_allowlist"] == 3   # the lockbox's three files, refused, never scanned


@pytest.mark.parametrize("band", ALL_BANDS)
def test_github_tools_refuse_the_lockbox_repo_at_every_band(band) -> None:
    common = dict(base_url=_NOWHERE, token="ghp_test", allowed_owners=None)
    with (turn_band(band) if band is not None else nullcontext()):
        outs = [
            _fetch_file_handler(owner="o", repo="kit-army-config", path="ACCESS_LOCKBOX.md", **common),
            _fetch_file_handler(owner="o", repo="Kit-Army-Config", path="README.md", **common),
            _fetch_file_handler(owner="o", repo="other", path="vault/ACCESS_LOCKBOX.md", **common),
            _list_repo_handler(owner="o", repo="kit-army-config", path="", **common),
            _list_repo_handler(owner="o", repo="other", path="kit-army-config/shared", **common),
            _put_file_handler(owner="o", repo="kit-army-config", path="x.md", content="c", message="m", **common),
            _create_issue_handler(owner="o", repo="kit-army-config", title="t", body="b", labels=None, **common),
        ]
    for out in outs:
        assert "owner's credentials" in out.get("error", ""), out


def test_github_refusal_comes_before_the_owner_allowlist_passes() -> None:
    """An allowlist that names the owner does not open the lockbox repo."""
    out = _fetch_file_handler(owner="o", repo="kit-army-config", path="README.md",
                              base_url=_NOWHERE, token="ghp_test", allowed_owners=["o"])
    assert "owner's credentials" in out.get("error", "")


def test_github_still_reaches_another_repo() -> None:
    """The control: the same call for another repo is NOT refused by this rule (it fails later, on the network)."""
    out = _fetch_file_handler(owner="o", repo="windy-agent", path="README.md",
                              base_url=_NOWHERE, token="ghp_test", allowed_owners=None)
    assert "owner's credentials" not in out.get("error", "")
    assert "network error" in out.get("error", "")
