"""Soul-import intake (Hub's review A4/A5): hostile zips are refused, only parser members are copied."""

from __future__ import annotations

import io
import os
import stat
import time
import zipfile

import pytest

from windyfly.soul_import import intake as ik


@pytest.fixture(autouse=True)
def _intake_in_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(ik, "_base_dir", lambda: tmp_path)


def _zip(entries: dict[str, bytes], *, symlink: str | None = None) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        if symlink:
            info = zipfile.ZipInfo(symlink)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, "/etc/passwd")
    buf.seek(0)
    return buf


def test_a_bare_conversations_json_is_taken():
    got = ik.receive(io.BytesIO(b"[]"), filename="conversations.json")
    assert sorted(p.name for p in got.dir.iterdir()) == ["conversations.json"]
    assert oct(got.dir.parent.stat().st_mode & 0o777) == "0o700"


def test_only_parser_members_are_copied_and_a_common_root_is_stripped():
    got = ik.receive(_zip({
        "export/SOUL.md": b"# soul", "export/MEMORY.md": b"- fact", "export/config.yaml": b"a: 1",
        "export/skills/deploy.md": b"steps", "export/photos/cat.jpg": b"\xff\xd8", "export/sessions.db": b"SQLite",
        "export/skills/.hidden.md": b"x", "export/skills/nested/deep.md": b"x",
    }), filename="export.zip")
    files = sorted(str(p.relative_to(got.dir)) for p in got.dir.rglob("*") if p.is_file())
    assert files == ["MEMORY.md", "SOUL.md", "config.yaml", "skills/deploy.md"]


@pytest.mark.parametrize("name", ["../../evil.md", "/etc/SOUL.md", "C:\\x\\SOUL.md", "a\\..\\SOUL.md"])
def test_paths_that_escape_are_refused(name):
    with pytest.raises(ik.IntakeError):
        ik.receive(_zip({name: b"x", "SOUL.md": b"ok"}), filename="x.zip")


def test_a_nul_in_a_name_is_refused():
    buf = _zip({"SOUL.md": b"ok", "MEMORY.mdXX": b"x"})
    raw = buf.getvalue().replace(b"MEMORY.mdXX", b"MEMORY.md\x00X")
    with pytest.raises(ik.IntakeError):
        ik.receive(io.BytesIO(raw), filename="x.zip")


def test_a_symlink_entry_is_refused():
    with pytest.raises(ik.IntakeError, match="link"):
        ik.receive(_zip({"SOUL.md": b"ok"}, symlink="MEMORY.md"), filename="x.zip")


def test_a_zip_bomb_is_refused_by_its_ratio():
    with pytest.raises(ik.IntakeError, match="compressed"):
        ik.receive(_zip({"conversations.json": b"\0" * (5 * 1024 * 1024)}), filename="x.zip")


def test_too_many_entries_and_an_oversize_upload_are_refused(monkeypatch):
    monkeypatch.setattr(ik, "MAX_ENTRIES", 3)
    with pytest.raises(ik.IntakeError, match="more than"):
        ik.receive(_zip({f"skills/s{i}.md": b"x" for i in range(5)}), filename="x.zip")
    with pytest.raises(ik.IntakeError, match="larger than"):
        ik.receive(io.BytesIO(b"x" * 2048), filename="conversations.json", max_bytes=1024)


def test_a_refused_upload_leaves_nothing_behind(tmp_path):
    with pytest.raises(ik.IntakeError):
        ik.receive(_zip({"../x.md": b"zqxcanary"}), filename="x.zip")
    assert not list(tmp_path.glob("windy-soul-intake-*"))


def test_discard_wipes_and_sweep_removes_old_intakes(tmp_path):
    got = ik.receive(io.BytesIO(b'[{"zqx": "canary"}]'), filename="conversations.json")
    ik.discard(got)
    assert not got.dir.exists()
    old = ik.receive(io.BytesIO(b"[]"), filename="conversations.json")
    past = time.time() - ik.MAX_AGE_S - 60
    os.utime(old.dir.parent, (past, past))
    fresh = ik.receive(io.BytesIO(b"[]"), filename="conversations.json")
    assert ik.sweep() == 1
    assert not old.dir.exists() and fresh.dir.exists()
