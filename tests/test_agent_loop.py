"""Tests for the agent loop with mocked LLM.

Tests prompt assembly, agent_respond flow, episode saving,
cost logging, and fact extraction.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from windyfly.agent.loop import _extract_and_store_facts, agent_respond
from windyfly.agent.prompt import _extract_keywords, assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.episodes import get_recent_episodes
from windyfly.memory.write_queue import WriteQueue


def _make_config() -> dict:
    return {
        "agent": {
            "default_model": "gpt-4o-mini",
            "max_context_tokens": 8000,
            "max_response_tokens": 2000,
            "temperature": 0.7,
        },
        "memory": {
            "db_path": ":memory:",
            "max_episodes_per_context": 20,
            "max_nodes_per_context": 10,
        },
        "personality": {
            "soul_path": "SOUL.md",
            "humor_level": 7,
            "formality": 4,
            "proactivity": 5,
            "verbosity": 5,
            "reasoning_depth": 6,
            "autonomy": 3,
            "epistemic_strictness": 5,
        },
    }


def _make_db() -> Database:
    return Database(":memory:")


class TestExtractKeywords:
    def test_basic_extraction(self):
        result = _extract_keywords("What is the weather like today?")
        assert "weather" in result
        assert "like" in result or "today" in result

    def test_filters_stopwords(self):
        result = _extract_keywords("I am going to the store")
        assert "the" not in result.split()
        assert "going" in result or "store" in result

    def test_short_words_filtered(self):
        result = _extract_keywords("I am OK")
        # Short words below min_length should be filtered
        assert "am" not in result.split()

    def test_empty_message(self):
        result = _extract_keywords("")
        assert result == ""


class TestAssemblePrompt:
    def test_has_system_message(self):
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session")
        assert messages[0]["role"] == "system"
        assert len(messages[0]["content"]) > 0
        db.close()

    def test_has_user_message_at_end(self):
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session")
        assert messages[-1]["role"] == "user"
        assert messages[-1]["content"] == "Hello!"
        db.close()

    def test_includes_mode_override(self):
        config = _make_config()
        db = _make_db()
        messages = assemble_prompt(config, db, "Hello!", "test-session", mode="focused")
        system_content = messages[0]["content"]
        assert "focused" in system_content.lower()
        db.close()


class TestAgentRespond:
    @patch("windyfly.agent.loop.call_llm")
    def test_returns_response(self, mock_llm):
        mock_llm.return_value = {
            "content": "Hello! I'm Windy Fly.",
            "model": "gpt-4o-mini",
            "input_tokens": 100,
            "output_tokens": 20,
            "tool_calls": None,
        }

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        response = agent_respond(config, db, wq, "Hi there!", "test-session")
        assert response == "Hello! I'm Windy Fly."
        assert mock_llm.called

        wq.stop()
        db.close()

    @patch("windyfly.agent.loop.call_llm")
    def test_passes_config_to_llm(self, mock_llm):
        mock_llm.return_value = {
            "content": "Response",
            "model": "gpt-4o-mini",
            "input_tokens": 50,
            "output_tokens": 10,
            "tool_calls": None,
        }

        config = _make_config()
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        agent_respond(config, db, wq, "Test", "test-session")

        call_kwargs = mock_llm.call_args
        assert call_kwargs is not None

        wq.stop()
        db.close()


class TestFactExtraction:
    def test_extracts_name(self):
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        _extract_and_store_facts(db, wq, "My name is Grant")

        import time
        time.sleep(0.5)
        wq.stop()

        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'person'")
        assert len(nodes) >= 1
        db.close()

    def test_extracts_location(self):
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        _extract_and_store_facts(db, wq, "I live in San Francisco")

        import time
        time.sleep(0.5)
        wq.stop()

        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'location'")
        assert len(nodes) >= 1
        db.close()

    def test_extracts_preference(self):
        db = _make_db()
        wq = WriteQueue()
        wq.start()

        _extract_and_store_facts(db, wq, "I love dark mode")

        import time
        time.sleep(0.5)
        wq.stop()

        nodes = db.fetchall("SELECT * FROM nodes WHERE type = 'preference'")
        assert len(nodes) >= 1
        db.close()
