"""Tests for the undo journal infrastructure (Wave 4 #1)."""

from __future__ import annotations

import base64
import os
from pathlib import Path

import pytest

from windyfly.agent.capabilities.undo_journal import (
    DEFAULT_RETENTION_DAYS,
    MAX_ORIGINAL_STATE_BYTES,
    append_record,
    capture_file_state,
    find_record,
    latest_undoable_record,
    mark_undone,
    read_records,
    restore_from_record,
)


@pytest.fixture
def journal(tmp_path):
    return str(tmp_path / "undo-journal.ndjson")


@pytest.fixture
def text_file(tmp_path):
    p = tmp_path / "file.txt"
    p.write_text("captured content")
    return p


def test_capture_returns_none_for_missing(tmp_path):
    assert capture_file_state(tmp_path / "ghost.txt") is None


def test_capture_returns_file_envelope(text_file):
    state = capture_file_state(text_file)
    assert state["kind"] == "file"
    assert state["size"] == len("captured content")
    decoded = base64.b64decode(state["content_b64"]).decode("utf-8")
    assert decoded == "captured content"


def test_capture_marks_too_big(tmp_path):
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (MAX_ORIGINAL_STATE_BYTES + 1))
    state = capture_file_state(big)
    assert state["too_big"] is True
    assert state["content_b64"] is None
    assert state["size"] == MAX_ORIGINAL_STATE_BYTES + 1


def test_capture_marks_symlink(tmp_path):
    target = tmp_path / "real.txt"
    target.write_text("real")
    link = tmp_path / "link.txt"
    link.symlink_to(target)
    state = capture_file_state(link)
    assert state["kind"] == "symlink"
    assert state["link_target"] == str(target)


def test_capture_marks_directory(tmp_path):
    d = tmp_path / "dir"
    d.mkdir()
    state = capture_file_state(d)
    assert state["kind"] == "directory"


def test_append_and_read(journal):
    rid = append_record(
        capability_id="fs.delete_file",
        action="delete",
        target="/tmp/x.txt",
        original_state={"kind": "file", "size": 10, "mode": 0o644,
                        "mtime": 0, "content_b64": "aGVsbG8="},
        journal_path=journal,
    )
    records = read_records(journal)
    assert len(records) == 1
    assert records[0]["id"] == rid
    assert records[0]["capability_id"] == "fs.delete_file"
    assert records[0]["undone"] is False


def test_records_ordered_oldest_first(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    b = append_record(capability_id="x", action="delete", target="/b",
                      original_state=None, journal_path=journal)
    records = read_records(journal)
    assert [r["id"] for r in records] == [a, b]


def test_read_excludes_undone_by_default(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    mark_undone(a, journal)
    assert read_records(journal) == []
    assert len(read_records(journal, include_undone=True)) == 1


def test_find_record_locates_by_id(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    found = find_record(a, journal)
    assert found is not None
    assert found["id"] == a


def test_find_record_returns_none_for_missing(journal):
    assert find_record("nonexistent", journal) is None


def test_latest_undoable_returns_most_recent(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    b = append_record(capability_id="x", action="delete", target="/b",
                      original_state=None, journal_path=journal)
    latest = latest_undoable_record(journal)
    assert latest["id"] == b


def test_latest_undoable_skips_undone(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    b = append_record(capability_id="x", action="delete", target="/b",
                      original_state=None, journal_path=journal)
    mark_undone(b, journal)
    latest = latest_undoable_record(journal)
    assert latest["id"] == a


def test_mark_undone_sets_flag(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    assert mark_undone(a, journal) is True
    rec = find_record(a, journal)
    assert rec["undone"] is True
    assert "undone_at" in rec


def test_mark_undone_idempotent(journal):
    a = append_record(capability_id="x", action="delete", target="/a",
                      original_state=None, journal_path=journal)
    assert mark_undone(a, journal) is True
    assert mark_undone(a, journal) is False


def test_mark_undone_for_missing_returns_false(journal):
    assert mark_undone("nonexistent", journal) is False


def test_restore_undoes_delete_of_file(tmp_path, journal):
    target = tmp_path / "deleted.txt"
    target.write_text("the original")
    state = capture_file_state(target)
    target.unlink()

    record = {
        "id": "test", "capability_id": "fs.delete_file",
        "action": "delete", "target": str(target),
        "original_state": state,
    }
    restore_from_record(record)
    assert target.read_text() == "the original"


def test_restore_undoes_delete_of_symlink(tmp_path, journal):
    real = tmp_path / "real.txt"
    real.write_text("data")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    state = capture_file_state(link)
    link.unlink()

    record = {
        "id": "test", "capability_id": "fs.delete_file",
        "action": "delete", "target": str(link),
        "original_state": state,
    }
    restore_from_record(record)
    assert link.is_symlink()
    assert os.readlink(link) == str(real)


def test_restore_undoes_overwrite(tmp_path, journal):
    target = tmp_path / "to_overwrite.txt"
    target.write_text("original")
    state = capture_file_state(target)
    target.write_text("new content")
    assert target.read_text() == "new content"

    record = {
        "id": "test", "capability_id": "fs.write_file",
        "action": "overwrite", "target": str(target),
        "original_state": state,
    }
    restore_from_record(record)
    assert target.read_text() == "original"


def test_restore_undoes_move(tmp_path, journal):
    original_src = tmp_path / "from.txt"
    moved_to = tmp_path / "to.txt"
    original_src.write_text("moved data")
    original_src.rename(moved_to)

    record = {
        "id": "test", "capability_id": "fs.move_file",
        "action": "move", "target": str(moved_to),
        "original_state": None,
        "extra": {"source": str(original_src)},
    }
    restore_from_record(record)
    assert original_src.exists()
    assert not moved_to.exists()


def test_restore_refuses_too_big_delete(tmp_path):
    record = {
        "id": "test", "capability_id": "fs.delete_file",
        "action": "delete", "target": str(tmp_path / "ghost.txt"),
        "original_state": {
            "kind": "file", "too_big": True,
            "size": MAX_ORIGINAL_STATE_BYTES + 1,
            "content_b64": None,
            "mode": 0o644, "mtime": 0,
        },
    }
    with pytest.raises(ValueError, match="too big"):
        restore_from_record(record)


def test_restore_refuses_unknown_action():
    record = {
        "id": "test", "capability_id": "x",
        "action": "nuke", "target": "/tmp/x",
        "original_state": None,
    }
    with pytest.raises(ValueError, match="unknown action"):
        restore_from_record(record)


def test_restore_move_refuses_when_source_now_exists(tmp_path):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    dst.write_text("moved data")
    src.write_text("something new in the source slot")

    record = {
        "id": "test", "capability_id": "fs.move_file",
        "action": "move", "target": str(dst),
        "original_state": None,
        "extra": {"source": str(src)},
    }
    with pytest.raises(FileExistsError, match="now exists"):
        restore_from_record(record)


def test_concurrent_appends_dont_interleave(journal):
    """Threading lock keeps multi-thread appends serialized."""
    import threading

    def append_n(n: int) -> None:
        for i in range(n):
            append_record(
                capability_id="x", action="delete",
                target=f"/p/{threading.get_ident()}/{i}",
                original_state=None,
                journal_path=journal,
            )

    threads = [threading.Thread(target=append_n, args=(20,)) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    records = read_records(journal)
    assert len(records) == 100
    assert len({r["id"] for r in records}) == 100
