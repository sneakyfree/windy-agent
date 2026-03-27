"""Tests for relationship moments extraction and prompt injection."""

from unittest.mock import MagicMock, patch

from windyfly.memory.database import Database


class TestRelationshipMomentsInPrompt:
    """Test that relationship moments are injected into the prompt."""

    def test_moments_appear_in_assembled_prompt(self) -> None:
        """Verify that relationship_moment nodes are injected as Shared Experiences."""
        db = Database(":memory:")

        # Insert a relationship_moment node
        from windyfly.memory.nodes import upsert_node
        upsert_node(
            db,
            "relationship_moment",
            "moment:User was frustrated → we debugged → relief",
            metadata={"summary": "User was frustrated → we debugged → relief"},
            source="agent_observed",
            epistemic_status="verified",
        )

        config = {"personality": {}, "memory": {"max_nodes_per_context": 10}}
        from windyfly.agent.prompt import assemble_prompt
        messages = assemble_prompt(config, db, "Hey there", "test-session")

        # Find the Shared Experiences system message
        shared_exp = [
            m for m in messages
            if m["role"] == "system" and "Shared Experiences" in m.get("content", "")
        ]
        assert len(shared_exp) >= 1
        assert "frustrated" in shared_exp[0]["content"]

    def test_no_moments_when_empty(self) -> None:
        """No Shared Experiences block when no moments exist."""
        db = Database(":memory:")
        config = {"personality": {}, "memory": {}}
        from windyfly.agent.prompt import assemble_prompt
        messages = assemble_prompt(config, db, "Hello", "test-session")
        shared_exp = [
            m for m in messages
            if m["role"] == "system" and "Shared Experiences" in m.get("content", "")
        ]
        assert len(shared_exp) == 0


class TestTurnoverLetterInPrompt:
    """Test that turnover letters are injected into the prompt."""

    def test_turnover_letter_loaded_on_session_start(self) -> None:
        """Verify the last turnover letter is injected as Last Session Handoff."""
        db = Database(":memory:")

        from windyfly.memory.nodes import upsert_node
        upsert_node(
            db,
            "turnover_letter",
            "turnover_letter:2024-03-27",
            metadata={"summary": "We were working on the gateway. Grant was excited about SMS."},
            source="agent_observed",
            epistemic_status="verified",
        )

        config = {"personality": {}, "memory": {"max_nodes_per_context": 10}}
        from windyfly.agent.prompt import assemble_prompt
        messages = assemble_prompt(config, db, "Hey", "test-session")

        handoff = [
            m for m in messages
            if m["role"] == "system" and "Last Session Handoff" in m.get("content", "")
        ]
        assert len(handoff) >= 1
        assert "gateway" in handoff[0]["content"]
