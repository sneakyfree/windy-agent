"""An export archive holds the whole memory in plain SQLite AND the .env secrets, so it is created 0600 (only
the owner's account can read it), whatever the umask. With the usual 0022 it used to come out 0644."""

from __future__ import annotations

import os
import stat
import tarfile
from argparse import Namespace

import pytest

from windyfly.memory.snapshot import private_tar

pytestmark = pytest.mark.skipif(os.name != "posix", reason="file modes are a POSIX statement")


@pytest.fixture
def open_umask():
    old = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(old)


def _mode(p) -> int:
    return stat.S_IMODE(os.stat(p).st_mode)


def test_a_new_archive_is_0600_and_a_real_tar_gz(tmp_path, open_umask):
    src = tmp_path / "SOUL.md"
    src.write_text("soul", encoding="utf-8")
    out = tmp_path / "x.tar.gz"
    with private_tar(out) as tar:
        tar.add(src, arcname="SOUL.md")
    assert _mode(out) == 0o600
    with tarfile.open(out) as tar:
        assert tar.getnames() == ["SOUL.md"]


def test_an_existing_readable_file_is_tightened_before_anything_is_written(tmp_path, open_umask):
    out = tmp_path / "x.tar.gz"
    out.write_bytes(b"old")
    os.chmod(out, 0o644)
    with private_tar(out) as tar:
        assert _mode(out) == 0o600  # already private while the archive is being written
        info = tarfile.TarInfo("a")
        tar.addfile(info)
    assert _mode(out) == 0o600


def test_a_symlink_in_the_archive_s_place_is_refused(tmp_path):
    target = tmp_path / "elsewhere"
    target.write_bytes(b"keep")
    link = tmp_path / "x.tar.gz"
    link.symlink_to(target)
    with pytest.raises(OSError):
        with private_tar(link):
            pass
    assert target.read_bytes() == b"keep"


@pytest.mark.asyncio
async def test_chat_export_is_0600(tmp_path, monkeypatch, open_umask):
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry

    install = tmp_path / "install"
    (install / "data").mkdir(parents=True)
    (install / ".env").write_text("SECRET=fixture_value\n", encoding="utf-8")
    (install / "SOUL.md").write_text("soul", encoding="utf-8")
    monkeypatch.setattr(core_mod, "PROJECT_ROOT", install)
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    monkeypatch.chdir(tmp_path)

    result = await registry.get("export").handler({})
    assert "Backup saved" in result, result
    (archive,) = tmp_path.glob("windyfly-backup-*.tar.gz")
    assert _mode(archive) == 0o600
    with tarfile.open(archive) as tar:
        assert ".env" in tar.getnames()  # what it holds is why it must be private


def test_cli_export_is_0600(tmp_path, monkeypatch, open_umask):
    from windyfly.commands import _legacy

    (tmp_path / ".env").write_text("SECRET=fixture_value\n", encoding="utf-8")
    monkeypatch.setattr(_legacy, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_legacy, "_get_db_path", lambda: tmp_path / "data" / "none.db")
    _legacy.cmd_export(Namespace())
    (archive,) = tmp_path.glob("windyfly-backup-*.tar.gz")
    assert _mode(archive) == 0o600


# ── archives written BEFORE exports were private (Hub on #683) ──
from windyfly.memory.snapshot import tighten_old_exports  # noqa: E402


def test_older_archives_are_tightened_and_left_unchanged(tmp_path):
    old = tmp_path / "windyfly-backup-20261001_120000.tar.gz"
    old.write_bytes(b"old archive")
    os.chmod(old, 0o644)
    other = tmp_path / "notes.tar.gz"
    other.write_bytes(b"not ours")
    os.chmod(other, 0o644)
    assert tighten_old_exports(tmp_path) == 1
    assert _mode(old) == 0o600 and old.read_bytes() == b"old archive"
    assert _mode(other) == 0o644  # only windyfly's own archive names


def test_an_already_private_archive_is_not_counted(tmp_path):
    p = tmp_path / "windyfly-backup-1.tar.gz"
    p.write_bytes(b"x")
    os.chmod(p, 0o600)
    assert tighten_old_exports(tmp_path) == 0


def test_a_symlink_named_like_an_archive_is_never_followed(tmp_path):
    target = tmp_path / "someone-elses-file"
    target.write_bytes(b"keep")
    os.chmod(target, 0o644)
    (tmp_path / "windyfly-backup-2.tar.gz").symlink_to(target)
    assert tighten_old_exports(tmp_path) == 0
    assert _mode(target) == 0o644


