"""Soul import orchestrator.

Coordinates the full import flow: detect source, parse, preview,
and (on approval) write to database.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node
from windyfly.memory.skills import save_skill
from windyfly.soul_import.chatgpt import parse_chatgpt
from windyfly.soul_import.hermes import parse_hermes
from windyfly.soul_import.openclaw import parse_openclaw
from windyfly.soul_import.preview import classify_memory, format_soul_preview

logger = logging.getLogger(__name__)

_PARSERS = {
    "openclaw": parse_openclaw,
    "hermes": parse_hermes,
    "chatgpt": parse_chatgpt,
}


def detect_source_type(export_path: str) -> str | None:
    """Auto-detect the source type from file structure.

    Args:
        export_path: Path to the export directory.

    Returns:
        Source type string or None if unrecognized.
    """
    path = Path(export_path)
    if not path.exists():
        return None

    if (path / "conversations.json").exists():
        return "chatgpt"
    if (path / "SOUL.md").exists() and (path / "config.yaml").exists():
        return "openclaw"
    if any(path.glob("*.db")) or any(path.glob("sessions.*")):
        return "hermes"
    if (path / "SOUL.md").exists():
        return "openclaw"
    if (path / "MEMORY.md").exists():
        return "hermes"

    return None


def import_soul(
    db: Database,
    export_path: str,
    source_type: str | None = None,
    *,
    user_approved: bool = False,
) -> dict[str, Any]:
    """Import soul data from another agent's export.

    Flow:
    1. Detect or use provided source_type
    2. Parse the export with the appropriate parser
    3. Generate Soul Preview
    4. If not approved: return preview (don't write)
    5. If approved: write safe items, flag sensitive items, sandbox skills

    Args:
        db: Database instance.
        export_path: Path to the export directory.
        source_type: Source type ('openclaw', 'hermes', 'chatgpt') or None for auto-detect.
        user_approved: Whether the user has approved the import.

    Returns:
        Dict with preview text and/or import summary.
    """
    # 1. Detect source type
    if source_type is None:
        source_type = detect_source_type(export_path)
    if source_type is None:
        return {"error": "Could not detect export source type", "preview": None}

    # 2. Parse
    parser = _PARSERS.get(source_type)
    if parser is None:
        return {"error": f"Unknown source type: {source_type}", "preview": None}

    parsed_data = parser(export_path)

    # 3. Generate preview
    preview = format_soul_preview(parsed_data)

    # 4. If not approved, return preview only
    if not user_approved:
        return {
            "preview": preview,
            "parsed_data": parsed_data,
            "source_type": source_type,
            "imported": 0,
            "flagged": 0,
            "skipped": 0,
        }

    # 5. Write to database
    imported = 0
    flagged = 0
    skipped = 0

    source_label = f"imported_{source_type}"

    for memory in parsed_data.get("memories", []):
        classification = classify_memory(memory)
        content = memory.get("content", "")
        mem_type = memory.get("type", "fact")

        if classification == "safe":
            upsert_node(
                db,
                type=mem_type,
                name=content[:200],
                metadata={"raw_content": content, "import_source": source_label},
                confidence=0.5,
                source=source_label,
                epistemic_status="inferred",
            )
            imported += 1
        elif classification == "sensitive":
            upsert_node(
                db,
                type=mem_type,
                name=content[:200],
                metadata={"raw_content": content, "import_source": source_label, "needs_review": True},
                confidence=0.3,
                source=source_label,
                epistemic_status="speculative",
            )
            flagged += 1
        else:
            skipped += 1

    # Import skills as unpromoted
    for skill in parsed_data.get("skills", []):
        save_skill(
            db,
            name=skill["name"],
            code=skill["code"],
            language=skill.get("language", "unknown"),
            description=f"Imported from {source_type}",
            risk_level="medium",
        )
        imported += 1

    logger.info(
        "Soul import complete: %d imported, %d flagged, %d skipped",
        imported, flagged, skipped,
    )

    return {
        "preview": preview,
        "source_type": source_type,
        "imported": imported,
        "flagged": flagged,
        "skipped": skipped,
    }
