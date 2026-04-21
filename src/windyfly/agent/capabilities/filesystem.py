"""First-class filesystem capabilities — Wave 3 #1.

The agent's first real hands. ``fs.read_file`` and ``fs.list_directory``
register through CapabilityRegistry, which means they get:

  - Band gating (USER+ by default — grandma's Tier-0/1 instance can
    call read but not shell)
  - Audit ledger writes on every call (agent_actions row with the
    path arg redacted-then-stored)
  - Filtered tool-list emission to the LLM via tool_schemas_for_band
  - Typed error responses through #50's classifier

The allowlist is the safety contract. Every path the LLM passes gets
resolved (``~`` expansion + symlink follow) and checked against a list
of allowed roots. Anything outside raises ``PermissionError``, which
the dispatcher turns into a JSON error the LLM can self-correct on
("the file is outside my allowed roots, ask differently").

Default allowlist for the OWNER band on Grant's box: ``~/`` minus the
high-sensitivity subtrees (``.ssh``, ``.gnupg``, ``.aws``,
``.kube``). Hard-coded denies always win over allows. Configurable
per-instance in ``windyfly.toml`` under ``[capabilities.filesystem]``.

Wave 3 #2 ships ``fs.glob`` and ``fs.grep_files`` on top of this
allowlist. Wave 4 adds the write hands (``fs.write_file``,
``fs.move_file``, ``fs.delete_file``) at higher tiers.
"""

from __future__ import annotations

import glob as glob_module
import logging
import os
import re
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

# Bytes returned by read_file unless the caller overrides. 100KB caps
# the LLM context burn — anything bigger should go through a chunked
# read or a grep, both future capabilities.
_DEFAULT_MAX_BYTES = 100 * 1024

# Caps for glob and grep results. These bound the LLM context burn and
# prevent the agent from accidentally enumerating a 50k-file repo.
_DEFAULT_GLOB_MAX_RESULTS = 100
_DEFAULT_GREP_MAX_RESULTS = 100
_DEFAULT_GREP_MAX_MATCHES_PER_FILE = 20
# Cap individual file scans to avoid burning minutes on a single huge
# log file. Anything bigger should go through fs.read_file's chunked
# reads, not grep.
_GREP_MAX_FILE_BYTES = 5 * 1024 * 1024

# Subtrees we never let the LLM read regardless of allowlist. These are
# the secrets directories every agent framework eventually has a
# horror story about. Hard-coded so a config typo can't override.
_ALWAYS_DENY = (
    ".ssh",
    ".gnupg",
    ".aws",
    ".kube",
    ".gcp",
    ".docker/config.json",
    ".netrc",
    ".pgpass",
    ".env",  # the repo .env for the agent itself
    ".windy",  # secrets file the launchd plist sources
)


def _resolve_and_check(
    raw_path: str,
    allowed_roots: list[str],
) -> Path:
    """Resolve ``raw_path`` and verify it lives under one of
    ``allowed_roots``, after expanding ``~`` and following symlinks.

    Returns the canonical Path. Raises PermissionError if denied.

    Symlink-aware: ``Path.resolve(strict=False)`` follows links so a
    symlink inside the allowlist that points to /etc gets caught.
    """
    expanded = Path(raw_path).expanduser()
    resolved = expanded.resolve(strict=False)

    # Hard-coded denies first — these win over any allow.
    resolved_str = str(resolved)
    for needle in _ALWAYS_DENY:
        # Match either as a path component or a tail
        if (
            f"/{needle}/" in resolved_str
            or resolved_str.endswith(f"/{needle}")
            or resolved_str.endswith(f"/{needle}/")
        ):
            raise PermissionError(
                f"path {raw_path!r} resolves to {resolved} which lives "
                f"under the always-deny list ({needle!r})"
            )

    if not allowed_roots:
        raise PermissionError(
            f"no allowed roots configured for filesystem capabilities; "
            f"refusing {raw_path!r}"
        )

    for root in allowed_roots:
        root_path = Path(root).expanduser().resolve(strict=False)
        try:
            resolved.relative_to(root_path)
            return resolved
        except ValueError:
            continue

    raise PermissionError(
        f"path {raw_path!r} resolves to {resolved} which is outside "
        f"the allowed roots {allowed_roots!r}"
    )


