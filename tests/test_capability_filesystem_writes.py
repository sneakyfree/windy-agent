"""Tests for Wave 4 #1 — fs.write_file, fs.move_file, fs.delete_file,
fs.undo_last_action.

The unique-to-Windy-Fly properties this suite proves:

  - Atomic write via temp + rename (no partial-write state ever exists)
  - Runtime tier escalation: overwrite=true bumps band requirement
    from USER to TRUSTED at runtime
  - Undo journal reverses delete (within retention) and move
  - Always-deny inherited from #56 — write_file to ~/.ssh refused
  - Dry-run uniform shape across every destructive cap
  - audit ledger sees the write through the existing #53 hook (smoke)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from windyfly.agent.capabilities import (
    Band,
    CapabilityDenied,
    CapabilityRegistry,
    Tier,
)
from windyfly.agent.capabilities.filesystem import (
    _atomic_write_text,
    _delete_file_handler,
    _edit_file_handler,
    _move_file_handler,
    _undo_last_action_handler,
    _write_file_handler,
    register_filesystem_capabilities,
)


@pytest.fixture
def write_sandbox(tmp_path):
    """Allowed tree + a forbidden tree + a journal in the tmp dir."""
    allowed = tmp_path / "allowed"
    forbidden = tmp_path / "forbidden"
    allowed.mkdir()
    forbidden.mkdir()
    (allowed / "existing.txt").write_text("original content\n")
    (forbidden / "secret.txt").write_text("nope\n")
    journal = tmp_path / "undo-journal.ndjson"
    return {
        "allowed": str(allowed),
        "forbidden": str(forbidden),
        "journal": str(journal),
        "tmp_path": tmp_path,
    }


# ── _atomic_write_text — the partial-write impossibility ───────────


def test_atomic_write_creates_target(write_sandbox):
    target = Path(write_sandbox["allowed"]) / "fresh.txt"
    n = _atomic_write_text(target, "hello atomic")
    assert target.read_text() == "hello atomic"
    assert n == len("hello atomic".encode("utf-8"))


def test_atomic_write_cleans_up_temp(write_sandbox):
    target = Path(write_sandbox["allowed"]) / "fresh.txt"
    _atomic_write_text(target, "hi")
    # No .windy.tmp leftovers
    assert not list(Path(write_sandbox["allowed"]).glob("*.windy.tmp"))


def test_atomic_write_replaces_existing(write_sandbox):
    target = Path(write_sandbox["allowed"]) / "existing.txt"
    _atomic_write_text(target, "replaced")
    assert target.read_text() == "replaced"


# ── fs.write_file — base case + safety ─────────────────────────────


def test_write_file_creates_new(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "new.txt")
    out = _write_file_handler(
        path=target, content="hello",
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert out["atomic"] is True
    assert out["bytes_written"] == 5
    assert out["plan"]["action"] == "create"
    assert Path(target).read_text() == "hello"
    # No undo record for a fresh-create (nothing to restore)
    assert out["undo_record_id"] is None


def test_write_file_refuses_overwrite_by_default(write_sandbox):
    existing = os.path.join(write_sandbox["allowed"], "existing.txt")
    with pytest.raises(FileExistsError, match="overwrite=true"):
        _write_file_handler(
            path=existing, content="new",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_write_file_overwrite_true_replaces_and_journals(write_sandbox):
    existing = os.path.join(write_sandbox["allowed"], "existing.txt")
    out = _write_file_handler(
        path=existing, content="replaced",
        overwrite=True,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert Path(existing).read_text() == "replaced"
    assert out["plan"]["action"] == "overwrite"
    assert out["undo_record_id"] is not None  # journal record exists


def test_write_file_dry_run_does_not_write(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "would_be.txt")
    out = _write_file_handler(
        path=target, content="never",
        dry_run=True,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert out["plan"]["action"] == "create"
    assert not Path(target).exists()


def test_write_file_outside_allowlist_raises(write_sandbox):
    with pytest.raises(PermissionError):
        _write_file_handler(
            path=os.path.join(write_sandbox["forbidden"], "leak.txt"),
            content="x",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_write_file_to_always_deny_path_raises(write_sandbox):
    """Even with allowed_roots wide open, .ssh is always blocked."""
    ssh_dir = Path(write_sandbox["allowed"]) / ".ssh"
    ssh_dir.mkdir()
    with pytest.raises(PermissionError, match="always-deny"):
        _write_file_handler(
            path=str(ssh_dir / "id_rsa"),
            content="FAKE-PRIVATE-KEY",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


# ── fs.move_file ───────────────────────────────────────────────────


def test_move_file_works(write_sandbox):
    src = os.path.join(write_sandbox["allowed"], "existing.txt")
    dst = os.path.join(write_sandbox["allowed"], "moved.txt")
    out = _move_file_handler(
        source=src, destination=dst,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert not Path(src).exists()
    assert Path(dst).exists()
    assert out["undo_record_id"] is not None


def test_move_file_refuses_clobber(write_sandbox):
    src = os.path.join(write_sandbox["allowed"], "existing.txt")
    dst = os.path.join(write_sandbox["allowed"], "also_existing.txt")
    Path(dst).write_text("don't clobber me")
    with pytest.raises(FileExistsError, match="clobber"):
        _move_file_handler(
            source=src, destination=dst,
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_move_file_destination_outside_allowlist(write_sandbox):
    src = os.path.join(write_sandbox["allowed"], "existing.txt")
    dst = os.path.join(write_sandbox["forbidden"], "leaked.txt")
    with pytest.raises(PermissionError):
        _move_file_handler(
            source=src, destination=dst,
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


# ── fs.delete_file ─────────────────────────────────────────────────


def test_delete_file_removes_and_journals(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    out = _delete_file_handler(
        path=target,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert not Path(target).exists()
    assert out["undo_record_id"] is not None
    assert out["plan"]["undo_supported"] is True


def test_delete_file_refuses_directory(write_sandbox):
    with pytest.raises(IsADirectoryError):
        _delete_file_handler(
            path=write_sandbox["allowed"],
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_delete_file_dry_run_does_not_delete(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    out = _delete_file_handler(
        path=target, dry_run=True,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is False
    assert Path(target).exists()


# ── fs.undo_last_action — the marketing moment ─────────────────────


def test_undo_restores_deleted_file(write_sandbox):
    """The marketing demo: delete a file, undo, file is back exactly
    as it was."""
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    original_content = Path(target).read_text()

    _delete_file_handler(
        path=target,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert not Path(target).exists()

    out = _undo_last_action_handler(
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert out["original_action"] == "delete"
    assert Path(target).read_text() == original_content


def test_undo_restores_overwritten_file(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    original_content = Path(target).read_text()

    _write_file_handler(
        path=target, content="overwritten",
        overwrite=True,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert Path(target).read_text() == "overwritten"

    out = _undo_last_action_handler(
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert Path(target).read_text() == original_content


def test_undo_reverses_move(write_sandbox):
    src = os.path.join(write_sandbox["allowed"], "existing.txt")
    dst = os.path.join(write_sandbox["allowed"], "moved.txt")
    _move_file_handler(
        source=src, destination=dst,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert Path(dst).exists()
    assert not Path(src).exists()

    out = _undo_last_action_handler(
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert Path(src).exists()
    assert not Path(dst).exists()


def test_undo_with_no_records_returns_clean_message(write_sandbox):
    out = _undo_last_action_handler(
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is False
    assert "no undoable record" in out["reason"]


def test_undo_already_undone_returns_clean_message(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    _delete_file_handler(
        path=target,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    _undo_last_action_handler(_journal_path=write_sandbox["journal"])

    # Now nothing left to undo
    out2 = _undo_last_action_handler(
        _journal_path=write_sandbox["journal"],
    )
    assert out2["executed"] is False


# ── End-to-end through the registry ─────────────────────────────────


@pytest.mark.asyncio
async def test_write_caps_registered_with_correct_tiers(write_sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    assert r.get("fs.write_file").tier == Tier.WRITE_LOCAL_SAFE
    assert r.get("fs.move_file").tier == Tier.WRITE_DESTRUCTIVE
    assert r.get("fs.delete_file").tier == Tier.WRITE_DESTRUCTIVE
    assert r.get("fs.undo_last_action").tier == Tier.WRITE_LOCAL_SAFE
    # write_file's runtime tier check is hooked
    assert r.get("fs.write_file").runtime_tier_check is not None


@pytest.mark.asyncio
async def test_write_file_invoke_through_registry(write_sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    target = os.path.join(write_sandbox["allowed"], "via_registry.txt")
    out = await r.invoke(
        "fs.write_file",
        {"path": target, "content": "registry says hi"},
        Band.USER,
    )
    assert out["executed"] is True
    assert Path(target).read_text() == "registry says hi"


@pytest.mark.asyncio
async def test_user_band_can_create_new_file(write_sandbox):
    """USER band can call fs.write_file when overwrite is not set."""
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    out = await r.invoke(
        "fs.write_file",
        {"path": os.path.join(write_sandbox["allowed"], "fresh.txt"),
         "content": "x"},
        Band.USER,
    )
    assert out["executed"] is True


@pytest.mark.asyncio
async def test_user_band_blocked_from_overwrite(write_sandbox):
    """The runtime tier escalation: overwrite=true bumps to TRUSTED+.
    USER band can't pass that gate."""
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    with pytest.raises(CapabilityDenied) as excinfo:
        await r.invoke(
            "fs.write_file",
            {"path": target, "content": "x", "overwrite": True},
            Band.USER,
        )
    assert "escalated" in str(excinfo.value).lower()
    assert "trusted" in str(excinfo.value).lower()
    # File untouched
    assert Path(target).read_text() == "original content\n"


