"""OpenClaw export parser for Soul Continuity.

Parses an OpenClaw agent export directory (SOUL.md, MEMORY.md,
skills/*.md, config.yaml) and returns a standardized import dict.
"""

from __future__ import annotations

import json
import logging
import re
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

    # The tone in config.yaml or config.json (top-level humor / formality / verbosity numbers only).
    # Read without PyYAML, which is not a runtime dependency: with it missing, the tone of every
    # OpenClaw export was silently skipped.
    result["personality"].update(_config_tone(path))

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


_TONE_LINE = re.compile(r"^(humor|formality|verbosity)\s*:\s*(\d+(?:\.\d+)?)\s*(?:#.*)?$")


def _config_tone(path: Path) -> dict[str, float]:
    tone: dict[str, float] = {}
    yaml_file, json_file = path / "config.yaml", path / "config.json"
    try:
        if yaml_file.is_file():
            for line in yaml_file.read_text(encoding="utf-8", errors="replace").splitlines():
                m = _TONE_LINE.match(line.rstrip())
                if m:
                    tone[m.group(1)] = float(m.group(2))
        elif json_file.is_file():
            data = json.loads(json_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                tone = {k: data[k] for k in ("humor", "formality", "verbosity")
                        if isinstance(data.get(k), (int, float)) and not isinstance(data.get(k), bool)}
    except (OSError, ValueError) as e:
        logger.warning("OpenClaw config not read (%s)", type(e).__name__)
    return tone

