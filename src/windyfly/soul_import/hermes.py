"""Hermes agent export parser for Soul Continuity.

Parses a Hermes agent export (SQLite sessions DB, MEMORY.md, skills/).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_hermes(export_path: str) -> dict[str, Any]:
    """Parse a Hermes agent export.

    Reads SQLite sessions DB, MEMORY.md, and skills/ from the export
    directory and returns a standardized import structure.

    Args:
        export_path: Path to the Hermes export directory.

    Returns:
        Standardized import dict.
    """
    path = Path(export_path)
    result: dict[str, Any] = {
        "personality": {},
        "memories": [],
        "skills": [],
        "source": "hermes",
    }

    # Parse sessions SQLite database
    db_candidates = list(path.glob("*.db")) + list(path.glob("sessions.*"))
    for db_file in db_candidates:
        if db_file.suffix in (".db", ".sqlite", ".sqlite3"):
            try:
                result["memories"].extend(_extract_from_db(str(db_file)))
            except Exception as e:
                logger.warning("Failed to parse Hermes DB %s: %s", db_file, e)

    # Parse MEMORY.md
    memory_file = path / "MEMORY.md"
    if memory_file.exists():
        memory_text = memory_file.read_text(encoding="utf-8")
        for line in memory_text.split("\n"):
            line = line.strip()
            if line.startswith("- ") or line.startswith("* "):
                content = line.lstrip("-* ").strip()
                if content:
                    result["memories"].append({
                        "type": "fact",
                        "content": content,
                        "confidence": 0.5,
                        "source": "imported_hermes",
                    })

    # Parse skills/ directory
    skills_dir = path / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        for skill_file in skills_dir.iterdir():
            if skill_file.is_file():
                try:
                    code = skill_file.read_text(encoding="utf-8")
                    lang = "python" if skill_file.suffix == ".py" else skill_file.suffix.lstrip(".")
                    result["skills"].append({
                        "name": skill_file.stem,
                        "code": code,
                        "language": lang or "unknown",
                    })
                except Exception as e:
                    logger.warning("Failed to read skill %s: %s", skill_file, e)

    return result


def _extract_from_db(db_path: str) -> list[dict[str, Any]]:
    """Extract memories from a Hermes sessions database."""
    memories: list[dict[str, Any]] = []

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Try common Hermes table structures
    try:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = {row[0] for row in cursor.fetchall()}

        # Check for memories/facts table
        for table in ["memories", "facts", "knowledge", "notes"]:
            if table in tables:
                rows = conn.execute(f"SELECT * FROM {table} LIMIT 100").fetchall()
                for row in rows:
                    row_dict = dict(row)
                    content = row_dict.get("content") or row_dict.get("text") or row_dict.get("value", "")
                    if content:
                        memories.append({
                            "type": "fact",
                            "content": str(content),
                            "confidence": 0.5,
                            "source": "imported_hermes",
                        })

        # Check for sessions/conversations table
        for table in ["sessions", "conversations", "messages"]:
            if table in tables:
                rows = conn.execute(
                    f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 50"
                ).fetchall()
                for row in rows:
                    row_dict = dict(row)
                    content = row_dict.get("content") or row_dict.get("message") or row_dict.get("text", "")
                    role = row_dict.get("role", "unknown")
                    if content and role == "user":
                        memories.append({
                            "type": "conversation",
                            "content": str(content),
                            "confidence": 0.3,
                            "source": "imported_hermes",
                        })
    except Exception as e:
        logger.warning("Error reading Hermes DB: %s", e)
    finally:
        conn.close()

    return memories
