"""Intent detector — extract goals from user messages.

Uses pattern matching as a fast pre-filter (zero cost), then
optionally falls back to LLM analysis for messages the patterns miss.

The LLM fallback is OFF unless WINDY_INTENT_LLM=1. When on, it fires on
every user message that no pattern matches, is longer than 15 chars,
and arrives with the proactivity slider >= 5 (the default) -- i.e. on
most turns, one extra small model call each. That is the owner's model
budget, so it stays opt-in.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# Goal/intent keywords (fast, zero-cost pre-filter)
INTENT_PATTERNS: list[tuple[str, str]] = [
    (r"(?i)i (want|need|would like|'d like) to (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)i('m| am) (trying|planning|hoping|going) to (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)(can you|could you|would you) (help me|assist me|) ?(.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)my goal is (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)i need (.+?)(?:\.|!|\?|$)", "user_said"),
    (r"(?i)remind me to (.+?)(?:\.|!|\?|$)", "user_said"),
]

# Plain string, NOT a str.format template: the JSON example's braces
# made `.format(message=...)` raise KeyError('"has_intent"'), which the
# except below swallowed, so the LLM path never produced an intent.
_LLM_INTENT_PROMPT = (
    "Analyze the following user message. Does it express a goal, project, "
    "commitment, or something the user wants to accomplish or remember?\n\n"
    "Respond ONLY with valid JSON:\n"
    '{"has_intent": true/false, "description": "brief goal description"}\n\n'
    "If there is no intent, respond: {\"has_intent\": false, \"description\": \"\"}\n\n"
    "User message: "
)

# Opt-in switch for the LLM fallback (default OFF; see module docstring).
_INTENT_LLM_ENV = "WINDY_INTENT_LLM"


def _build_llm_prompt(user_message: str) -> str:
    """The classifier prompt with the user's message appended."""
    return _LLM_INTENT_PROMPT + user_message


def _intent_llm_enabled() -> bool:
    return os.environ.get(_INTENT_LLM_ENV, "").strip() == "1"


def detect_intent(
    user_message: str,
    context: list[dict[str, str]] | None = None,
    *,
    config: dict[str, Any] | None = None,
    proactivity: int = 5,
) -> dict[str, Any] | None:
    """Detect if a user message expresses a goal or intent.

    Strategy:
      1. Try fast regex patterns first (zero cost).
      2. If no regex match, WINDY_INTENT_LLM=1 and proactivity >= 5,
         use LLM analysis.

    Args:
        user_message: The user's message.
        context: Optional conversation context.
        config: Optional config dict for LLM defaults.
        proactivity: Proactivity slider value (0-10). LLM fallback
                     only fires when >= 5.

    Returns:
        Dict with has_intent, description, origin — or None.
    """
    # 1. Fast regex pre-filter (free)
    for pattern, origin in INTENT_PATTERNS:
        match = re.search(pattern, user_message)
        if match:
            groups = match.groups()
            description = groups[-1].strip() if groups else user_message
            if len(description) > 5:
                return {
                    "has_intent": True,
                    "description": description,
                    "origin": origin,
                }

    # 2. LLM fallback (opt-in, and only if proactivity warrants the cost)
    if _intent_llm_enabled() and proactivity >= 5 and len(user_message) > 15:
        return _detect_intent_llm(user_message, config)

    return None


def _detect_intent_llm(
    user_message: str,
    config: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Use LLM to detect subtle intents that regex misses.

    Args:
        user_message: The user's message.
        config: Config dict for model selection.

    Returns:
        Intent dict or None.
    """
    try:
        from windyfly.agent.models import call_llm, llm_purpose

        messages = [
            {
                "role": "system",
                "content": "You are a concise intent classifier. Respond only with JSON.",
            },
            {
                "role": "user",
                "content": _build_llm_prompt(user_message),
            },
        ]

        with llm_purpose("intent"):
            result = call_llm(
                messages,
                model=(config or {}).get("agent", {}).get("default_model", "gpt-4o-mini"),
                temperature=0.1,  # Low temp for classification
                max_tokens=100,   # Short response
                config=config,
            )

        content = result["content"].strip()
        # Strip markdown code fences
        if content.startswith("```"):
            content = content.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # Strip leading/trailing non-JSON chars (safety net for chatty LLMs)
        if not content.startswith("{"):
            idx = content.find("{")
            if idx >= 0:
                content = content[idx:]
        if not content.endswith("}"):
            idx = content.rfind("}")
            if idx >= 0:
                content = content[:idx + 1]

        data = json.loads(content)

        if data.get("has_intent") and data.get("description"):
            return {
                "has_intent": True,
                "description": data["description"],
                "origin": "inferred_from_chat",
            }

    except (json.JSONDecodeError, KeyError, RuntimeError) as e:
        logger.debug("LLM intent detection failed: %s", e)

    return None
