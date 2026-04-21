"""Cross-channel error classifier.

The agent loop raises a wide variety of exceptions — LLM 5xx, rate
limits, DB locks, malformed JSON tool args, network timeouts, etc.
Returning a generic "Sorry, I hit an error processing that. Try
again?" forces the operator to context-switch to logs to figure out
what happened. Worse, for a *user* (grandma's instance) the generic
string is uninformative without being friendly.

This module maps any exception bubbling out of the agent loop into a
two-part response:

  - ``user_message``: a short, actionable sentence the channel sends
    back. Owner-band instances get a verbose suffix with the exception
    class so debugging is one DM away; lower bands get the friendly
    version only.
  - ``log_message``: the verbose form for ``logger.error`` regardless
    of band.

The classifier is intentionally lossy — categories are coarse on
purpose. Anything we don't recognise becomes ``UNKNOWN`` with the
exception class name appended for the operator.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum


class ErrorCategory(str, Enum):
    LLM_FAILURE = "llm_failure"        # provider 5xx, all retries exhausted
    LLM_AUTH = "llm_auth"              # 401 / 403 — bad or missing key
    LLM_RATE_LIMIT = "llm_rate_limit"  # 429 from provider
    LLM_TIMEOUT = "llm_timeout"        # network timeout against provider
    BUDGET_EXCEEDED = "budget_exceeded"  # cost ledger declined the call
    TOOL_FAILURE = "tool_failure"      # a tool raised
    DB_FAILURE = "db_failure"          # SQLite lock / WAL / disk
    NETWORK = "network"                # generic outbound connection issue
    UNKNOWN = "unknown"


@dataclass
class ClassifiedError:
    category: ErrorCategory
    user_message: str
    log_message: str


# Friendly user-facing messages per category. These are the strings
# grandma's instance sees — short, actionable, no jargon.
_FRIENDLY_MSG: dict[ErrorCategory, str] = {
    ErrorCategory.LLM_FAILURE: (
        "I'm having trouble reaching the AI service. Try again in a minute."
    ),
    ErrorCategory.LLM_AUTH: (
        "My credentials for the AI service aren't working — let an "
        "admin know."
    ),
    ErrorCategory.LLM_RATE_LIMIT: (
        "I'm being asked to slow down by the AI service. Give me about "
        "a minute and try again."
    ),
    ErrorCategory.LLM_TIMEOUT: (
        "The AI service is slow to respond. Try once more — usually it "
        "clears up."
    ),
    ErrorCategory.BUDGET_EXCEEDED: (
        "I've used up today's spending budget. We can pick up tomorrow."
    ),
    ErrorCategory.TOOL_FAILURE: (
        "One of my tools hit an error mid-task. Try rephrasing or skip "
        "that step."
    ),
    ErrorCategory.DB_FAILURE: (
        "I can't write to my memory right now. Your message wasn't saved."
    ),
    ErrorCategory.NETWORK: (
        "I can't reach the network. Check the connection and try again."
    ),
    ErrorCategory.UNKNOWN: (
        "Something went wrong on my end. Try again."
    ),
}


def classify(exc: BaseException) -> ClassifiedError:
    """Map an exception into a category + user/log messages.

    The matching is heuristic — we look at exception class name and
    string representation rather than catching specific provider SDK
    types, so the classifier doesn't drag in optional dependencies.
    """
    name = type(exc).__name__
    msg = str(exc).lower()

    if "budget" in msg or "BudgetExceeded" in name:
        category = ErrorCategory.BUDGET_EXCEEDED
    elif "TrustDenied" in name:
        # Trust-gated action refused; not really an error from the
        # user's perspective — surface it specifically so we don't
        # hide it behind a generic "something broke."
        category = ErrorCategory.UNKNOWN
    elif "RateLimit" in name or "429" in msg:
        category = ErrorCategory.LLM_RATE_LIMIT
    elif "Authentication" in name or "401" in msg or "403" in msg or "invalid x-api-key" in msg:
        category = ErrorCategory.LLM_AUTH
    elif "Timeout" in name or "timed out" in msg or "timeout" in msg:
        category = ErrorCategory.LLM_TIMEOUT
    elif "503" in msg or "502" in msg or "500" in msg or "InternalServer" in name:
        category = ErrorCategory.LLM_FAILURE
    elif "LLM call failed" in msg:
        # Failover chain exhausted (PR #46) — generic LLM_FAILURE.
        category = ErrorCategory.LLM_FAILURE
    elif "OperationalError" in name or "database is locked" in msg or "no such table" in msg:
        category = ErrorCategory.DB_FAILURE
    elif "ConnectionError" in name or "ConnectError" in name or "name resolution" in msg:
        category = ErrorCategory.NETWORK
    elif "ToolError" in name or "tool_call" in msg:
        category = ErrorCategory.TOOL_FAILURE
    else:
        category = ErrorCategory.UNKNOWN

    # Short report_id so "this broke" → grep the log → exact line in 5s.
    # Six chars = 16M unique values per process; stable per-classify call.
    import secrets
    report_id = secrets.token_hex(3)

    user_message = _FRIENDLY_MSG[category]
    log_message = f"[err:{report_id}] [{category.value}] {name}: {exc}"

    if _is_verbose():
        # Owner-band instances get the technical class + report_id
        # appended so debugging is one DM round-trip.
        user_message = (
            f"{user_message}\n\n"
            f"(diagnostic: {category.value} / {name} · err:{report_id})"
        )
    else:
        # Even normie-band gets the report_id so they can quote it back
        # to support without exposing the technical class.
        user_message = f"{user_message}\n\n(ref: err:{report_id})"

    return ClassifiedError(
        category=category,
        user_message=user_message,
        log_message=log_message,
    )


def _is_verbose() -> bool:
    """True if this instance should surface the diagnostic suffix.

    Default: ON. The intent is that a personal/dog-food instance shows
    operators useful info, while public/normie deploys flip
    WINDYFLY_ERROR_VERBOSE=0 to hide jargon.

    Once the Capability Plane / passport-band system lands (Wave 2),
    this will key off the session's band instead of an env var.
    """
    return os.environ.get("WINDYFLY_ERROR_VERBOSE", "1") not in ("0", "false", "False")
