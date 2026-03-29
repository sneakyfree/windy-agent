"""Intent detector — extract goals from user messages.

Uses pattern matching as a fast pre-filter (zero cost), then
falls back to LLM analysis for ambiguous messages when the
proactivity slider is >= 5.
"""

from __future__ import annotations

import json
import logging
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

_LLM_INTENT_PROMPT = (
    "Analyze the following user message. Does it express a goal, project, "
    "commitment, or something the user wants to accomplish or remember?\n\n"
    "Respond ONLY with valid JSON:\n"
    '{"has_intent": true/false, "description": "brief goal description"}\n\n'
    "If there is no intent, respond: {\"has_intent\": false, \"description\": \"\"}\n\n"
    "User message: {message}"
)


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
      2. If no regex match and proactivity >= 5, use LLM analysis.

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

    # 2. LLM fallback (only if proactivity slider warrants the cost)
    if proactivity >= 5 and len(user_message) > 15:
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
        from windyfly.agent.models import call_llm

        messages = [
            {
                "role": "system",
                "content": "You are a concise intent classifier. Respond only with JSON.",
            },
            {
                "role": "user",
                "content": _LLM_INTENT_PROMPT.format(message=user_message),
            },
        ]

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
