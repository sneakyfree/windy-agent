"""skill.* capabilities — the agent's runtime learning surface.

Sprint 3 (2026-07-04 audit): the skills table had a full lifecycle
(versioning, lineage, promote/rollback, golden tests) but NOTHING real
flowed through it — the only writer emitted canned boilerplate and no
LLM-callable surface existed. These capabilities close the loop:

- ``skill.list`` — progressive-disclosure level 0: the compact index
  (names + one-liners). Cheap enough to call any time.
- ``skill.view`` — level 1: load one skill's full playbook body on
  demand. Never inline the whole library into context.
- ``skill.save`` — the write path. The agent (or user, via chat)
  persists a PLAYBOOK: markdown instructions for a workflow it has
  figured out. Playbooks are knowledge, not code — they are loaded
  into context and followed with the agent's normal band-gated
  capabilities, never executed directly, so no new sandbox surface is
  created. Every save is mirrored to ``~/.windy/skills/<name>.md`` for
  human review/edit/deletion (the Hermes portability lesson).

Removal surfaces already exist: ``/forget <name>`` demotes, and
deleting the SKILL.md file removes it from the next boot's sync.
"""

from __future__ import annotations

import logging
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)

MAX_PLAYBOOK_SKILLS_LISTED = 50


def register_skill_learning_capabilities(
    registry: CapabilityRegistry,
    db: Any,
    write_queue: Any | None = None,
    config: dict[str, Any] | None = None,
) -> None:
    """Register skill.list / skill.view / skill.save."""
    logger.info("Registering skill.* capabilities (learning surface)")

    def skill_list() -> dict[str, Any]:
        from windyfly.memory.skills import list_skills

        rows = [
            s for s in list_skills(db, promoted_only=True)
            if s.get("language") == "playbook"
        ][:MAX_PLAYBOOK_SKILLS_LISTED]
        return {
            "count": len(rows),
            "skills": [
                {
                    "name": s["name"],
                    "description": s.get("description") or "",
                    "version": s.get("version"),
                    "times_used": s.get("usage_count") or 0,
                }
                for s in rows
            ],
        }

    def skill_view(*, name: str) -> dict[str, Any]:
        from windyfly.memory.skills import get_skill_by_name
        from windyfly.skills.files import sanitize_skill_name

        slug = sanitize_skill_name(name)
        skill = get_skill_by_name(db, slug) if slug else None
        if not skill or not skill.get("promoted"):
            return {"ok": False, "error": f"no promoted skill named {name!r}"}
        try:
            # Direct tiny update (manager.increment_usage needs a write
            # queue + success verdict; a view is just "was recalled").
            db.execute(
                "UPDATE skills SET usage_count = usage_count + 1, "
                "last_used = CURRENT_TIMESTAMP WHERE id = ?",
                (skill["id"],),
            )
            db.commit()
        except Exception:
            pass
        return {
            "ok": True,
            "name": skill["name"],
            "description": skill.get("description") or "",
            "version": skill.get("version"),
            "body": skill.get("code") or "",
        }

    def skill_save(
        *,
        name: str,
        description: str,
        body: str,
        tags: str = "",
    ) -> dict[str, Any]:
        from windyfly.memory.skills import get_skill_by_name
        from windyfly.skills.files import (
            MAX_SKILL_BODY_CHARS,
            export_skill_to_file,
            sanitize_skill_name,
        )
        from windyfly.skills.manager import create_skill, promote_skill

        slug = sanitize_skill_name(name)
        if not slug:
            return {
                "ok": False,
                "error": "invalid skill name — use kebab-case like "
                         "'deploy-website'",
            }
        body = (body or "").strip()
        if len(body) < 20:
            return {"ok": False, "error": "playbook body too short to be useful"}
        if len(body) > MAX_SKILL_BODY_CHARS:
            return {
                "ok": False,
                "error": f"playbook too long ({len(body)} chars; max "
                         f"{MAX_SKILL_BODY_CHARS}) — split it into two skills",
            }

        existing = get_skill_by_name(db, slug)
        skill_id = create_skill(
            db,
            name=slug,
            code=body,
            language="playbook",
            description=(description or "")[:200] or None,
            risk_level="low",
        )
        if existing:
            # Explicit lineage + version bump — save_skill always
            # inserts version=1 otherwise, which breaks
            # get_skill_by_name's ORDER BY version DESC.
            db.execute(
                "UPDATE skills SET version = ?, parent_skill_id = ? "
                "WHERE id = ?",
                ((existing.get("version") or 1) + 1, existing["id"], skill_id),
            )
            db.commit()
        promote_skill(db, skill_id)
        file_path = export_skill_to_file(
            name=slug,
            description=(description or "")[:200],
            body=body,
            tags=tags,
        )
        return {
            "ok": True,
            "name": slug,
            "version": ((existing.get("version") or 1) + 1) if existing else 1,
            "file": str(file_path) if file_path else None,
            "note": (
                "Saved and promoted. The user can review/edit the file, "
                "or say /forget " + slug + " to remove it."
            ),
        }

    registry.register(Capability(
        id="skill.list",
        description=(
            "List the playbook skills you've learned (names + short "
            "descriptions). Cheap — call whenever a task might match "
            "something you already know how to do, then load the "
            "winner with skill.view."
        ),
        handler=skill_list,
        tier=Tier.PURE_COMPUTE,
        scope="learning",
        input_schema={"type": "object", "properties": {}, "required": []},
    ))

    registry.register(Capability(
        id="skill.view",
        description=(
            "Load one saved playbook skill's full step-by-step body by "
            "name. Use after skill.list finds a match — follow the "
            "steps with your normal tools."
        ),
        handler=skill_view,
        tier=Tier.PURE_COMPUTE,
        scope="learning",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "skill name from skill.list"},
            },
            "required": ["name"],
        },
    ))

    registry.register(Capability(
        id="skill.save",
        description=(
            "Save (or update) a playbook skill so you remember how to "
            "do a workflow forever — across restarts and new sessions. "
            "Use after completing a non-trivial multi-step task, or "
            "when the user teaches you a procedure. Write the body as "
            "numbered steps a future you can follow, including exact "
            "commands/values that worked. Saved skills appear as "
            "editable files in the user's skills folder."
        ),
        handler=skill_save,
        tier=Tier.WRITE_LOCAL_SAFE,
        scope="learning",
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "kebab-case, e.g. deploy-website"},
                "description": {"type": "string", "description": "one line: when to use this"},
                "body": {"type": "string", "description": "the playbook — numbered steps with exact commands"},
                "tags": {"type": "string", "description": "optional comma-separated tags"},
            },
            "required": ["name", "description", "body"],
        },
    ))