@pytest.mark.asyncio
async def test_trusted_band_can_overwrite(write_sandbox):
    """TRUSTED band passes the runtime escalation gate."""
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    out = await r.invoke(
        "fs.write_file",
        {"path": target, "content": "trusted overwrote", "overwrite": True},
        Band.TRUSTED,
    )
    assert out["executed"] is True
    assert Path(target).read_text() == "trusted overwrote"


@pytest.mark.asyncio
async def test_user_band_blocked_from_delete(write_sandbox):
    """delete_file is statically Tier.WRITE_DESTRUCTIVE → TRUSTED+."""
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    with pytest.raises(CapabilityDenied):
        await r.invoke(
            "fs.delete_file",
            {"path": os.path.join(write_sandbox["allowed"], "existing.txt")},
            Band.USER,
        )


@pytest.mark.asyncio
async def test_undo_invoke_through_registry(write_sandbox):
    """End-to-end: delete then undo both through the registry."""
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [write_sandbox["allowed"]],
            "undo_journal_path": write_sandbox["journal"],
        }}},
    )
    target = os.path.join(write_sandbox["allowed"], "existing.txt")
    original = Path(target).read_text()

    await r.invoke("fs.delete_file", {"path": target}, Band.TRUSTED)
    assert not Path(target).exists()

    out = await r.invoke("fs.undo_last_action", {}, Band.USER)
    assert out["executed"] is True
    assert Path(target).read_text() == original


