"""Phase 2.3.2 (partial) — first prompt section extracted from prompt.py.

This package is the FUTURE home of all 10 sections enumerated in
docs/PROMPT_AS_BUILT.md. The full split is gated on Grant's 5 design
Qs from §6 of that doc; this initial commit only extracts ONE section
(BIAS TO ACTION) as a proof-of-shape so:

  1. The import path exists and any future section drops in cleanly
  2. The render-then-append pattern is established and contract-tested
  3. prompt.py can incrementally migrate without one giant PR

Behavior is byte-identical to the previous inlined string. The
caller in prompt.py imports BIAS_TO_ACTION_TEXT and appends it to
system_parts exactly where the inline string used to be.
"""

from windyfly.agent.prompt_sections.active_goal import render_active_goal
from windyfly.agent.prompt_sections.bias_to_action import BIAS_TO_ACTION_TEXT
from windyfly.agent.prompt_sections.epistemic import EPISTEMIC_TEXT
from windyfly.agent.prompt_sections.first_contact import FIRST_CONTACT_TEXT
from windyfly.agent.prompt_sections.grandma_mode import GRANDMA_MODE_TEXT
from windyfly.agent.prompt_sections.low_working_memory import LOW_WORKING_MEMORY_TEXT
from windyfly.agent.prompt_sections.runtime_guardrail import RUNTIME_GUARDRAIL_TEXT

__all__ = [
    "BIAS_TO_ACTION_TEXT",
    "EPISTEMIC_TEXT",
    "FIRST_CONTACT_TEXT",
    "GRANDMA_MODE_TEXT",
    "LOW_WORKING_MEMORY_TEXT",
    "RUNTIME_GUARDRAIL_TEXT",
    "render_active_goal",
]
