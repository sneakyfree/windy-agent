"""Tests for the user-memory → nodes-table seeding."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from windyfly.memory.database import Database
from windyfly.memory.seed_from_user_memory import (
    _parse_frontmatter,
    seed_from_user_memory,
)


# ── Frontmatter parsing ────────────────────────────────────────────


def test_parses_frontmatter_and_body():
    text = (
        "---\n"
        "name: Polly Clone Blueprint\n"
        "description: 90-day clone plan\n"
        "type: project\n"
        "---\n"
        "Body line 1\nBody line 2\n"
    )
    fm, body = _parse_frontmatter(text)
    assert fm["name"] == "Polly Clone Blueprint"
    assert fm["description"] == "90-day clone plan"
    assert fm["type"] == "project"
    assert body.startswith("Body line 1")


def test_no_frontmatter_returns_empty_dict_and_full_text():
    text = "no frontmatter here\njust body\n"
    fm, body = _parse_frontmatter(text)
    assert fm == {}
    assert body == text


def test_frontmatter_with_colons_in_value():
    text = (
        "---\n"
        "name: Wave 13 Launch Scope\n"
        "description: PR #56 (pg.Pool) deployed cef6ff2\n"
        "type: project\n"
        "---\n"
        "body\n"
    )
    fm, _ = _parse_frontmatter(text)
    assert fm["description"] == "PR #56 (pg.Pool) deployed cef6ff2"


# ── End-to-end seeding ─────────────────────────────────────────────


@pytest.fixture
def db():
    with tempfile.TemporaryDirectory() as td:
        d = Database(str(Path(td) / "t.db"))
        try:
            yield d
        finally:
            d.close()


def _write_memory_files(dir: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (dir / name).write_text(content, encoding="utf-8")


def test_seeds_nodes_for_each_memory_file(db, tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    _write_memory_files(mem, {
        "project_polly.md": (
            "---\nname: Polly Clone\ndescription: mortgage pricing\ntype: project\n---\n"
            "Polly is the pricing engine I'm cloning.\n"
        ),
        "project_nachocrunch.md": (
            "---\nname: NachoCrunch\ndescription: rate sheet ingestion\ntype: project\n---\n"
            "NachoCrunch ingests rate sheets from Onity, PennyMac, etc.\n"
        ),
        "MEMORY.md": "- index — should be skipped\n",
    })
    out = seed_from_user_memory(db, memory_dir=str(mem))
    assert out["imported"] == 2
    assert out["skipped"] == 1  # MEMORY.md
    assert out["errors"] == 0

    rows = db.fetchall("SELECT type, name FROM nodes ORDER BY name")
    types_names = {(r["type"], r["name"]) for r in rows}
    assert ("memory.project", "Polly Clone") in types_names
    assert ("memory.project", "NachoCrunch") in types_names


def test_metadata_stores_body_and_truncation_flag(db, tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    huge_body = "x" * 5000
    _write_memory_files(mem, {
        "big.md": (
            "---\nname: BigOne\ntype: project\n---\n" + huge_body
        ),
    })
    seed_from_user_memory(db, memory_dir=str(mem))
    row = db.fetchone(
        "SELECT metadata FROM nodes WHERE name = ?", ("BigOne",),
    )
    md = json.loads(row["metadata"])
    assert md["body_truncated"] is True
    assert md["body_total_chars"] == 5000
    assert len(md["body"]) == 4000


def test_idempotent_re_seed_upserts_not_duplicates(db, tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    _write_memory_files(mem, {
        "x.md": "---\nname: Once\ntype: project\n---\nfirst body\n",
    })
    seed_from_user_memory(db, memory_dir=str(mem))
    # Modify and re-seed
    (mem / "x.md").write_text(
        "---\nname: Once\ntype: project\n---\nupdated body\n",
        encoding="utf-8",
    )
    out2 = seed_from_user_memory(db, memory_dir=str(mem))
    assert out2["imported"] == 1
    rows = db.fetchall("SELECT id, metadata FROM nodes WHERE name = ?", ("Once",))
    assert len(rows) == 1  # upsert, not duplicate
    md = json.loads(rows[0]["metadata"])
    assert "updated body" in md["body"]


def test_dry_run_does_not_write(db, tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    _write_memory_files(mem, {
        "p.md": "---\nname: WillNotBeWritten\ntype: project\n---\nbody\n",
    })
    out = seed_from_user_memory(db, memory_dir=str(mem), dry_run=True)
    assert out["imported"] == 1
    assert out["dry_run"] is True
    rows = db.fetchall("SELECT id FROM nodes")
    assert rows == []  # nothing actually written


def test_missing_dir_returns_clean_error(db, tmp_path):
    out = seed_from_user_memory(db, memory_dir=str(tmp_path / "nope"))
    assert out["imported"] == 0
    assert "does not exist" in out["error"]


def test_file_without_frontmatter_still_imported(db, tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    _write_memory_files(mem, {
        "loose_note.md": "Just a note with no frontmatter at all.\n",
    })
    out = seed_from_user_memory(db, memory_dir=str(mem))
    assert out["imported"] == 1
    rows = db.fetchall("SELECT type, name FROM nodes")
    assert rows[0]["type"] == "memory.note"
    assert rows[0]["name"] == "Loose Note"  # filename-derived
