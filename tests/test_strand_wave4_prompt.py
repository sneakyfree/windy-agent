"""Strand-to-green Wave 4, Gene 2.3: epistemic strictness in the prompt.

* strictness > 9 keeps ONLY verified + user_stated knowledge nodes in the
  "Relevant Knowledge" block (inferred / speculative / other statuses drop).
* strictness 8-9 drops only speculative + inferred.
* the "indicate your confidence level" instruction is always in the system
  prompt.
"""

from __future__ import annotations

from windyfly.agent.prompt import assemble_prompt
from windyfly.memory.database import Database
from windyfly.memory.nodes import upsert_node

STATUSES = ["verified", "user_stated", "inferred", "speculative", "observed"]


def _seed(db: Database) -> None:
    for status in STATUSES:
        upsert_node(db, "fact", f"marmalade_{status}",
                    epistemic_status=status, source="test")


def _config(strictness: int) -> dict:
    return {
        "personality": {"epistemic_strictness": strictness},
        "memory": {"max_nodes_per_context": 20},
    }


def _knowledge_block(messages: list[dict]) -> str:
    blocks = [m["content"] for m in messages
              if m["role"] == "system" and m["content"].startswith("## Relevant Knowledge:")]
    return blocks[0] if blocks else ""


def _statuses_in(block: str) -> set[str]:
    return {s for s in STATUSES if f"marmalade_{s}" in block}


def test_Gene_2_3_strictness_10_keeps_only_verified_and_user_stated():
    db = Database(":memory:")
    _seed(db)
    messages = assemble_prompt(_config(10), db, "tell me about marmalade", "wave4-strict")
    block = _knowledge_block(messages)
    assert block, "no Relevant Knowledge block was assembled"
    assert _statuses_in(block) == {"verified", "user_stated"}
    assert "[VERIFIED]" in block and "[USER_STATED]" in block
    db.close()


def test_Gene_2_3_strictness_8_drops_only_inferred_and_speculative():
    db = Database(":memory:")
    _seed(db)
    messages = assemble_prompt(_config(8), db, "tell me about marmalade", "wave4-strict")
    assert _statuses_in(_knowledge_block(messages)) == {"verified", "user_stated", "observed"}
    db.close()


def test_Gene_2_3_default_strictness_keeps_everything():
    db = Database(":memory:")
    _seed(db)
    messages = assemble_prompt(_config(5), db, "tell me about marmalade", "wave4-strict")
    assert _statuses_in(_knowledge_block(messages)) == set(STATUSES)
    db.close()


def test_Gene_2_3_slider_row_in_db_overrides_config_strictness():
    """The control-panel slider (DB) wins over the config default, so the
    strict filter follows what the owner set at runtime."""
    from windyfly.control_panel import set_slider

    db = Database(":memory:")
    _seed(db)
    set_slider(db, "epistemic_strictness", 10)
    messages = assemble_prompt(_config(5), db, "tell me about marmalade", "wave4-strict")
    assert _statuses_in(_knowledge_block(messages)) == {"verified", "user_stated"}
    db.close()


def test_Gene_2_3_confidence_instruction_is_in_system_prompt():
    for strictness in (0, 5, 10):
        db = Database(":memory:")
        messages = assemble_prompt(_config(strictness), db, "hello there", "wave4-conf")
        system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
        assert "indicate your confidence level" in system_text
        assert "If a fact is marked INFERRED, say so." in system_text
        db.close()
