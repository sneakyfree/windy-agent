"""Goal evaluator — the second model in the /goal two-model loop.

After the worker (Sonnet/Opus/whatever the user configured) finishes
its turn, this evaluator looks at the goal text + the most recent
exchange and answers ONE question: *has the goal been met?*

Three reasons this is a separate model from the worker:

1. **Anti-self-congratulation.** The worker that just did the work
   is biased to call its own work done. A fresh model with no
   sunk cost gives a cleaner verdict — same principle that made
   Claude Code 2.1.139's evaluator split materially better than
   the inline self-check it replaced.

2. **Cost.** Haiku ≈ $0.0005 per evaluator call (~500-token
   transcript + 50-token JSON reply). Free on Max plan beyond
   the 5-hour rate window. Cheap enough to fire every turn.

3. **Latency.** Haiku is fast enough (typically <1.5s) that
   firing it after every worker turn doesn't visibly slow the
   user-facing reply path.

The evaluator outputs strict JSON with one of four verdicts:

  - ``met``        — goal is clearly done; loop terminates
  - ``advanced``   — concrete progress this turn (log a note)
  - ``blocked``    — waiting on user / external / failed tool
  - ``unrelated``  — user moved to a different topic this turn

False-positive ``met`` is the failure mode to guard against, not
false-negative — a missed completion just means one extra turn
before the user types ``/goal done``, but a premature ``met``
abandons real ongoing work and erases the user's stated objective.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from windyfly.agent.models import call_llm
from windyfly.memory import goals as goals_mod

logger = logging.getLogger(__name__)


DEFAULT_EVALUATOR_MODEL = "claude-haiku-4-5-20251001"

# How many recent turns of conversation to show the evaluator. More
# context = better judgment but more tokens. 4 exchanges (8 messages)
# is the sweet spot — enough for the model to tell "user thanked us"
# from "user is asking a follow-up", small enough to keep the call
# under ~500 input tokens.
_EVAL_CONTEXT_TURNS = 8

_EVALUATOR_SYSTEM_PROMPT = """You are the GOAL EVALUATOR for an AI assistant.

Your ONLY job: read the user's stated goal and the recent transcript, then return JSON answering whether the goal has been met.

You are not the assistant. You do not address the user. You output JSON ONLY, with this exact schema and no surrounding prose:

{"verdict": "met"|"advanced"|"blocked"|"unrelated", "reason": "<one sentence>", "progress_note": "<one sentence or null>"}

Verdict definitions:

- "met": the user explicitly indicated completion ("thanks, that's it", "perfect, we're done"), OR the assistant produced the concrete deliverable the goal asks for. Be STRICT — false positives are worse than misses.
- "advanced": this turn made concrete progress (tool used, decision narrowed, deliverable partially produced).
- "blocked": assistant is waiting on user input, external data, or hit a tool failure it can't recover from this turn.
- "unrelated": this turn is on a completely different topic than the goal. User asked about lunch when the goal is about taxes — that's unrelated. User asked a related follow-up question — that is NOT unrelated, that's "advanced" or "blocked".

progress_note: required when verdict is "advanced" — a single sentence describing what was accomplished, suitable for showing the user in /goal status. Null otherwise.

Reply with the JSON object only. No code fences, no commentary."""


def evaluate_goal(
    goal_text: str,
    recent_messages: list[dict[str, str]],
    *,
    model: str | None = None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the evaluator and return ``{verdict, reason, progress_note}``.

    Errors from the LLM call fall back to ``{verdict: "blocked",
    reason: "evaluator unavailable"}`` so the loop can continue
    without bouncing the user — the cost of a missed verdict for
    one turn is tiny.
    """
    eval_model = model or (config or {}).get("goal", {}).get(
        "evaluator_model", DEFAULT_EVALUATOR_MODEL,
    )

    transcript = _format_transcript(recent_messages[-_EVAL_CONTEXT_TURNS:])
    user_prompt = (
        f"Goal: {goal_text}\n\n"
        f"Recent transcript:\n{transcript}\n\n"
        "Verdict?"
    )

    try:
        result = call_llm(
            [
                {"role": "system", "content": _EVALUATOR_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            model=eval_model,
            temperature=0.0,
            max_tokens=200,
            config=config,
        )
    except Exception as e:
        logger.warning("goal evaluator LLM call failed: %s", e)
        return {
            "verdict": goals_mod.VERDICT_BLOCKED,
            "reason": f"evaluator unavailable ({type(e).__name__})",
            "progress_note": None,
        }

    content = (result.get("content") or "").strip()
    parsed = _parse_evaluator_json(content)
    if parsed is None:
        logger.warning(
            "goal evaluator returned unparseable output: %r", content[:200],
        )
        return {
            "verdict": goals_mod.VERDICT_BLOCKED,
            "reason": "evaluator output was not valid JSON",
            "progress_note": None,
        }

    # Strict verdict whitelist so an LLM-invented value doesn't
    # corrupt the consecutive_unrelated counter or the status text.
    verdict = parsed.get("verdict")
    if verdict not in (
        goals_mod.VERDICT_MET, goals_mod.VERDICT_ADVANCED,
        goals_mod.VERDICT_BLOCKED, goals_mod.VERDICT_UNRELATED,
    ):
        logger.warning("goal evaluator returned unknown verdict %r", verdict)
        return {
            "verdict": goals_mod.VERDICT_BLOCKED,
            "reason": f"evaluator returned unknown verdict: {verdict!r}",
            "progress_note": None,
        }

    return {
        "verdict": verdict,
        "reason": str(parsed.get("reason") or "")[:300],
        "progress_note": (
            str(parsed["progress_note"])[:300]
            if parsed.get("progress_note") else None
        ),
    }


def _format_transcript(messages: list[dict[str, str]]) -> str:
    """Render messages as a compact ``USER: ... / ASSISTANT: ...``
    block. Long messages get truncated to keep the evaluator call
    cheap and focused."""
    lines: list[str] = []
    for msg in messages:
        role = (msg.get("role") or "").upper()
        if role not in ("USER", "ASSISTANT"):
            continue
        content = (msg.get("content") or "").strip()
        if len(content) > 600:
            content = content[:597].rstrip() + "..."
        lines.append(f"{role}: {content}")
    return "\n\n".join(lines) if lines else "(no transcript yet)"


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)


def _parse_evaluator_json(text: str) -> dict[str, Any] | None:
    """Robustly extract the JSON object the evaluator was asked to
    return. The system prompt asks for raw JSON but small models
    sometimes wrap in code fences or add a leading sentence; tolerate
    both rather than failing the loop on cosmetic noise.
    """
    # Try direct parse first
    try:
        out = json.loads(text)
        if isinstance(out, dict):
            return out
    except json.JSONDecodeError:
        pass

    # Try fenced block
    m = _JSON_FENCE_RE.search(text)
    if m:
        try:
            out = json.loads(m.group(1))
            if isinstance(out, dict):
                return out
        except json.JSONDecodeError:
            pass

    # Last resort: find the first {...} substring and try it
    brace_start = text.find("{")
    brace_end = text.rfind("}")
    if 0 <= brace_start < brace_end:
        try:
            out = json.loads(text[brace_start : brace_end + 1])
            if isinstance(out, dict):
                return out
        except json.JSONDecodeError:
            pass

    return None
