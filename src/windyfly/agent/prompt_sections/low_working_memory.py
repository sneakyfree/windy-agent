"""LOW WORKING MEMORY hint (PR #117, commit e060f22).

Conditional: emitted when `pct_remaining < 10`. Grandma-friendly /new
suggestion. Bans "context window" / "tokens" jargon — say "working
memory" instead.
"""

from __future__ import annotations


LOW_WORKING_MEMORY_TEXT: str = (
    "LOW WORKING MEMORY: This conversation has used most of "
    "its context window. After answering the user's current "
    "question naturally, add a short, plain-English line "
    "letting them know your working memory is getting full "
    "and that they can type /new whenever they want to start "
    "a fresh conversation — your long-term memory of them "
    "stays. Do not say 'context window' or 'tokens' — say "
    "'working memory' or 'short-term memory'. Keep the "
    "suggestion friendly and one sentence."
)
