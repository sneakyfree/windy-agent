"""WINDY FACTS: never state a Windy price (Hub, 10-02: a hosted agent recited Anthropic's plans)."""

from __future__ import annotations


def _system_text(message: str = "how much does Windy cost?") -> str:
    from windyfly.agent.prompt import assemble_prompt
    from windyfly.memory.database import Database

    config = {"agent": {"default_model": "gpt-4o-mini"},
              "memory": {"db_path": ":memory:", "max_nodes_per_context": 10},
              "personality": {"soul_path": "SOUL.md", "autonomy": 5}}
    msgs = assemble_prompt(config, Database(":memory:"), message, "s1")
    return "\n\n".join(m["content"] for m in msgs if m["role"] == "system")


def test_facts_block_forbids_prices_and_names_the_dashboard():
    text = _system_text()
    assert "WINDY FACTS" in text
    assert "windy_plans" in text and "never present" in text and "app.windyword.ai/upgrade" in text


def test_facts_block_carries_no_numbers_to_repeat():
    text = _system_text()
    block = text.split("WINDY FACTS", 1)[1].split("\n\n", 1)[0]
    assert "$" not in block and "€" not in block


def test_facts_cover_home_and_mind():
    text = _system_text("where do you live?")
    assert "windy bring-home" in text and "Windy Mind" in text