# ── fs.edit_file — Claude Code-style string replacement ────────────


def _seed_edit_target(sandbox, name="edit_me.txt", text="alpha beta gamma\n"):
    p = Path(sandbox["allowed"]) / name
    p.write_text(text)
    return str(p)


def test_edit_file_replaces_unique_match(write_sandbox):
    target = _seed_edit_target(write_sandbox, text="hello world\n")
    out = _edit_file_handler(
        path=target, old_string="world", new_string="windy",
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert out["occurrences_replaced"] == 1
    assert Path(target).read_text() == "hello windy\n"
    assert out["undo_record_id"] is not None


def test_edit_file_refuses_ambiguous_match(write_sandbox):
    target = _seed_edit_target(write_sandbox, text="x x x\n")
    with pytest.raises(ValueError, match="matches 3 times"):
        _edit_file_handler(
            path=target, old_string="x", new_string="y",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )
    # File untouched after refusal
    assert Path(target).read_text() == "x x x\n"


def test_edit_file_replace_all_with_ambiguous_match(write_sandbox):
    target = _seed_edit_target(write_sandbox, text="x x x\n")
    out = _edit_file_handler(
        path=target, old_string="x", new_string="y",
        replace_all=True,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["occurrences_replaced"] == 3
    assert Path(target).read_text() == "y y y\n"


def test_edit_file_refuses_when_old_string_not_found(write_sandbox):
    target = _seed_edit_target(write_sandbox, text="hello\n")
    with pytest.raises(ValueError, match="not found"):
        _edit_file_handler(
            path=target, old_string="missing", new_string="found",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_edit_file_refuses_empty_old_string(write_sandbox):
    target = _seed_edit_target(write_sandbox)
    with pytest.raises(ValueError, match="empty"):
        _edit_file_handler(
            path=target, old_string="", new_string="anything",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_edit_file_refuses_identical_old_and_new(write_sandbox):
    target = _seed_edit_target(write_sandbox)
    with pytest.raises(ValueError, match="identical"):
        _edit_file_handler(
            path=target, old_string="alpha", new_string="alpha",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_edit_file_refuses_nonexistent_path(write_sandbox):
    target = os.path.join(write_sandbox["allowed"], "ghost.txt")
    with pytest.raises(FileNotFoundError, match="does not exist"):
        _edit_file_handler(
            path=target, old_string="x", new_string="y",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_edit_file_refuses_directory(write_sandbox):
    with pytest.raises(IsADirectoryError):
        _edit_file_handler(
            path=write_sandbox["allowed"], old_string="x", new_string="y",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )


def test_edit_file_dry_run_does_not_write(write_sandbox):
    target = _seed_edit_target(write_sandbox, text="hello world\n")
    out = _edit_file_handler(
        path=target, old_string="world", new_string="windy",
        dry_run=True,
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is False
    assert out["preview_only"] is True
    assert out["plan"]["occurrences_replaced"] == 1
    assert Path(target).read_text() == "hello world\n"


def test_edit_file_undo_restores_original(write_sandbox):
    target = _seed_edit_target(write_sandbox, text="line one\nline two\n")
    _edit_file_handler(
        path=target, old_string="line two", new_string="line edited",
        _allowed_roots=[write_sandbox["allowed"]],
        _journal_path=write_sandbox["journal"],
    )
    assert Path(target).read_text() == "line one\nline edited\n"

    out = _undo_last_action_handler(
        record_id=None, _journal_path=write_sandbox["journal"],
    )
    assert out["executed"] is True
    assert Path(target).read_text() == "line one\nline two\n"


def test_edit_file_outside_allowlist_refused(write_sandbox):
    forbidden = os.path.join(write_sandbox["forbidden"], "secret.txt")
    with pytest.raises(PermissionError, match="outside the allowed roots"):
        _edit_file_handler(
            path=forbidden, old_string="nope", new_string="yes",
            _allowed_roots=[write_sandbox["allowed"]],
            _journal_path=write_sandbox["journal"],
        )
