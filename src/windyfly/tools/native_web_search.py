"""Anthropic native web_search server-side tool integration (PR #164).

Strategic context:
  Provider-native server-side search (Anthropic, OpenAI, xAI all
  ship one) gives the model a built-in research capability that is
  TUNED at training time — the model knows when to search vs. when
  training data is enough, how to phrase queries, when to follow up,
  and how to write a polished synthesis with citations. This is
  meaningfully different from our client-side web_search tool which
  is just an HTTP call the model picks like any other.

  The cost is billed directly to whoever owns the API key (BYOK in
  Windy Fly's case = the user). At $10 / 1K searches, grandma's 3
  research questions/day = ~$0.03 added to her Anthropic bill. We
  pay zero, charge zero — pure passthrough.

This module owns:
  - The tool spec dict to add to the LLM ``tools`` list
  - The model allowlist (which Claude versions support web_search)
  - The daily search-cap counter (safety against runaway loops)
  - The kill-switch env var
  - The citation extractor / formatter (turn Anthropic's structured
    citations into a Telegram-friendly "Sources:" footer)

Tier 0 = native search when available (this module).
Tier 1 = our client-side web_search → Brave → DDG fallback chain.
Tier 2 = fetch_url with windy-search → direct httpx fallback (PR #163).

The agent loop tries Tier 0 first. If unavailable / disabled / cap
hit / unsupported-by-model, the model still has Tier 2 fetch_url
plus our existing tool surface for narrower lookups.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date as _date
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Anthropic tool versioning. ``web_search_20250305`` is the basic
# tool — no code-execution dependency, just runs searches and
# returns text-with-citations into the model's reasoning context.
# The newer ``web_search_20260209`` adds dynamic filtering (model
# writes code to pre-filter results) but requires the code-exec
# tool dependency, which we don't currently integrate. Stick with
# the basic version until we have a reason to upgrade.
NATIVE_TOOL_TYPE = "web_search_20250305"
NATIVE_TOOL_NAME = "web_search"

# Default cap: 50 searches/day per agent instance. Sized for a
# grandma-tier user doing a handful of research questions; the
# real point is preventing runaway tool loops from accidentally
# burning $5+ in a single conversation.
DEFAULT_DAILY_SEARCH_CAP = 50

# Allowlist of model prefixes that support the basic web_search
# tool. Per Anthropic docs as of 2026-05, the support matrix is
# not explicitly enumerated for the basic tool, but the dynamic-
# filtering variant covers Opus 4.6/4.7 + Sonnet 4.6 + Mythos
# preview. We optimistically add Haiku 4.x — if Anthropic returns
# an unsupported-tool 400, the agent loop has a defensive retry
# that drops the tool and tries again without it. So worst case
# is a small extra latency on the first failed call.
SUPPORTED_MODEL_PREFIXES: tuple[str, ...] = (
    "claude-opus-4-",
    "claude-sonnet-4-",
    "claude-haiku-4-",
    "claude-mythos",  # preview line
)


def is_model_supported(model: str | None) -> bool:
    """True iff ``model`` is on the allowlist of Claude versions
    that (likely) support the basic web_search tool."""
    if not model:
        return False
    lowered = model.lower()
    return any(lowered.startswith(p) for p in SUPPORTED_MODEL_PREFIXES)


def is_killswitched() -> bool:
    """True iff the operator explicitly disabled native web search
    via env var. Distinct from "not supported by model" — this is
    a manual override that should win over everything else."""
    val = os.environ.get("WINDY_NATIVE_WEB_SEARCH", "").strip().lower()
    return val in ("0", "false", "off", "no")


# ── Daily cap counter ────────────────────────────────────────────


def _counter_path() -> Path:
    """JSON file with today's count: ``{"date": "2026-05-10",
    "count": 12}``. Auto-resets on date rollover (read returns 0
    when the stored date is not today). Atomic .tmp + rename
    write so a torn write can't corrupt the count."""
    return Path(os.environ.get(
        "WINDY_DAILY_SEARCH_COUNTER",
        "/home/grantwhitmer/.windy/.daily_search_count",
    ))


def _today_iso() -> str:
    return _date.today().isoformat()


def daily_search_count() -> int:
    """Read today's accumulated count. Returns 0 if file missing,
    JSON corrupt, or stored date isn't today (auto date-rollover)."""
    path = _counter_path()
    if not path.exists():
        return 0
    try:
        data = json.loads(path.read_text())
    except Exception:
        return 0
    if not isinstance(data, dict):
        return 0
    if data.get("date") != _today_iso():
        return 0
    count = data.get("count", 0)
    return int(count) if isinstance(count, (int, float)) else 0


