"""Seed the agent's nodes table from Claude Code user-memory files.

The agent's ``nodes`` table starts empty for a fresh instance. The
agent (and any collaborator that filters by topic_keywords) then has
no domain context to draw on — so when the user says "Polly," the
collaborator confabulates AWS Polly TTS instead of recognizing the
mortgage pricing engine the user is actually building.

This module reads the user's persistent memory directory (default:
``~/.claude/projects/-Users-<user>/memory/``) and upserts each file
into the agent's nodes table. The frontmatter fields become metadata;
the body becomes the searchable text. After running, the agent has
real domain context to filter by.

Idempotent — re-running upserts existing nodes with the same
(type, name) key.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node

logger = logging.getLogger(__name__)

# Default memory dir for Grant's machine; configurable via
# WINDYFLY_USER_MEMORY_DIR env var or per-call argument.
DEFAULT_USER_MEMORY_DIR = (
    Path.home() / ".claude" / "projects" / "-Users-thewindstorm" / "memory"
)

# Cap stored body text per node so the nodes table doesn't bloat with
# multi-thousand-line memory files. The agent's prompt-assembly layer
# is the one that decides how much memory to pass; here we just
# preserve enough to be useful.
_MAX_BODY_CHARS = 4000

# YAML frontmatter regex — matches the leading ``---\n...\n---\n`` block
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter_dict, body_after_frontmatter).

    Lightweight YAML — only handles ``key: value`` lines, no nested
    structures or lists. Sufficient for the user-memory format which
    uses name/description/type/originSessionId only.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    raw_fm = m.group(1)
    body = text[m.end():]

    fm: dict[str, str] = {}
    for line in raw_fm.split("\n"):
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        fm[key.strip()] = value.strip()
    return fm, body


def _slugify(name: str) -> str:
    """Lowercase + collapse whitespace to dashes for a stable node-name key."""
    return re.sub(r"\s+", "-", name.strip().lower())


def seed_from_user_memory(
    db: Database,
    *,
    memory_dir: str | None = None,
    skip_index: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Read every .md file in ``memory_dir`` and upsert as nodes.

    Args:
        db: target database (the agent's nodes table goes here)
        memory_dir: override path; defaults to env var or
            ``~/.claude/projects/-Users-thewindstorm/memory``
        skip_index: don't import the MEMORY.md index file (it's just
            pointers to other files, not domain content)
        dry_run: parse + report without writing

    Returns dict with summary counts + per-file outcomes.
    """
    dir_path = Path(
        memory_dir
        or os.environ.get("WINDYFLY_USER_MEMORY_DIR", "")
        or str(DEFAULT_USER_MEMORY_DIR)
    ).expanduser()

    if not dir_path.exists():
        return {
            "imported": 0,
            "skipped": 0,
            "errors": 0,
            "memory_dir": str(dir_path),
            "error": f"memory_dir does not exist: {dir_path}",
        }

    imported: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []

    for md_file in sorted(dir_path.glob("*.md")):
        if skip_index and md_file.name == "MEMORY.md":
            skipped.append(md_file.name)
            continue
        try:
            text = md_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            errors.append({"file": md_file.name, "error": str(e)})
            continue

        fm, body = _parse_frontmatter(text)
        # Fall back to filename-derived name/type when frontmatter is
        # missing — better to import as a generic memory node than to
        # silently skip.
        name = fm.get("name") or md_file.stem.replace("_", " ").title()
        node_type = "memory." + (fm.get("type") or "note")
        body_truncated = body[:_MAX_BODY_CHARS]
        truncated = len(body) > _MAX_BODY_CHARS

        metadata = {
            "source_file": md_file.name,
            "description": fm.get("description", ""),
            "body": body_truncated,
            "body_truncated": truncated,
            "body_total_chars": len(body),
        }

        if dry_run:
            imported.append(f"{node_type}/{name} (dry-run)")
            continue

        try:
            upsert_node(
                db,
                type=node_type,
                name=name,
                metadata=metadata,
                epistemic_status="user_stated",
                confidence=1.0,
                source=f"user_memory:{md_file.name}",
            )
            imported.append(f"{node_type}/{name}")
        except Exception as e:
            errors.append({"file": md_file.name, "error": str(e)})

    return {
        "imported": len(imported),
        "skipped": len(skipped),
        "errors": len(errors),
        "memory_dir": str(dir_path),
        "imported_nodes": imported,
        "skipped_files": skipped,
        "error_details": errors,
        "dry_run": dry_run,
    }
