"""OpenClaw export parser for Soul Continuity.

Parses an OpenClaw agent export directory (SOUL.md, MEMORY.md,
skills/*.md, config.yaml) and returns a standardized import dict.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def parse_openclaw(export_path: str) -> dict[str, Any]:
    """Parse an OpenClaw agent export.

    Reads SOUL.md, MEMORY.md, skills/*.md, and config.yaml from
    the export directory and returns a standardized import structure.

    Args:
        export_path: Path to the OpenClaw export directory.

    Returns:
        Standardized import dict with personality, memories, skills, and source.
    """
    path = Path(export_path)
    result: dict[str, Any] = {
        "personality": {},
        "memories": [],
        "skills": [],
        "source": "openclaw",
    }

    # Parse SOUL.md for personality traits
    soul_file = path / "SOUL.md"
    if soul_file.exists():
        soul_text = soul_file.read_text(encoding="utf-8")
        result["personality"] = _extract_personality(soul_text)

    # Parse MEMORY.md for memories/facts
    memory_file = path / "MEMORY.md"
    if memory_file.exists():
        memory_text = memory_file.read_text(encoding="utf-8")
        result["memories"] = _extract_memories(memory_text)

    # Parse skills/ directory
    skills_dir = path / "skills"
    if skills_dir.exists() and skills_dir.is_dir():
        for skill_file in skills_dir.glob("*.md"):
            skill_content = skill_file.read_text(encoding="utf-8")
            result["skills"].append({
                "name": skill_file.stem,
                "code": skill_content,
                "language": "markdown",
            })

    # Parse config.yaml for additional settings
    config_file = path / "config.yaml"
    if config_file.exists():
        try:
            import yaml  # noqa: F811
            config_data = yaml.safe_load(config_file.read_text(encoding="utf-8"))
            if isinstance(config_data, dict):
                if "humor" in config_data:
                    result["personality"]["humor"] = config_data["humor"]
                if "formality" in config_data:
                    result["personality"]["formality"] = config_data["formality"]
        except ImportError:
            logger.debug("PyYAML not installed, skipping config.yaml parsing")
        except Exception as e:
            logger.warning("Failed to parse config.yaml: %s", e)

    # Mark all imports with low confidence
    for memory in result["memories"]:
        memory.setdefault("confidence", 0.5)

    return result


def _extract_personality(soul_text: str) -> dict[str, Any]:
    """Extract personality traits from SOUL.md text."""
    traits: list[str] = []
    personality: dict[str, Any] = {"traits": traits}

    for line in soul_text.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            trait = line.lstrip("-* ").strip()
            if trait:
                traits.append(trait)

    return personality


def _extract_memories(memory_text: str) -> list[dict[str, Any]]:
    """Extract memory items from MEMORY.md text."""
    memories: list[dict[str, Any]] = []

    for line in memory_text.split("\n"):
        line = line.strip()
        if line.startswith("- ") or line.startswith("* "):
            content = line.lstrip("-* ").strip()
            if content:
                # Classify memory type
                mem_type = "fact"
                if any(w in content.lower() for w in ["prefer", "like", "favorite", "love"]):
                    mem_type = "preference"
                elif any(w in content.lower() for w in ["believe", "think", "feel"]):
                    mem_type = "belief"
                elif any(w in content.lower() for w in ["name is", "lives in", "works"]):
                    mem_type = "identity"

                memories.append({
                    "type": mem_type,
                    "content": content,
                    "confidence": 0.5,
                    "source": "imported_openclaw",
                })

    return memories