def _read_file_handler(
    *, path: str, max_bytes: int = _DEFAULT_MAX_BYTES,
    _allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    roots = _allowed_roots or []
    resolved = _resolve_and_check(path, roots)

    if not resolved.exists():
        raise FileNotFoundError(f"{path!r} does not exist")
    if resolved.is_dir():
        raise IsADirectoryError(
            f"{path!r} is a directory; use fs.list_directory instead"
        )

    size = resolved.stat().st_size
    truncated = False
    with open(resolved, "rb") as f:
        raw = f.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raw = raw[:max_bytes]
        truncated = True

    # Best-effort decode — binary files come back as a hint string
    # rather than mojibake, since the LLM can't do anything with
    # binary anyway.
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return {
            "path": str(resolved),
            "size_bytes": size,
            "binary": True,
            "content": None,
            "note": "file appears to be binary; not returning contents",
        }

    return {
        "path": str(resolved),
        "size_bytes": size,
        "returned_bytes": len(raw),
        "truncated": truncated,
        "content": text,
    }


def _list_directory_handler(
    *, path: str,
    _allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    roots = _allowed_roots or []
    resolved = _resolve_and_check(path, roots)

    if not resolved.exists():
        raise FileNotFoundError(f"{path!r} does not exist")
    if not resolved.is_dir():
        raise NotADirectoryError(
            f"{path!r} is not a directory; use fs.read_file instead"
        )

    entries = []
    for child in sorted(resolved.iterdir()):
        try:
            stat = child.lstat()  # don't follow symlinks for the listing
            entries.append({
                "name": child.name,
                "type": (
                    "symlink" if child.is_symlink()
                    else "dir" if child.is_dir()
                    else "file"
                ),
                "size": stat.st_size,
                "modified": int(stat.st_mtime),
            })
        except OSError as e:
            entries.append({
                "name": child.name,
                "type": "unknown",
                "error": str(e),
            })

    return {
        "path": str(resolved),
        "entry_count": len(entries),
        "entries": entries,
    }


def _glob_handler(
    *, pattern: str, max_results: int = _DEFAULT_GLOB_MAX_RESULTS,
    _allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Glob a pattern, filtered to allowlist + always-deny.

    Pattern is standard Python ``glob`` syntax (``**`` for recursive,
    ``?`` for single char, etc.). Each result path is validated through
    ``_resolve_and_check`` and silently dropped if it falls outside the
    allowed roots — so a pattern like ``~/**`` returns home contents
    minus .ssh / .aws / .env, not an error.
    """
    roots = _allowed_roots or []
    if not roots:
        raise PermissionError(
            "no allowed roots configured for filesystem capabilities; "
            f"refusing glob {pattern!r}"
        )

    expanded_pattern = os.path.expanduser(pattern)
    raw_results = glob_module.glob(
        expanded_pattern, recursive=True,
    )

    accepted: list[dict[str, Any]] = []
    denied = 0
    for raw in sorted(raw_results):
        try:
            resolved = _resolve_and_check(raw, roots)
        except PermissionError:
            denied += 1
            continue
        try:
            stat = resolved.lstat()
            kind = (
                "symlink" if Path(raw).is_symlink()
                else "dir" if resolved.is_dir()
                else "file"
            )
            accepted.append({
                "path": str(resolved),
                "type": kind,
                "size": stat.st_size,
            })
        except OSError:
            continue
        if len(accepted) >= max_results:
            break

    return {
        "pattern": pattern,
        "match_count": len(accepted),
        "matches": accepted,
        "truncated": len(accepted) >= max_results,
        "denied_by_allowlist": denied,
    }


def _grep_files_handler(
    *,
    pattern: str,
    root: str,
    include_glob: str | None = None,
    max_results: int = _DEFAULT_GREP_MAX_RESULTS,
    max_matches_per_file: int = _DEFAULT_GREP_MAX_MATCHES_PER_FILE,
    case_insensitive: bool = False,
    _allowed_roots: list[str] | None = None,
) -> dict[str, Any]:
    """Grep a regex across files under root, filtered to allowlist.

    Walks ``root`` recursively, optionally filtered by ``include_glob``
    (e.g., ``"*.py"``), grepping each text file's contents. Returns up
    to ``max_results`` total matches across all files; per-file capped
    at ``max_matches_per_file`` to avoid one giant log file dominating
    the result set.

    Compiles the regex with ``re.compile``; an invalid pattern raises
    ``re.error``, which the dispatcher routes to the typed-error
    classifier so the LLM sees a clean "your regex is invalid" message
    rather than a stack trace.

    Skips files >5MB (cap defined in _GREP_MAX_FILE_BYTES) and binary
    files (UTF-8 decode failure heuristic, same as fs.read_file).
    """
    roots = _allowed_roots or []
    root_path = _resolve_and_check(root, roots)

    if not root_path.exists():
        raise FileNotFoundError(f"{root!r} does not exist")
    if not root_path.is_dir():
        raise NotADirectoryError(f"{root!r} is not a directory")

    flags = re.IGNORECASE if case_insensitive else 0
    try:
        regex = re.compile(pattern, flags)
    except re.error as e:
        raise ValueError(f"invalid regex {pattern!r}: {e}") from e

    matches: list[dict[str, Any]] = []
    files_scanned = 0
    files_skipped_size = 0
    files_skipped_binary = 0
    files_denied = 0
    truncated = False

    # rglob with optional include_glob (e.g., "*.py")
    walker = (
        root_path.rglob(include_glob) if include_glob
        else root_path.rglob("*")
    )
    for child in walker:
        if not child.is_file():
            continue
        try:
            resolved_child = _resolve_and_check(str(child), roots)
        except PermissionError:
            files_denied += 1
            continue
        try:
            size = resolved_child.stat().st_size
        except OSError:
            continue
        if size > _GREP_MAX_FILE_BYTES:
            files_skipped_size += 1
            continue

        try:
            with open(resolved_child, "r", encoding="utf-8", errors="strict") as f:
                file_match_count = 0
                for line_no, line in enumerate(f, start=1):
                    if file_match_count >= max_matches_per_file:
                        break
                    if regex.search(line):
                        matches.append({
                            "path": str(resolved_child),
                            "line": line_no,
                            "content": line.rstrip("\n")[:300],
                        })
                        file_match_count += 1
                        if len(matches) >= max_results:
                            truncated = True
                            break
        except UnicodeDecodeError:
            files_skipped_binary += 1
            continue
        except OSError:
            continue

        files_scanned += 1
        if truncated:
            break

    return {
        "pattern": pattern,
        "root": str(root_path),
        "match_count": len(matches),
        "matches": matches,
        "files_scanned": files_scanned,
        "files_skipped_size": files_skipped_size,
        "files_skipped_binary": files_skipped_binary,
        "files_denied_by_allowlist": files_denied,
        "truncated": truncated,
    }


def _atomic_write_text(target: Path, content: str) -> int:
    """Write ``content`` to ``target`` atomically via temp + rename.

    Same-filesystem rename is atomic on every POSIX. If we crash
    mid-write, the target either has the old contents (if it existed
    and we were overwriting) or doesn't exist (if we were creating).
    No partial-write state.

    Returns bytes written.
    """
    tmp = target.with_suffix(target.suffix + ".windy.tmp")
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = content.encode("utf-8")
    with open(tmp, "wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    return len(raw)


def _write_file_handler(
    *, path: str, content: str,
    overwrite: bool = False,
    dry_run: bool = False,
    _allowed_roots: list[str] | None = None,
    _journal_path: str | None = None,
) -> dict[str, Any]:
    """Write a text file. Atomic via temp + rename.

    Default ``overwrite=false`` rejects writes to existing paths
    (Tier.WRITE_LOCAL_SAFE — USER+). Pass ``overwrite=true`` to
    replace an existing file (the runtime tier escalation in the
    Capability descriptor bumps the band requirement to TRUSTED+).

    ``dry_run=true`` returns the plan without writing — uniform shape
    across every Wave 4+ destructive capability.
    """
    from windyfly.agent.capabilities.undo_journal import (
        append_record, capture_file_state,
    )

    roots = _allowed_roots or []
    resolved = _resolve_and_check(path, roots)

    exists = resolved.exists()
    if exists and not overwrite:
        raise FileExistsError(
            f"{path!r} already exists; pass overwrite=true to replace "
            "(elevates the call to Tier.WRITE_DESTRUCTIVE / TRUSTED+ band)"
        )
    if exists and resolved.is_dir():
        raise IsADirectoryError(
            f"{path!r} is a directory; refusing to overwrite as a file"
        )

    plan = {
        "action": "overwrite" if exists else "create",
        "target": str(resolved),
        "would_write_bytes": len(content.encode("utf-8")),
        "exists": exists,
        "side_effects": (
            [f"overwrites existing {resolved.stat().st_size} bytes"]
            if exists else
            [f"creates new file in {resolved.parent}"]
        ),
    }

    if dry_run:
        return {"plan": plan, "executed": False, "preview_only": True}

    # Capture original_state for undo BEFORE writing — if the write
    # fails after journal write but before completion, we still have
    # the breadcrumb to recover.
    record_id = None
    if exists:
        original = capture_file_state(resolved)
        record_id = append_record(
            capability_id="fs.write_file",
            action="overwrite",
            target=str(resolved),
            original_state=original,
            journal_path=_journal_path,
        )

    written = _atomic_write_text(resolved, content)

    return {
        "plan": plan,
        "executed": True,
        "atomic": True,
        "bytes_written": written,
        "undo_record_id": record_id,
        "outcome_score": 1.0,
    }


def _move_file_handler(
    *, source: str, destination: str,
    dry_run: bool = False,
    _allowed_roots: list[str] | None = None,
    _journal_path: str | None = None,
) -> dict[str, Any]:
    """Move a file from source to destination, both inside allowlist."""
    from windyfly.agent.capabilities.undo_journal import append_record

    roots = _allowed_roots or []
    src_resolved = _resolve_and_check(source, roots)
    dst_resolved = _resolve_and_check(destination, roots)

    if not src_resolved.exists():
        raise FileNotFoundError(f"source {source!r} does not exist")
    if dst_resolved.exists():
        raise FileExistsError(
            f"destination {destination!r} already exists; refusing "
            "to clobber (use fs.write_file with overwrite=true to overwrite)"
        )

    plan = {
        "action": "move",
        "source": str(src_resolved),
        "target": str(dst_resolved),
        "side_effects": [f"moves {src_resolved.name} → {dst_resolved}"],
    }

    if dry_run:
        return {"plan": plan, "executed": False, "preview_only": True}

    record_id = append_record(
        capability_id="fs.move_file",
        action="move",
        target=str(dst_resolved),
        original_state=None,
        extra={"source": str(src_resolved)},
        journal_path=_journal_path,
    )

    dst_resolved.parent.mkdir(parents=True, exist_ok=True)
    src_resolved.rename(dst_resolved)

    return {
        "plan": plan,
        "executed": True,
        "undo_record_id": record_id,
        "outcome_score": 1.0,
    }


def _delete_file_handler(
    *, path: str,
    dry_run: bool = False,
    _allowed_roots: list[str] | None = None,
    _journal_path: str | None = None,
) -> dict[str, Any]:
    """Delete a file. Captures original state to the undo journal first
    so fs.undo_last_action can resurrect it (within the journal
    retention window)."""
    from windyfly.agent.capabilities.undo_journal import (
        append_record, capture_file_state, MAX_ORIGINAL_STATE_BYTES,
    )

    roots = _allowed_roots or []
    resolved = _resolve_and_check(path, roots)

    if not resolved.exists():
        raise FileNotFoundError(f"{path!r} does not exist")
    if resolved.is_dir():
        raise IsADirectoryError(
            f"{path!r} is a directory; fs.delete_file refuses dirs "
            "(use fs.delete_directory in Wave 4 #2)"
        )

    size = resolved.stat().st_size
    plan = {
        "action": "delete",
        "target": str(resolved),
        "size_bytes": size,
        "undo_supported": size <= MAX_ORIGINAL_STATE_BYTES,
        "side_effects": [f"removes 1 file", f"frees {size} bytes"],
    }

    if dry_run:
        return {"plan": plan, "executed": False, "preview_only": True}

    original = capture_file_state(resolved)
    record_id = append_record(
        capability_id="fs.delete_file",
        action="delete",
        target=str(resolved),
        original_state=original,
        journal_path=_journal_path,
    )

    resolved.unlink()

    return {
        "plan": plan,
        "executed": True,
        "undo_record_id": record_id,
        "outcome_score": 1.0,
    }


def _undo_last_action_handler(
    *, record_id: str | None = None,
    _journal_path: str | None = None,
) -> dict[str, Any]:
    """Undo the most recent (or named) destructive action.

    The marketing moment: 'I just deleted 47 files. Undo.' → reverses.
    Hermes doesn't ship this. OpenClaw doesn't ship this. We do, by
    construction.
    """
    from windyfly.agent.capabilities.undo_journal import (
        find_record, latest_undoable_record, mark_undone, restore_from_record,
    )

    record = (
        find_record(record_id, _journal_path)
        if record_id
        else latest_undoable_record(_journal_path)
    )
    if record is None:
        return {
            "executed": False,
            "reason": (
                f"no undoable record found"
                + (f" with id {record_id}" if record_id else "")
            ),
        }

    if record.get("undone"):
        return {
            "executed": False,
            "reason": f"record {record['id']} was already undone at {record.get('undone_at')}",
        }

    restoration = restore_from_record(record)
    mark_undone(record["id"], _journal_path)

    return {
        "executed": True,
        "record_id": record["id"],
        "original_action": record["action"],
        "original_capability": record["capability_id"],
        "restored": restoration,
        "outcome_score": 1.0,
    }


def _default_allowed_roots() -> list[str]:
    """Default allowlist if config doesn't override.

    Just the user's home directory. The always-deny list above keeps
    .ssh / .aws / .env / etc. unreachable even though they live under
    home. Tighter per-deployment lockdown belongs in
    ``[capabilities.filesystem] allowed_roots = [...]`` in
    ``windyfly.toml``.
    """
    return [str(Path.home())]


def register_filesystem_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register fs.read_file and fs.list_directory on ``registry``.

    Pulls allowed_roots from ``config["capabilities"]["filesystem"]
    ["allowed_roots"]`` if present, otherwise defaults to ``~/``.
    """
    fs_cfg = (config or {}).get("capabilities", {}).get("filesystem", {})
    allowed_roots: list[str] = fs_cfg.get(
        "allowed_roots", _default_allowed_roots(),
    )
    logger.info(
        "Registering filesystem capabilities with allowed_roots=%s",
        allowed_roots,
    )

    def read_file(*, path: str, max_bytes: int = _DEFAULT_MAX_BYTES) -> dict[str, Any]:
        return _read_file_handler(
            path=path, max_bytes=max_bytes, _allowed_roots=allowed_roots,
        )

    def list_directory(*, path: str) -> dict[str, Any]:
        return _list_directory_handler(
            path=path, _allowed_roots=allowed_roots,
        )

    registry.register(Capability(
        id="fs.read_file",
        description=(
            "Read the contents of a text file at the given path. "
            "Returns up to max_bytes (default 100KB) of UTF-8 content. "
            "Binary files return a hint instead of contents. Path must "
            "be inside the agent's allowed roots."
        ),
        handler=read_file,
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-expanded path to a text file.",
                },
                "max_bytes": {
                    "type": "integer",
                    "description": "Cap on bytes returned (default 102400).",
                },
            },
            "required": ["path"],
        },
        tier=Tier.READ_EXTERNAL,
        scope="filesystem_allowlist",
    ))

    registry.register(Capability(
        id="fs.list_directory",
        description=(
            "List entries in a directory, including type (file/dir/symlink), "
            "size, and modified time. Path must be inside the agent's "
            "allowed roots."
        ),
        handler=list_directory,
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-expanded directory path.",
                },
            },
            "required": ["path"],
        },
        tier=Tier.READ_EXTERNAL,
        scope="filesystem_allowlist",
    ))

    def fs_glob(*, pattern: str, max_results: int = _DEFAULT_GLOB_MAX_RESULTS) -> dict[str, Any]:
        return _glob_handler(
            pattern=pattern, max_results=max_results,
            _allowed_roots=allowed_roots,
        )

    def fs_grep(
        *, pattern: str, root: str,
        include_glob: str | None = None,
        max_results: int = _DEFAULT_GREP_MAX_RESULTS,
        max_matches_per_file: int = _DEFAULT_GREP_MAX_MATCHES_PER_FILE,
        case_insensitive: bool = False,
    ) -> dict[str, Any]:
        return _grep_files_handler(
            pattern=pattern, root=root,
            include_glob=include_glob,
            max_results=max_results,
            max_matches_per_file=max_matches_per_file,
            case_insensitive=case_insensitive,
            _allowed_roots=allowed_roots,
        )

    registry.register(Capability(
        id="fs.glob",
        description=(
            "Glob a path pattern (e.g., '~/projects/**/*.py') and return "
            "matching paths with type/size. Pattern uses standard glob "
            "syntax with ** for recursive. Results filtered to allowed "
            "roots — anything outside is silently dropped (not an error). "
            "Capped at max_results (default 100)."
        ),
        handler=fs_glob,
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern; ** matches recursively.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on matches returned (default 100).",
                },
            },
            "required": ["pattern"],
        },
        tier=Tier.READ_EXTERNAL,
        scope="filesystem_allowlist",
    ))

    registry.register(Capability(
        id="fs.grep_files",
        description=(
            "Grep a regex across files under root (recursively). Returns "
            "up to max_results matches as {path, line, content}. Skips "
            "files >5MB and binary files. Optional include_glob filter "
            "(e.g., '*.py'). Use case_insensitive=true for case-blind "
            "search. Path must be inside allowed roots."
        ),
        handler=fs_grep,
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Python regex to match line-by-line.",
                },
                "root": {
                    "type": "string",
                    "description": "Directory to recursively grep under.",
                },
                "include_glob": {
                    "type": "string",
                    "description": "Optional file-name glob filter (e.g., '*.py').",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Cap on total matches across files (default 100).",
                },
                "max_matches_per_file": {
                    "type": "integer",
                    "description": "Cap on matches per single file (default 20).",
                },
                "case_insensitive": {
                    "type": "boolean",
                    "description": "Case-insensitive matching.",
                },
            },
            "required": ["pattern", "root"],
        },
        tier=Tier.READ_EXTERNAL,
        scope="filesystem_allowlist",
    ))

    # ── Wave 4 #1: write hands ──────────────────────────────────────

    journal_path = fs_cfg.get("undo_journal_path")  # None → default ~/.windy/undo-journal.ndjson

    def fs_write_file(
        *, path: str, content: str,
        overwrite: bool = False,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _write_file_handler(
            path=path, content=content,
            overwrite=overwrite, dry_run=dry_run,
            _allowed_roots=allowed_roots,
            _journal_path=journal_path,
        )

    def fs_move_file(
        *, source: str, destination: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        return _move_file_handler(
            source=source, destination=destination, dry_run=dry_run,
            _allowed_roots=allowed_roots,
            _journal_path=journal_path,
        )

    def fs_delete_file(
        *, path: str, dry_run: bool = False,
    ) -> dict[str, Any]:
        return _delete_file_handler(
            path=path, dry_run=dry_run,
            _allowed_roots=allowed_roots,
            _journal_path=journal_path,
        )

    def fs_undo_last_action(
        *, record_id: str | None = None,
    ) -> dict[str, Any]:
        return _undo_last_action_handler(
            record_id=record_id,
            _journal_path=journal_path,
        )

    def _write_file_runtime_check(args: dict[str, Any]) -> Tier | None:
        """Bump fs.write_file to WRITE_DESTRUCTIVE when overwrite=true.

        The blessed Wave 4 design (Decision W4-4): write_file ships at
        Tier.WRITE_LOCAL_SAFE statically (USER+); when called with
        overwrite=true, the runtime escalation hook bumps it to
        Tier.WRITE_DESTRUCTIVE (TRUSTED+) so grandma's USER-band
        instance can't accidentally overwrite files.
        """
        if args.get("overwrite") is True:
            return Tier.WRITE_DESTRUCTIVE
        return None

    registry.register(Capability(
        id="fs.write_file",
        description=(
            "Write text content to a file atomically (temp + rename). "
            "Default refuses to overwrite existing files; pass "
            "overwrite=true to replace (this elevates the call to a "
            "higher band requirement automatically). Use dry_run=true "
            "to preview the action without writing. Path must be inside "
            "the agent's allowed roots."
        ),
        handler=fs_write_file,
        input_schema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or ~-expanded target path.",
                },
                "content": {
                    "type": "string",
                    "description": "UTF-8 text content to write.",
                },
                "overwrite": {
                    "type": "boolean",
                    "description": (
                        "If true, replace an existing file. Elevates the "
                        "call to TRUSTED+ band requirement at runtime."
                    ),
                },
                "dry_run": {
                    "type": "boolean",
                    "description": "If true, return the plan without writing.",
                },
            },
            "required": ["path", "content"],
        },
        tier=Tier.WRITE_LOCAL_SAFE,
        scope="filesystem_allowlist",
        runtime_tier_check=_write_file_runtime_check,
    ))

    registry.register(Capability(
        id="fs.move_file",
        description=(
            "Move/rename a file from source to destination. Both must "
            "be inside allowed roots. Refuses to clobber an existing "
            "destination — use fs.write_file with overwrite=true for "
            "that. Logs an undo record so fs.undo_last_action can "
            "reverse the move."
        ),
        handler=fs_move_file,
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["source", "destination"],
        },
        tier=Tier.WRITE_DESTRUCTIVE,
        scope="filesystem_allowlist",
    ))

    registry.register(Capability(
        id="fs.delete_file",
        description=(
            "Delete a single file. Captures the file's contents to the "
            "undo journal first so fs.undo_last_action can restore it "
            "(within the journal's 30-day retention window, for files "
            "≤5MB). Refuses directories; use fs.delete_directory in a "
            "future PR for that. dry_run=true returns the plan without "
            "deleting."
        ),
        handler=fs_delete_file,
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "dry_run": {"type": "boolean"},
            },
            "required": ["path"],
        },
        tier=Tier.WRITE_DESTRUCTIVE,
        scope="filesystem_allowlist",
    ))

    registry.register(Capability(
        id="fs.undo_last_action",
        description=(
            "Reverse the most recent destructive action recorded in "
            "the undo journal (fs.write_file overwrite, fs.move_file, "
            "fs.delete_file). Pass record_id to undo a specific record "
            "instead of the latest. Returns {executed, restored, ...}. "
            "Some actions can't be undone (e.g., delete of a >5MB file)."
        ),
        handler=fs_undo_last_action,
        input_schema={
            "type": "object",
            "properties": {
                "record_id": {
                    "type": "string",
                    "description": (
                        "Optional. Specific journal record id. "
                        "If omitted, undoes the latest unundone record."
                    ),
                },
            },
            "required": [],
        },
        tier=Tier.WRITE_LOCAL_SAFE,
        scope="filesystem_allowlist",
    ))
