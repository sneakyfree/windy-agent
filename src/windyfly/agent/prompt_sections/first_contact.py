"""FIRST CONTACT guard (PR #85, commit af755d4).

Conditional: emitted when both episodes and nodes tables are empty
(`_is_first_contact(db)` in prompt.py). Counteracts the LLM's
default warmth that produces "welcome back" on a virgin DB.
"""

from __future__ import annotations


FIRST_CONTACT_TEXT: str = (
    "FIRST CONTACT: You have no prior memory of this user — "
    "no episodes, no extracted facts, no turnover letter. "
    "They have never spoken with you before. Greet them as a "
    "brand-new acquaintance. DO NOT use 'welcome back', 'good "
    "to see you again', 'as we discussed', 'picking up where "
    "we left off', or ANY phrase implying prior interaction. "
    "Introduce yourself naturally if appropriate."
)
