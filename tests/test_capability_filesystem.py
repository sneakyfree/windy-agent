"""Tests for fs.read_file and fs.list_directory (Wave 3 #1).

The allowlist + always-deny checks are the safety contract — most of
the test surface here is "prove the LLM can't read /etc/passwd or
~/.ssh even if it asks nicely."
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from windyfly.agent.capabilities import (
    Band,
    CapabilityRegistry,
    Tier,
)
from windyfly.agent.capabilities.filesystem import (
    _read_file_handler,
    _list_directory_handler,
    _resolve_and_check,
    register_filesystem_capabilities,
)


@pytest.fixture
def sandbox(tmp_path):
    """Two trees: one inside the allowlist, one outside.

    Returns dict with keys ``allowed`` (allowed root), ``forbidden``
    (outside-allowlist tree), and a few prepared files.
    """
    allowed = tmp_path / "allowed"
    forbidden = tmp_path / "forbidden"
    allowed.mkdir()
    forbidden.mkdir()

    (allowed / "hello.txt").write_text("hi from allowed")
    (allowed / "subdir").mkdir()
    (allowed / "subdir" / "nested.md").write_text("# nested")
    (allowed / "binary.bin").write_bytes(b"\x00\x01\x02\x03\xff\xfe")

    (forbidden / "secret.txt").write_text("nope you can't read this")

    return {
        "allowed": str(allowed),
        "forbidden": str(forbidden),
        "tmp_path": tmp_path,
    }


# ── Allowlist enforcement ──────────────────────────────────────────


def test_resolves_path_inside_allowlist(sandbox):
    p = _resolve_and_check(
        os.path.join(sandbox["allowed"], "hello.txt"),
        [sandbox["allowed"]],
    )
    assert p.is_file()


def test_rejects_path_outside_allowlist(sandbox):
    with pytest.raises(PermissionError, match="outside the allowed roots"):
        _resolve_and_check(
            os.path.join(sandbox["forbidden"], "secret.txt"),
            [sandbox["allowed"]],
        )


def test_rejects_traversal_attempt(sandbox):
    """`/allowed/../forbidden/secret.txt` resolves outside allowlist."""
    sneaky = os.path.join(
        sandbox["allowed"], "..", "forbidden", "secret.txt",
    )
    with pytest.raises(PermissionError, match="outside the allowed roots"):
        _resolve_and_check(sneaky, [sandbox["allowed"]])


def test_rejects_symlink_pointing_outside_allowlist(sandbox):
    """Symlinks inside the allowed tree pointing outside must be denied."""
    link = Path(sandbox["allowed"]) / "evil_link"
    link.symlink_to(Path(sandbox["forbidden"]) / "secret.txt")
    with pytest.raises(PermissionError, match="outside the allowed roots"):
        _resolve_and_check(str(link), [sandbox["allowed"]])


def test_empty_allowlist_denies_everything(sandbox):
    with pytest.raises(PermissionError, match="no allowed roots"):
        _resolve_and_check(
            os.path.join(sandbox["allowed"], "hello.txt"), [],
        )


def test_always_deny_ssh_blocks_even_inside_allowlist(sandbox):
    """If somehow .ssh ended up inside the allowlist, we still refuse."""
    ssh_dir = Path(sandbox["allowed"]) / ".ssh"
    ssh_dir.mkdir()
    (ssh_dir / "id_rsa").write_text("FAKE-PRIVATE-KEY-FOR-TEST-ONLY")

    with pytest.raises(PermissionError, match="always-deny"):
        _resolve_and_check(
            str(ssh_dir / "id_rsa"), [sandbox["allowed"]],
        )


def test_always_deny_aws_blocks_even_inside_allowlist(sandbox):
    aws_dir = Path(sandbox["allowed"]) / ".aws"
    aws_dir.mkdir()
    (aws_dir / "credentials").write_text("FAKE")

    with pytest.raises(PermissionError, match="always-deny"):
        _resolve_and_check(
            str(aws_dir / "credentials"), [sandbox["allowed"]],
        )


def test_always_deny_dotenv_blocks_even_inside_allowlist(sandbox):
    """The agent's own .env should be unreachable through fs.read_file."""
    env_file = Path(sandbox["allowed"]) / ".env"
    env_file.write_text("ZAI_API_KEY=fake")

    with pytest.raises(PermissionError, match="always-deny"):
        _resolve_and_check(str(env_file), [sandbox["allowed"]])


# ── read_file behavior ─────────────────────────────────────────────


def test_read_file_returns_content(sandbox):
    out = _read_file_handler(
        path=os.path.join(sandbox["allowed"], "hello.txt"),
        _allowed_roots=[sandbox["allowed"]],
    )
    assert out["content"] == "hi from allowed"
    assert out["truncated"] is False
    assert out["size_bytes"] == len("hi from allowed")