def bump_daily_search_count(n: int = 1) -> int:
    """Increment today's counter by ``n``. Returns the NEW total.
    Atomic .tmp + rename. Best-effort — failure to persist returns
    the in-memory new total without raising (don't break the bot
    just because a counter file couldn't be written)."""
    if n <= 0:
        return daily_search_count()
    new_total = daily_search_count() + n
    path = _counter_path()
    payload = json.dumps({"date": _today_iso(), "count": new_total})
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(payload)
        tmp.replace(path)
    except Exception as e:
        logger.warning("daily_search_count persist failed: %s", e)
    return new_total


def daily_search_cap() -> int:
    """Configurable via env. Default 50 — covers a grandma-tier
    user with comfortable headroom; small enough to prevent a
    runaway tool loop from racking up $50 of API charges."""
    raw = os.environ.get("WINDY_DAILY_SEARCH_CAP")
    if raw is None:
        return DEFAULT_DAILY_SEARCH_CAP
    try:
        v = int(raw)
        return v if v > 0 else DEFAULT_DAILY_SEARCH_CAP
    except ValueError:
        return DEFAULT_DAILY_SEARCH_CAP


def cap_reached() -> bool:
    """True iff today's count is at-or-over the cap. When True the
    agent loop drops the native tool from the request — the bot
    keeps working with Tier 1 / Tier 2 plumbing, just without the
    polished native synthesis on this day."""
    return daily_search_count() >= daily_search_cap()


# ── Tool spec ────────────────────────────────────────────────────


def native_web_search_tool_spec(
    max_uses: int = 5,
    allowed_domains: list[str] | None = None,
    blocked_domains: list[str] | None = None,
) -> dict[str, Any]:
    """Return the dict to add to the LLM ``tools`` array.

    ``max_uses`` caps how many searches the model can do PER TURN
    (different from the daily cap which caps across the whole day).
    Default 5 covers most research tasks without runaway risk.
    """
    spec: dict[str, Any] = {
        "type": NATIVE_TOOL_TYPE,
        "name": NATIVE_TOOL_NAME,
        "max_uses": max_uses,
    }
    if allowed_domains:
        spec["allowed_domains"] = allowed_domains
    if blocked_domains:
        spec["blocked_domains"] = blocked_domains
    return spec


def should_inject_native_tool(model: str | None) -> dict[str, Any]:
    """Single-call decision used by the agent loop. Returns a dict
    with ``inject`` (bool) and ``reason`` (str) so the caller can
    log telemetry without re-deriving the answer.

    Decision tree:
      1. If kill-switched → don't inject (reason="killswitched")
      2. If model not supported → don't inject (reason="model_unsupported")
      3. If daily cap reached → don't inject (reason="cap_reached")
      4. Else → inject (reason="ok")
    """
    if is_killswitched():
        return {"inject": False, "reason": "killswitched"}
    if not is_model_supported(model):
        return {"inject": False, "reason": "model_unsupported"}
    if cap_reached():
        return {"inject": False, "reason": "cap_reached"}
    return {"inject": True, "reason": "ok"}


# ── Citation extraction + formatting ─────────────────────────────


def format_citations_footer(citations: list[dict[str, Any]] | None) -> str:
    """Turn a list of Anthropic citation dicts into a Telegram-
    friendly "Sources:" footer. Returns "" if no usable citations.

    Each Anthropic citation has at minimum ``url`` and ``title``;
    we deduplicate by URL (the model often cites the same source
    multiple times across paragraphs).
    """
    if not citations:
        return ""
    seen: set[str] = set()
    lines: list[str] = []
    for c in citations:
        if not isinstance(c, dict):
            continue
        url = (c.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        title = (c.get("title") or url).strip() or url
        lines.append(f"  • [{title}]({url})")
    if not lines:
        return ""
    return "\n\n*Sources:*\n" + "\n".join(lines)


def is_unsupported_tool_error(exc: BaseException) -> bool:
    """Heuristic: did this exception come from Anthropic rejecting
    our web_search tool spec? If yes the agent loop retries without
    the tool. Conservative — false negatives just mean the regular
    chain-fail path fires.

    Pattern matching on text rather than exception class because
    the anthropic SDK wraps API errors into multiple exception
    layers and the message is the most stable signal."""
    msg = str(exc).lower()
    if "tool" not in msg:
        return False
    return any(marker in msg for marker in (
        "unsupported", "not supported", "invalid_request",
        "web_search_20250305",
    ))
