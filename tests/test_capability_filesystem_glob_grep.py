"""Tests for fs.glob and fs.grep_files (Wave 3 #2).

Mirror the security focus of Wave 3 #1's tests: prove the allowlist +
always-deny enforcement holds for the new globbing/grepping path too.
Plus capability-specific edges: regex injection safety, max-results
caps, binary file skipping, include_glob filtering.
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
    _glob_handler,
    _grep_files_handler,
    register_filesystem_capabilities,
)


@pytest.fixture
def grep_sandbox(tmp_path):
    """A small project tree to grep across.

    Layout:
      allowed/
        a.py        # contains "def hello"
        b.py        # contains "def world"
        notes.md    # contains "TODO write tests"
        deep/
          c.py      # contains "def hello again"
        binary.bin  # binary; should be skipped
        .ssh/       # always-deny; should never match
          id_rsa    # contains "def secret_function" (must NOT match)
      forbidden/    # outside allowlist
        leak.py     # contains "def hello"
    """
    allowed = tmp_path / "allowed"
    forbidden = tmp_path / "forbidden"
    allowed.mkdir()
    forbidden.mkdir()
    (allowed / "deep").mkdir()
    (allowed / ".ssh").mkdir()

    (allowed / "a.py").write_text("def hello():\n    pass\n")
    (allowed / "b.py").write_text("def world():\n    pass\n")
    (allowed / "notes.md").write_text("TODO write tests\n# headline\n")
    (allowed / "deep" / "c.py").write_text("def hello again():\n    return 1\n")
    (allowed / "binary.bin").write_bytes(b"\x00def hello\x00\xff\xfe")
    (allowed / ".ssh" / "id_rsa").write_text("def secret_function(): pass\n")

    (forbidden / "leak.py").write_text("def hello():\n    pass\n")

    return {
        "allowed": str(allowed),
        "forbidden": str(forbidden),
        "tmp_path": tmp_path,
    }


# ── glob: allowlist + correctness ───────────────────────────────────


def test_glob_returns_matching_files(grep_sandbox):
    out = _glob_handler(
        pattern=os.path.join(grep_sandbox["allowed"], "*.py"),
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    paths = {m["path"] for m in out["matches"]}
    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("b.py") for p in paths)
    assert out["truncated"] is False


def test_glob_recursive_with_double_star(grep_sandbox):
    out = _glob_handler(
        pattern=os.path.join(grep_sandbox["allowed"], "**", "*.py"),
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    paths = {m["path"] for m in out["matches"]}
    assert any(p.endswith("c.py") for p in paths)


def test_glob_silently_drops_outside_allowlist(grep_sandbox):
    """A pattern that matches both allowed and forbidden trees should
    return only the allowed half — silent drop, not error."""
    pattern = os.path.join(grep_sandbox["tmp_path"], "**", "*.py")
    out = _glob_handler(
        pattern=pattern,
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    paths = {m["path"] for m in out["matches"]}
    assert all(grep_sandbox["allowed"] in p for p in paths)
    assert not any("forbidden" in p for p in paths)
    assert out["denied_by_allowlist"] >= 1  # leak.py was denied


def test_glob_skips_always_deny_subtrees(grep_sandbox):
    """Globbing under home-style root must never return .ssh entries."""
    out = _glob_handler(
        pattern=os.path.join(grep_sandbox["allowed"], "**", "*"),
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    paths = {m["path"] for m in out["matches"]}
    assert not any(".ssh" in p for p in paths)


def test_glob_max_results_truncates(grep_sandbox):
    out = _glob_handler(
        pattern=os.path.join(grep_sandbox["allowed"], "**", "*"),
        max_results=2,
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    assert len(out["matches"]) == 2
    assert out["truncated"] is True


def test_glob_empty_allowlist_raises(grep_sandbox):
    with pytest.raises(PermissionError, match="no allowed roots"):
        _glob_handler(
            pattern="/tmp/*",
            _allowed_roots=[],
        )


# ── grep: allowlist + correctness ──────────────────────────────────


def test_grep_finds_matches_across_files(grep_sandbox):
    out = _grep_files_handler(
        pattern=r"def hello",
        root=grep_sandbox["allowed"],
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    matches = out["matches"]
    paths = {m["path"] for m in matches}
    assert any(p.endswith("a.py") for p in paths)
    assert any(p.endswith("c.py") for p in paths)


def test_grep_skips_binary_files(grep_sandbox):
    """The binary.bin file contains the literal bytes 'def hello' but
    should not appear in the matches because it's not UTF-8 decodable."""
    out = _grep_files_handler(
        pattern=r"def hello",
        root=grep_sandbox["allowed"],
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    paths = {m["path"] for m in out["matches"]}
    assert not any("binary.bin" in p for p in paths)
    assert out["files_skipped_binary"] >= 1


def test_grep_silently_skips_always_deny(grep_sandbox):
    """The .ssh/id_rsa contains 'def secret_function' but the always-
    deny check must keep it out of the match list."""
    out = _grep_files_handler(
        pattern=r"def secret_function",
        root=grep_sandbox["allowed"],
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    assert out["match_count"] == 0
    assert out["files_denied_by_allowlist"] >= 1


def test_grep_outside_allowlist_root_raises(grep_sandbox):
    with pytest.raises(PermissionError):
        _grep_files_handler(
            pattern=r"def",
            root=grep_sandbox["forbidden"],
            _allowed_roots=[grep_sandbox["allowed"]],
        )


def test_grep_include_glob_filters_files(grep_sandbox):
    out = _grep_files_handler(
        pattern=r"hello",
        root=grep_sandbox["allowed"],
        include_glob="*.py",
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    paths = {m["path"] for m in out["matches"]}
    # Only .py files should be matched; notes.md (which doesn't have
    # 'hello' anyway) wouldn't appear regardless, but the filter is
    # honored regardless.
    assert all(p.endswith(".py") for p in paths)


def test_grep_invalid_regex_raises_clean_error(grep_sandbox):
    """A malformed regex should raise ValueError with a clean message,
    not crash with a raw re.error."""
    with pytest.raises(ValueError, match="invalid regex"):
        _grep_files_handler(
            pattern=r"[unclosed",
            root=grep_sandbox["allowed"],
            _allowed_roots=[grep_sandbox["allowed"]],
        )


def test_grep_max_results_truncates(grep_sandbox):
    """Asking for max_results=1 against a pattern that matches many
    should return exactly 1 with truncated=true."""
    out = _grep_files_handler(
        pattern=r"def",
        root=grep_sandbox["allowed"],
        max_results=1,
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    assert len(out["matches"]) == 1
    assert out["truncated"] is True


def test_grep_max_matches_per_file(grep_sandbox):
    """Cap per-file matches even when overall max_results is higher."""
    # Make a file with many matches so the per-file cap kicks in
    big = Path(grep_sandbox["allowed"]) / "manyhits.txt"
    big.write_text("\n".join("def hit" for _ in range(50)))

    out = _grep_files_handler(
        pattern=r"def hit",
        root=grep_sandbox["allowed"],
        max_results=1000,
        max_matches_per_file=5,
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    matches_in_big = [m for m in out["matches"] if "manyhits.txt" in m["path"]]
    assert len(matches_in_big) == 5


def test_grep_case_insensitive(grep_sandbox):
    """case_insensitive=True matches mixed-case patterns."""
    out = _grep_files_handler(
        pattern=r"DEF HELLO",
        root=grep_sandbox["allowed"],
        case_insensitive=True,
        _allowed_roots=[grep_sandbox["allowed"]],
    )
    assert out["match_count"] >= 2


# ── End-to-end through the registry ─────────────────────────────────


@pytest.mark.asyncio
async def test_glob_and_grep_registered(grep_sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [grep_sandbox["allowed"]],
        }}},
    )
    assert r.get("fs.glob") is not None
    assert r.get("fs.grep_files") is not None
    assert r.get("fs.glob").tier == Tier.READ_EXTERNAL
    assert r.get("fs.grep_files").band_required == Band.USER


@pytest.mark.asyncio
async def test_glob_invoke_through_registry(grep_sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [grep_sandbox["allowed"]],
        }}},
    )
    out = await r.invoke(
        "fs.glob",
        {"pattern": os.path.join(grep_sandbox["allowed"], "*.py")},
        Band.USER,
    )
    assert out["match_count"] >= 2


@pytest.mark.asyncio
async def test_grep_invoke_through_registry(grep_sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [grep_sandbox["allowed"]],
        }}},
    )
    out = await r.invoke(
        "fs.grep_files",
        {"pattern": "def hello", "root": grep_sandbox["allowed"]},
        Band.USER,
    )
    assert out["match_count"] >= 2


@pytest.mark.asyncio
async def test_glob_and_grep_denied_for_sandbox_band(grep_sandbox):
    r = CapabilityRegistry()
    register_filesystem_capabilities(
        r,
        config={"capabilities": {"filesystem": {
            "allowed_roots": [grep_sandbox["allowed"]],
        }}},
    )
    with pytest.raises(CapabilityDenied):
        await r.invoke(
            "fs.glob",
            {"pattern": os.path.join(grep_sandbox["allowed"], "*")},
            Band.SANDBOX,
        )
    with pytest.raises(CapabilityDenied):
        await r.invoke(
            "fs.grep_files",
            {"pattern": "x", "root": grep_sandbox["allowed"]},
            Band.SANDBOX,
        )