def test_read_file_truncates_at_max_bytes(sandbox):
    big = Path(sandbox["allowed"]) / "big.txt"
    big.write_text("x" * 1000)

    out = _read_file_handler(
        path=str(big), max_bytes=100,
        _allowed_roots=[sandbox["allowed"]],
    )
    assert out["truncated"] is True
    assert len(out["content"]) == 100
    assert out["size_bytes"] == 1000


def test_read_file_returns_binary_hint(sandbox):
    out = _read_file_handler(
        path=os.path.join(sandbox["allowed"], "binary.bin"),
        _allowed_roots=[sandbox["allowed"]],
    )
    assert out["binary"] is True
    assert out["content"] is None
    assert "binary" in out["note"]


def test_read_file_raises_on_directory(sandbox):
    with pytest.raises(IsADirectoryError):
        _read_file_handler(
            path=os.path.join(sandbox["allowed"], "subdir"),
            _allowed_roots=[sandbox["allowed"]],
        )


def test_read_file_raises_on_missing(sandbox):
    with pytest.raises(FileNotFoundError):
        _read_file_handler(
            path=os.path.join(sandbox["allowed"], "ghost.txt"),
            _allowed_roots=[sandbox["allowed"]],
        )


def test_read_file_raises_on_outside_allowlist(sandbox):
    with pytest.raises(PermissionError):
        _read_file_handler(
            path=os.path.join(sandbox["forbidden"], "secret.txt"),
            _allowed_roots=[sandbox["allowed"]],
        )


# ── list_directory behavior ────────────────────────────────────────


def test_list_directory_lists_entries(sandbox):
    out = _list_directory_handler(
        path=sandbox["allowed"],
        _allowed_roots=[sandbox["allowed"]],
    )
    names = {e["name"] for e in out["entries"]}
    assert "hello.txt" in names
    assert "subdir" in names
    assert "binary.bin" in names
    assert out["entry_count"] == len(out["entries"])


def test_list_directory_marks_types_correctly(sandbox):
    out = _list_directory_handler(
        path=sandbox["allowed"],
        _allowed_roots=[sandbox["allowed"]],
    )
    by_name = {e["name"]: e for e in out["entries"]}
    assert by_name["hello.txt"]["type"] == "file"
    assert by_name["subdir"]["type"] == "dir"


def test_list_directory_raises_on_file_path(sandbox):
    with pytest.raises(NotADirectoryError):
        _list_directory_handler(
            path=os.path.join(sandbox["allowed"], "hello.txt"),
            _allowed_roots=[sandbox["allowed"]],
        )


def test_list_directory_raises_on_outside_allowlist(sandbox):
    with pytest.raises(PermissionError):
        _list_directory_handler(
            path=sandbox["forbidden"],
            _allowed_roots=[sandbox["allowed"]],
        )


# ── Registration / end-to-end through the registry ─────────────────


def test_register_filesystem_capabilities_adds_two(sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {"allowed_roots": [sandbox["allowed"]]}}},
    )
    assert r.get("fs.read_file") is not None
    assert r.get("fs.list_directory") is not None


def test_registered_caps_are_tier_read_external(sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {"allowed_roots": [sandbox["allowed"]]}}},
    )
    assert r.get("fs.read_file").tier == Tier.READ_EXTERNAL
    assert r.get("fs.read_file").band_required == Band.USER


@pytest.mark.asyncio
async def test_invoke_read_file_through_registry(sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {"allowed_roots": [sandbox["allowed"]]}}},
    )
    out = await r.invoke(
        "fs.read_file",
        {"path": os.path.join(sandbox["allowed"], "hello.txt")},
        Band.USER,
    )
    assert out["content"] == "hi from allowed"


@pytest.mark.asyncio
async def test_invoke_list_directory_through_registry(sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {"allowed_roots": [sandbox["allowed"]]}}},
    )
    out = await r.invoke(
        "fs.list_directory",
        {"path": sandbox["allowed"]},
        Band.USER,
    )
    assert out["entry_count"] >= 3


@pytest.mark.asyncio
async def test_sandbox_band_cannot_call_fs(sandbox):
    """Tier.READ_EXTERNAL requires USER+, so SANDBOX is denied."""
    from windyfly.agent.capabilities import CapabilityDenied

    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {"allowed_roots": [sandbox["allowed"]]}}},
    )
    with pytest.raises(CapabilityDenied):
        await r.invoke(
            "fs.read_file",
            {"path": os.path.join(sandbox["allowed"], "hello.txt")},
            Band.SANDBOX,
        )