def test_a_fifo_named_like_an_archive_never_blocks_and_is_left_alone(tmp_path):
    """Hub on #686: /export scans the cwd, which may be shared; a planted FIFO must not hang the export."""
    import threading

    fifo = tmp_path / "windyfly-backup-3.tar.gz"
    os.mkfifo(fifo, 0o644)
    out: list[int] = []
    t = threading.Thread(target=lambda: out.append(tighten_old_exports(tmp_path)), daemon=True)
    t.start()
    t.join(5)
    assert not t.is_alive() and out == [0]
    assert _mode(fifo) == 0o644


def test_a_missing_folder_is_fine(tmp_path):
    assert tighten_old_exports(tmp_path / "nope") == 0


@pytest.mark.asyncio
async def test_chat_export_tightens_older_archives_in_both_places(tmp_path, monkeypatch, open_umask):
    from windyfly.commands import core as core_mod
    from windyfly.commands.registry import registry

    install = tmp_path / "install"
    (install / "data").mkdir(parents=True)
    old_root = install / "windyfly-backup-20260901_000000.tar.gz"
    old_cwd = tmp_path / "windyfly-backup-20260902_000000.tar.gz"
    for p in (old_root, old_cwd):
        p.write_bytes(b"old")
        os.chmod(p, 0o644)
    monkeypatch.setattr(core_mod, "PROJECT_ROOT", install)
    registry._commands.clear()
    registry._aliases.clear()
    core_mod._register_all()
    monkeypatch.chdir(tmp_path)
    assert "Backup saved" in await registry.get("export").handler({})
    assert _mode(old_root) == 0o600 and _mode(old_cwd) == 0o600


def test_cli_export_tightens_older_archives(tmp_path, monkeypatch, open_umask):
    from windyfly.commands import _legacy

    old = tmp_path / "windyfly-backup-20260901_000000.tar.gz"
    old.write_bytes(b"old")
    os.chmod(old, 0o644)
    (tmp_path / ".env").write_text("SECRET=fixture_value\n", encoding="utf-8")
    monkeypatch.setattr(_legacy, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(_legacy, "_get_db_path", lambda: tmp_path / "data" / "none.db")
    _legacy.cmd_export(Namespace())
    assert _mode(old) == 0o600


def test_a_fifo_is_never_even_opened(tmp_path, monkeypatch):
    """Layer 1: lstat decides first; a non-regular file is skipped before any open."""
    from windyfly.memory import snapshot

    os.mkfifo(tmp_path / "windyfly-backup-4.tar.gz")
    opened: list = []
    real_open = os.open
    monkeypatch.setattr(snapshot.os, "open", lambda *a, **k: opened.append(a) or real_open(*a, **k))
    assert tighten_old_exports(tmp_path) == 0 and opened == []


def test_the_open_is_always_nonblocking(tmp_path, monkeypatch):
    """Layer 2: even a regular file is opened O_NONBLOCK, so a swap to a FIFO after lstat can't hang."""
    from windyfly.memory import snapshot

    p = tmp_path / "windyfly-backup-5.tar.gz"
    p.write_bytes(b"x")
    os.chmod(p, 0o644)
    flags: list[int] = []
    real_open = os.open
    monkeypatch.setattr(snapshot.os, "open", lambda path, fl, *a: flags.append(fl) or real_open(path, fl, *a))
    assert tighten_old_exports(tmp_path) == 1
    assert flags and all(f & os.O_NONBLOCK and f & os.O_NOFOLLOW for f in flags)


def test_a_file_swapped_between_lstat_and_open_is_left_alone(tmp_path, monkeypatch):
    """Layer 3: the opened file must be the one lstat saw (same inode and device)."""
    from windyfly.memory import snapshot

    p = tmp_path / "windyfly-backup-6.tar.gz"
    p.write_bytes(b"x")
    os.chmod(p, 0o644)
    real_lstat = os.lstat

    def other_inode(path, *a, **k):
        st = real_lstat(path, *a, **k)
        return os.stat_result((st.st_mode, st.st_ino + 1) + tuple(st)[2:])

    monkeypatch.setattr(snapshot.os, "lstat", other_inode)
    assert tighten_old_exports(tmp_path) == 0 and _mode(p) == 0o644
