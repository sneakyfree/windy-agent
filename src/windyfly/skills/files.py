"""SKILL.md file catalog — skills as human-readable, portable files.

The 2026-07-04 audit's Hermes lesson: learned capability must live in
files the user can read, diff, git-commit, and carry between agents —
not in an opaque SQLite blob. This module makes the skills directory
(``windy_state_dir()/skills/*.md``) the human-facing mirror of the DB
skill lifecycle:

- **Ingest** at boot: SKILL.md files become promoted ``playbook``
  skills (create-or-reversion on content change). Drop a skill file in,
  restart (or /reload-skills), and the agent knows it.
- **Export** on runtime authoring: when the agent saves a skill via the
  ``skill.save`` capability, the file appears here for the human to
  review, edit, or delete.

Format (AgentSkills-style — portable to/from Hermes, OpenClaw, Claude
Code):

    ---
    name: deploy-website
    description: How to deploy the site end to end
    tags: deploy, cloudflare
    ---
    1. Run wrangler deploy from the site directory
    2. Purge the Cloudflare cache
    ...

Playbook skills are INSTRUCTIONS the agent loads into context on
demand (progressive disclosure) — they are never executed as code, so
the code-sandbox problem doesn't apply to them.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from windyfly.platform import windy_state_dir

logger = logging.getLogger(__name__)

MAX_SKILL_BODY_CHARS = 8000
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")


def skills_dir() -> Path:
    return windy_state_dir() / "skills"


def sanitize_skill_name(name: str) -> str | None:
    """Kebab-case the name; None if it can't be made safe."""
    slug = re.sub(r"[^a-z0-9._-]+", "-", (name or "").strip().lower())
    slug = slug.strip("-._")[:64]
    return slug if slug and _NAME_RE.match(slug) else None


def parse_skill_file(text: str) -> dict[str, Any] | None:
    """Parse frontmatter + body. Minimal parser — no YAML dependency.

    Returns {name, description, tags, body} or None if malformed.
    """
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)$", text, re.S)
    if not m:
        return None
    meta: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip().lower()] = value.strip()
    name = sanitize_skill_name(meta.get("name", ""))
    body = m.group(2).strip()
    if not name or not body:
        return None
    return {
        "name": name,
        "description": meta.get("description", "")[:200],
        "tags": meta.get("tags", ""),
        "body": body[:MAX_SKILL_BODY_CHARS],
    }


def render_skill_file(
    *, name: str, description: str, body: str, tags: str = "",
) -> str:
    parts = ["---", f"name: {name}", f"description: {description}"]
    if tags:
        parts.append(f"tags: {tags}")
    parts += ["---", "", body.strip(), ""]
    return "\n".join(parts)


def export_skill_to_file(
    *, name: str, description: str, body: str, tags: str = "",
) -> Path | None:
    """Write/overwrite the SKILL.md mirror for a skill (best-effort)."""
    try:
        d = skills_dir()
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{name}.md"
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(
            render_skill_file(
                name=name, description=description, body=body, tags=tags,
            ),
            encoding="utf-8",
        )
        tmp.replace(path)
        return path
    except OSError as e:
        logger.warning("skill file export failed for %s: %s", name, e)
        return None


def sync_skill_files(db: Any) -> dict[str, int]:
    """Ingest ``skills_dir()/*.md`` into the DB skill lifecycle.

    New file → new promoted playbook skill. Changed body → new version
    (created + promoted; the old version stays in history for
    rollback). Unchanged → untouched. Returns counters for logging.
    """
    from windyfly.memory.skills import get_skill_by_name
    from windyfly.skills.manager import create_skill, promote_skill

    stats = {"ingested": 0, "updated": 0, "unchanged": 0, "malformed": 0}
    d = skills_dir()
    if not d.is_dir():
        return stats

    for path in sorted(d.glob("*.md")):
        try:
            parsed = parse_skill_file(path.read_text(encoding="utf-8"))
        except OSError:
            parsed = None
        if parsed is None:
            stats["malformed"] += 1
            logger.warning("skill file %s is malformed — skipped", path.name)
            continue

        existing = get_skill_by_name(db, parsed["name"])
        if existing and (existing.get("code") or "").strip() == parsed["body"]:
            stats["unchanged"] += 1
            continue

        skill_id = create_skill(
            db,
            name=parsed["name"],
            code=parsed["body"],
            language="playbook",
            description=parsed["description"] or None,
            risk_level="low",
        )
        promote_skill(db, skill_id)
        stats["updated" if existing else "ingested"] += 1

    if stats["ingested"] or stats["updated"]:
        logger.info("skill file sync: %s", stats)
    return stats
