"""The 11-13 known gaps after #762 (Boss BOARD 2026-11-08T03:23:47Z; Hub's conditions on the same question).

The file tools refuse ANY file whose text has a git-only section header at the start of a line AND a real credential
(the content rule), checked on the bytes the tool already read. The narrower rules stay: a hard link (nlink > 1), a
padded file directly in a git folder scanned whole, and includes of includes; the shell sandbox (which masks by path and
reads no ordinary file) gets the hard link and the nested includes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from windyfly.agent.capabilities import git_secrets
from windyfly.agent.capabilities.filesystem import (
    GIT_CREDENTIAL_NEEDLE,
    _edit_file_handler,
    _grep_files_handler,
    _move_file_handler,
    _read_file_handler,
    _resolve_and_check,
)
from windyfly.agent.capabilities.sandbox.docker import deny_masks

_TOKEN = "ghp_" + "Ab3dE6gH9jK2mN5pQ8sT1vW4yZ7bC0eF3hJ6"     # ghp_-shaped, fake
_README = f'Set the remote like this:\n\n[remote "origin"]\n\turl = https://{_TOKEN}@github.com/o/r.git\n'
_WORDS = f"under the always-deny list ({GIT_CREDENTIAL_NEEDLE!r})"


@pytest.fixture(autouse=True)
def _own_home(tmp_path, monkeypatch):
    home = tmp_path / "userhome"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    return home


def _git(root: Path, rel: str = "app/.git") -> Path:
    g = root / rel
    g.mkdir(parents=True)
    (g / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    return g


def _read(path: Path, root: Path) -> dict:
    return _read_file_handler(path=str(path), _allowed_roots=[str(root)])


# ── The content rule: what is refused, and that the refusal never prints the token (Boss conditions 2, 3) ───────────


def test_a_readme_showing_a_remote_with_a_real_looking_token_is_refused_and_never_printed(tmp_path):
    doc = tmp_path / "README.md"
    doc.write_text(_README, encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _read(doc, tmp_path)
    assert _WORDS in str(e.value)
    assert _TOKEN not in str(e.value) and "ghp_" not in str(e.value)


@pytest.mark.parametrize("text", [
    '[remote "origin"]\n\turl = https://github.com/o/r.git\n',                        # git header, plain https
    '[remote "origin"]\n\turl = ssh://git@github.com/o/r.git\n\tpushurl = git@h:o/r\n', # git header, ssh remotes
    f"Clone with https://{_TOKEN}@github.com/o/r.git (no git header here)\n",          # token, no git header
    f"We say [url in prose: https://{_TOKEN}@github.com/o/r\n",                       # not at the start of a line
])
def test_what_stays_readable(tmp_path, text):
    doc = tmp_path / "doc.md"
    doc.write_text(text, encoding="utf-8")
    assert _read(doc, tmp_path)["content"] == text


@pytest.mark.parametrize("url", [
    "https://<token>@github.com/o/r.git",
    "https://x-access-token:${GH_TOKEN}@github.com/o/r.git",
    "https://$GH_TOKEN@github.com/o/r.git",
    "https://me:YOUR_TOKEN@github.com/o/r.git",
    "https://xxxx@github.com/o/r.git",
])
def test_a_placeholder_is_not_a_credential(tmp_path, url):
    doc = tmp_path / "README.md"
    doc.write_text(f'[remote "origin"]\n\turl = {url}\n', encoding="utf-8")
    assert url in _read(doc, tmp_path)["content"]


def test_an_extraheader_with_a_real_value_is_refused_and_a_placeholder_is_not(tmp_path):
    doc = tmp_path / "notes.txt"
    doc.write_text("[http]\n\textraheader = AUTHORIZATION: bearer <token>\n", encoding="utf-8")
    assert _read(doc, tmp_path)["content"]
    doc.write_text("[http]\n\textraheader = AUTHORIZATION: basic eDpzZWNyZXQ=\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        _read(doc, tmp_path)


def test_a_file_a_repo_config_includes_is_refused_whatever_its_name_and_place(tmp_path):
    """The reverse lookup #762 left open: the file tools can't know which repo includes it, but its text tells."""
    g = _git(tmp_path)
    (g / "config").write_text("[include]\n\tpath = ../../shared/creds.txt\n", encoding="utf-8")
    inc = tmp_path / "shared" / "creds.txt"
    inc.parent.mkdir()
    inc.write_text(f'[url "https://{_TOKEN}@github.com/"]\n\tinsteadOf = https://github.com/\n', encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _read(inc, tmp_path)
    assert _WORDS in str(e.value)


def test_a_submodule_url_with_a_token_is_refused(tmp_path):
    doc = tmp_path / "modules.txt"
    doc.write_text(f'[submodule "lib"]\n\turl = https://{_TOKEN}@github.com/o/lib.git\n', encoding="utf-8")
    with pytest.raises(PermissionError):
        _read(doc, tmp_path)


# ── grep: one read, the check only on a file that matched (Boss condition 1; Hub condition 1) ───────────────────────


def test_grep_drops_a_matching_credentialed_file_and_still_finds_the_rest(tmp_path):
    (tmp_path / "README.md").write_text(_README, encoding="utf-8")
    (tmp_path / "notes.md").write_text("github.com is where the code lives\n", encoding="utf-8")
    out = _grep_files_handler(pattern="github", root=str(tmp_path), _allowed_roots=[str(tmp_path)])
    assert _TOKEN not in repr(out)
    assert [Path(m["path"]).name for m in out["matches"]] == ["notes.md"]
    assert out["files_denied_by_allowlist"] == 1


def test_grep_checks_only_a_file_that_matched_and_reads_each_file_once(tmp_path, monkeypatch):
    (tmp_path / "README.md").write_text(_README, encoding="utf-8")
    (tmp_path / "notes.md").write_text("hello\n", encoding="utf-8")
    checked: list[str] = []
    real = git_secrets.text_has_git_credential
    monkeypatch.setattr(git_secrets, "text_has_git_credential", lambda t: checked.append(t) or real(t))
    opened: list[str] = []
    real_open = open

    def counting_open(file, *a, **k):
        opened.append(str(file))
        return real_open(file, *a, **k)

    monkeypatch.setattr("builtins.open", counting_open)
    out = _grep_files_handler(pattern="hello", root=str(tmp_path), _allowed_roots=[str(tmp_path)])
    assert [Path(m["path"]).name for m in out["matches"]] == ["notes.md"]
    assert checked == ["hello\n"]                       # README did not match: not checked, and counted as scanned
    assert out["files_denied_by_allowlist"] == 0
    assert sorted(Path(p).name for p in opened) == ["README.md", "notes.md"]     # one open each


# ── edit and move ─────────────────────────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("old", [_TOKEN, "not in the file at all"])
def test_edit_refuses_the_same_way_whether_or_not_old_string_is_there(tmp_path, old):
    doc = tmp_path / "README.md"
    doc.write_text(_README, encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _edit_file_handler(path=str(doc), old_string=old, new_string="X", _allowed_roots=[str(tmp_path)],
                           _journal_path=str(tmp_path / "j.jsonl"))
    assert _WORDS in str(e.value) and _TOKEN not in str(e.value)
    assert doc.read_text(encoding="utf-8") == _README


def test_move_refuses_a_credentialed_file_by_the_streaming_check(tmp_path, monkeypatch):
    monkeypatch.setattr(git_secrets, "_CHUNK", 16)            # the token straddles chunk edges
    monkeypatch.setattr(git_secrets, "_OVERLAP", 80)
    doc = tmp_path / "README.md"
    doc.write_text(_README, encoding="utf-8")
    with pytest.raises(PermissionError) as e:
        _move_file_handler(source=str(doc), destination=str(tmp_path / "moved.md"), _allowed_roots=[str(tmp_path)],
                           _journal_path=str(tmp_path / "j.jsonl"))
    assert _WORDS in str(e.value)
    assert doc.exists() and not (tmp_path / "moved.md").exists()


def test_move_of_a_plain_file_still_works(tmp_path):
    doc = tmp_path / "a.md"
    doc.write_text('[remote "origin"]\n\turl = https://github.com/o/r\n', encoding="utf-8")
    _move_file_handler(source=str(doc), destination=str(tmp_path / "b.md"), _allowed_roots=[str(tmp_path)],
                       _journal_path=str(tmp_path / "j.jsonl"))
    assert (tmp_path / "b.md").exists()


# ── (a) hard links, (b) padding, (c) includes of includes ────────────────────────────────────────────────────────────


def test_a_hard_link_to_a_credential_with_no_git_header_is_refused_by_the_link_rule(tmp_path):
    """The content rule can't see this one (no header); the nlink > 1 backstop does."""
    src = tmp_path / "app" / "keep.txt"
    src.parent.mkdir()
    src.write_text(f"url = https://{_TOKEN}@github.com/o/r\n", encoding="utf-8")
    link = tmp_path / "notes.txt"
    os.link(src, link)
    with pytest.raises(PermissionError) as e:
        _resolve_and_check(str(link), [str(tmp_path)])
    assert _WORDS in str(e.value)
    plain = tmp_path / "plain.txt"
    plain.write_text("nothing here\n", encoding="utf-8")
    os.link(plain, tmp_path / "plain2.txt")
    assert _resolve_and_check(str(tmp_path / "plain2.txt"), [str(tmp_path)])


def test_a_padded_renamed_config_in_a_git_folder_is_scanned_whole(tmp_path, monkeypatch):
    monkeypatch.setattr(git_secrets, "MAX_BYTES", 64)
    g = _git(tmp_path)
    real = g / "config.real"
    real.write_text("#" * 500 + "\n" + f"\turl = https://{_TOKEN}@github.com/o/r\n", encoding="utf-8")
    with pytest.raises(PermissionError):
        _resolve_and_check(str(real), [str(tmp_path)])


def test_only_a_file_directly_in_a_git_folder_is_streamed_never_objects(tmp_path, monkeypatch):
    monkeypatch.setattr(git_secrets, "MAX_BYTES", 64)
    scanned: list[str] = []
    monkeypatch.setattr(git_secrets, "_scan", lambda p: scanned.append(p.name) or False)
    g = _git(tmp_path)
    (g / "objects" / "pack").mkdir(parents=True)
    pack = g / "objects" / "pack" / "pack-1.pack"
    pack.write_bytes(b"\x00" * 500)
    big = g / "index"
    big.write_bytes(b"\x00" * 500)
    assert _resolve_and_check(str(pack), [str(tmp_path)])
    assert _resolve_and_check(str(big), [str(tmp_path)])
    assert scanned == ["index"]


def test_includes_of_includes_are_followed_and_a_loop_ends(tmp_path, _own_home):
    (_own_home / ".gitconfig").write_text("[include]\n\tpath = ~/a.inc\n", encoding="utf-8")
    (_own_home / "a.inc").write_text("[include]\n\tpath = b.inc\n", encoding="utf-8")
    (_own_home / "b.inc").write_text(
        f"[include]\n\tpath = a.inc\n[lfs]\n\turl = https://{_TOKEN}@lfs.example/\n", encoding="utf-8")
    targets = git_secrets.global_include_targets()
    assert (_own_home / "b.inc").resolve() in targets
    with pytest.raises(PermissionError):
        _resolve_and_check(str(_own_home / "b.inc"), [str(_own_home)])


def test_a_new_nested_include_refreshes_the_cache(tmp_path, _own_home):
    (_own_home / ".gitconfig").write_text("[include]\n\tpath = ~/a.inc\n", encoding="utf-8")
    (_own_home / "a.inc").write_text("[user]\n\tname = A\n", encoding="utf-8")
    assert (_own_home / "c.inc").resolve() not in git_secrets.global_include_targets()
    st = (_own_home / "a.inc").stat()
    (_own_home / "a.inc").write_text("[include]\n\tpath = c.inc\n", encoding="utf-8")
    os.utime(_own_home / "a.inc", ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    assert (_own_home / "c.inc").resolve() in git_secrets.global_include_targets()


def test_include_depth_stops_where_git_stops(tmp_path):
    for i in range(12):
        (tmp_path / f"{i}.inc").write_text(f"[include]\n\tpath = {i + 1}.inc\n", encoding="utf-8")
    found = git_secrets.include_targets(tmp_path / "0.inc")
    assert len(found) == git_secrets.INCLUDE_DEPTH


# ── The sandbox keeps (a) and (c): it masks by path (Boss condition 5) ───────────────────────────────────────────────


def _masked(flags: list[str]) -> set[str]:
    return {f.split(":")[1] for f in flags if f.count(":") >= 2}


def test_the_sandbox_masks_a_hard_link_to_a_credentialed_config_even_walked_first(tmp_path):
    root = tmp_path / "Home"
    g = _git(root, "zzz/.git")
    (g / "config").write_text(f'[remote "origin"]\n\turl = https://{_TOKEN}@github.com/o/r\n', encoding="utf-8")
    os.link(g / "config", root / "aaa.txt")                 # walked before the repo's .git
    (root / "plain.txt").write_text("ok\n", encoding="utf-8")
    os.link(root / "plain.txt", root / "plain-link.txt")
    masked = _masked(deny_masks(root, "Home") or [])
    assert "/mnt/Home/aaa.txt" in masked and "/mnt/Home/zzz/.git/config" in masked
    assert "/mnt/Home/plain-link.txt" not in masked


def test_the_sandbox_masks_a_repo_configs_nested_includes(tmp_path):
    root = tmp_path / "Home"
    g = _git(root, "app/.git")
    (g / "config").write_text("[include]\n\tpath = ../../one.inc\n", encoding="utf-8")
    (root / "one.inc").write_text("[include]\n\tpath = two.inc\n", encoding="utf-8")
    (root / "two.inc").write_text(f"[lfs]\n\turl = https://{_TOKEN}@lfs.example/\n", encoding="utf-8")
    masked = _masked(deny_masks(root, "Home") or [])
    assert "/mnt/Home/two.inc" in masked and "/mnt/Home/one.inc" not in masked
