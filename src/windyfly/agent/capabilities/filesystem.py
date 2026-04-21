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

import logging
import os
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

# Bytes returned by read_file unless the caller overrides. 100KB caps
# the LLM context burn — anything bigger should go through a chunked
# read or a grep, both future capabilities.
_DEFAULT_MAX_BYTES = 100 * 1024

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
