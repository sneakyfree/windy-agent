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
