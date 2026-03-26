"""Tests for Soul Continuity import parsers and orchestrator.

Tests OpenClaw, Hermes, and ChatGPT parsers, the soul preview
formatter, the orchestrator's source detection, and the full
import flow.
"""

from __future__ import annotations

import json
import os

from windyfly.memory.database import Database
from windyfly.soul_import.chatgpt import parse_chatgpt
from windyfly.soul_import.hermes import parse_hermes
from windyfly.soul_import.openclaw import parse_openclaw
from windyfly.soul_import.orchestrator import detect_source_type, import_soul
from windyfly.soul_import.preview import classify_memory, format_soul_preview


class TestOpenClawParser:
    def test_parses_soul(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("# Soul\n- Warm and empathetic\n- Witty\n")
        result = parse_openclaw(str(tmp_path))
        assert result["source"] == "openclaw"
        traits = result["personality"]["traits"]
        assert "Warm and empathetic" in traits
        assert "Witty" in traits

    def test_parses_memories(self, tmp_path):
        (tmp_path / "MEMORY.md").write_text("# Memories\n- User prefers dark mode\n- Name is Grant\n")
        result = parse_openclaw(str(tmp_path))
        assert len(result["memories"]) == 2

    def test_parses_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "greet.md").write_text("# Greeting skill\nSay hello nicely")
        result = parse_openclaw(str(tmp_path))
        assert len(result["skills"]) == 1
        assert result["skills"][0]["name"] == "greet"

    def test_empty_directory(self, tmp_path):
        result = parse_openclaw(str(tmp_path))
        assert result["source"] == "openclaw"
        assert result["memories"] == []
        assert result["skills"] == []


class TestHermesParser:
    def test_parses_memory_file(self, tmp_path):
        (tmp_path / "MEMORY.md").write_text("- User likes coffee\n- User is a developer\n")
        result = parse_hermes(str(tmp_path))
        assert result["source"] == "hermes"
        assert len(result["memories"]) == 2

    def test_parses_skills(self, tmp_path):
        skills_dir = tmp_path / "skills"
        skills_dir.mkdir()
        (skills_dir / "calc.py").write_text("def add(a, b): return a + b")
        result = parse_hermes(str(tmp_path))
        assert len(result["skills"]) == 1
        assert result["skills"][0]["language"] == "python"

    def test_empty_directory(self, tmp_path):
        result = parse_hermes(str(tmp_path))
        assert result["memories"] == []


class TestChatGPTParser:
    def test_parses_conversations(self, tmp_path):
        convs = [
            {
                "title": "Python help",
                "mapping": {
                    "node1": {
                        "message": {
                            "author": {"role": "user"},
                            "content": {"parts": ["I prefer Python over JavaScript for backend"]},
                        }
                    }
                },
            },
            {"title": "Travel planning", "mapping": {}},
        ]
        (tmp_path / "conversations.json").write_text(json.dumps(convs))
        result = parse_chatgpt(str(tmp_path))
        assert result["source"] == "chatgpt"
        assert len(result["memories"]) >= 2  # Topics

    def test_no_conversations_file(self, tmp_path):
        result = parse_chatgpt(str(tmp_path))
        assert result["memories"] == []

    def test_handles_invalid_json(self, tmp_path):
        (tmp_path / "conversations.json").write_text("not json")
        result = parse_chatgpt(str(tmp_path))
        assert result["memories"] == []


class TestSoulPreview:
    def test_format_preview(self):
        data = {
            "source": "openclaw",
            "personality": {"traits": ["Warm", "Witty"]},
            "memories": [
                {"type": "preference", "content": "dark mode", "confidence": 0.5},
                {"type": "belief", "content": "open source is best", "confidence": 0.5},
            ],
            "skills": [{"name": "greet", "code": "hello"}],
        }
        preview = format_soul_preview(data)
        assert "openclaw" in preview
        assert "2" in preview  # 2 personality traits
        assert "Sensitive" in preview or "sensitive" in preview.lower()
        assert "sandbox" in preview.lower()

    def test_classify_safe(self):
        assert classify_memory({"type": "preference"}) == "safe"
        assert classify_memory({"type": "fact"}) == "safe"

    def test_classify_sensitive(self):
        assert classify_memory({"type": "belief"}) == "sensitive"
        assert classify_memory({"type": "identity"}) == "sensitive"


class TestSourceDetection:
    def test_detects_chatgpt(self, tmp_path):
        (tmp_path / "conversations.json").write_text("[]")
        assert detect_source_type(str(tmp_path)) == "chatgpt"

    def test_detects_openclaw(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("soul")
        (tmp_path / "config.yaml").write_text("humor: 5")
        assert detect_source_type(str(tmp_path)) == "openclaw"

    def test_detects_hermes_db(self, tmp_path):
        (tmp_path / "sessions.db").write_text("")  # Just needs to exist
        assert detect_source_type(str(tmp_path)) == "hermes"

    def test_returns_none_for_empty(self, tmp_path):
        assert detect_source_type(str(tmp_path)) is None

    def test_nonexistent_path(self):
        assert detect_source_type("/nonexistent/path") is None


class TestImportOrchestrator:
    def test_preview_mode(self, tmp_path):
        (tmp_path / "SOUL.md").write_text("- Be kind\n")
        db = Database(":memory:")
        result = import_soul(db, str(tmp_path), user_approved=False)
        assert result["preview"] is not None
        assert result["imported"] == 0
        db.close()

    def test_approved_import(self, tmp_path):
        (tmp_path / "MEMORY.md").write_text("- User likes coffee\n- User prefers dark mode\n")
        (tmp_path / "SOUL.md").write_text("- Be helpful\n")
        db = Database(":memory:")
        result = import_soul(db, str(tmp_path), source_type="openclaw", user_approved=True)
        assert result["imported"] >= 2  # At least the memories
        db.close()

    def test_unknown_source(self, tmp_path):
        db = Database(":memory:")
        result = import_soul(db, str(tmp_path))
        assert result.get("error") is not None
        db.close()
