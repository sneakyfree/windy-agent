"""Context header — the gas tank indicator + always-on health emoji.

Two surfaces in this module:

  1. **Single-emoji health prefix** (PR #144 / grandma-proofing #6):
     Every LLM reply is prefixed with one emoji indicating the bot's
     current state. Grandma can glance at the very first character of
     any reply and know "🟢 healthy / 🟡 slow / 🔴 problem / 🛟
     lifeboat." Cheap (1-2 visible chars), always-on, language-
     agnostic.

  2. **Full gas-tank panel** (existing): when context-window % drops
     by 10+ since last header OR an hour has passed since last
     header, the full panel ``[🪰 Windy Fly · ts · 🟢 95%]`` replaces
     the single-emoji prefix. Same emoji is part of the panel so
     visual continuity is preserved across thresholds.

State priority (highest first):
  🛟 — lifeboat mode (resurrected — running on local Ollama)
  🔴 — context < 10% remaining (very near memory cap)
  🟡 — context < 30% remaining (slowing down)
  🟢 — healthy (default)

Rationale for default-on prefix vs threshold-only header:
  - Pre-PR a healthy reply had NO marker. User had no signal whether
    the bot was OK or limping until threshold crossed.
  - Post-PR every reply ships ONE character of state info. Free
    visual telemetry; grandma learns the four emojis and never has
    to guess "is something wrong with my bot?"

Slash-command acks bypass this — they have their own structured
formatting (e.g., ``🛟 *Lifeboat mode activated...*``) and don't go
through ``maybe_prepend_header``.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


def _state_emoji(pct: float, resurrected: bool = False) -> str:
    """Single source of truth for the bot's current state emoji.

    Threshold legend (preserved from pre-PR format_header for
    backwards compat with existing test_context_header tests):
      🟢 ≥ 50% remaining
      🟡 10-50%
      🔴 <  10%
      🛟 resurrected (overrides everything — paid creds dead is
         more user-facing than memory pressure, so surface it)
    """
    if resurrected:
        return "🛟"
    if pct >= 50:
        return "🟢"
    if pct >= 10:
        return "🟡"
    return "🔴"


def _is_resurrected_safe() -> bool:
    """Best-effort resurrection probe. Lazy import + guarded so a
    broken resurrect module can't crash the header path on every
    reply."""
    try:
        from windyfly.agent.resurrect import is_resurrected
        return is_resurrected()
    except Exception:
        return False


class ContextTracker:
    """Tracks session token state for gas-tank header decisions."""

    def __init__(self, max_context_tokens: int = 200_000) -> None:
        self.max_context_tokens = max_context_tokens
        self.tokens_used = 0
        self._last_header_time: float = 0.0
        self._last_header_pct: float = 100.0
        # Engine transparency (2026-07-18): the model that actually
        # served the last reply — including a Mind-broker reroute the
        # user didn't explicitly pick. Shown in the periodic panel
        # ONLY (never the per-message emoji prefix — no bloat), so
        # grandma watches engines swap and learns it's normal:
        # "your agent, any engine."
        self.last_engine: str | None = None

    def add_tokens(self, count: int) -> None:
        """Record tokens consumed in this session."""
        self.tokens_used += count

    @property
    def pct_remaining(self) -> float:
        """Percentage of context remaining (0-100)."""
        used_pct = (self.tokens_used / self.max_context_tokens) * 100
        return max(0.0, 100.0 - used_pct)

    def current_state_emoji(self) -> str:
        """The emoji reflecting the bot's current state. Used by
        both the single-emoji prefix and the full gas-tank panel
        for consistency."""
        return _state_emoji(self.pct_remaining, _is_resurrected_safe())

    def should_show_header(self) -> bool:
        """Determine if the FULL panel should be shown on this response.

        Rules (OR'd):
          - 1+ hour since last header
          - 10%+ context delta since last header

        When False, the caller still prepends a single-emoji marker
        (PR #144). The full panel is the periodic deeper status; the
        emoji prefix is the always-on quick state indicator.
        """
        now = time.time()
        hours_elapsed = (now - self._last_header_time) / 3600

        current_pct = self.pct_remaining
        delta = abs(self._last_header_pct - current_pct)

        return hours_elapsed >= 1.0 or delta >= 10.0

    def format_header(self) -> str:
        """Format the Windy Fly signature header.

        Returns:
            Header string like: [🪰 Windy Fly · Mar 27, 10:56 AM · 🟢 93%]
        """
        pct = self.pct_remaining
        now = datetime.now(timezone.utc).astimezone()
        timestamp = now.strftime("%b %d, %I:%M %p")
        indicator = self.current_state_emoji()

        self._last_header_time = time.time()
        self._last_header_pct = pct

        if self.last_engine:
            return (
                f"[🪰 Windy Fly · {timestamp} · {indicator} {pct:.0f}% · "
                f"{self.last_engine}]"
            )
        return f"[🪰 Windy Fly · {timestamp} · {indicator} {pct:.0f}%]"


# Module-level singleton — one tracker per process
_tracker: ContextTracker | None = None


def get_tracker(max_tokens: int = 200_000) -> ContextTracker:
    """Get or create the module-level context tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ContextTracker(max_context_tokens=max_tokens)
    return _tracker


def maybe_prepend_header(
    response_text: str, tokens_used: int,
    max_tokens: int = 200_000,
    engine: str | None = None,
) -> str:
    """Prepend a state marker to the response.

    Two paths (mutually exclusive):
      - Threshold met (10%+ delta or 1+ hour since last header):
        prepend the full gas-tank panel. The panel embeds the state
        emoji, so it carries the same signal in a richer form.
      - Threshold NOT met: prepend just the single-emoji marker
        (PR #144). Cheap visual telemetry on every reply.

    ``max_tokens`` — the effective context-window cap for this
    session (PR #199). Pre-fix the tracker hardcoded 200K, so a
    Windy 0 channel that had ``/memory 1M`` pinned on Opus saw the
    gas tank report 🔴 0% after only ~30K tokens of cumulative
    usage. Pass the per-channel effective cap and the header
    reflects reality.

    Empty response is returned unchanged — no point prefixing
    nothing.
    """
    if not response_text:
        return response_text

    tracker = get_tracker()
    # Update tracker's cap each call — the user can /memory 1M
    # mid-conversation and the very next reply should reflect the
    # new cap. (Per-channel races aren't a concern on Windy 0 which
    # is a single-user instance; future multi-user deployments
    # should switch to per-session trackers, see issue tracker.)
    tracker.max_context_tokens = max_tokens
    tracker.tokens_used = tokens_used
    if engine:
        tracker.last_engine = engine

    if tracker.should_show_header():
        header = tracker.format_header()
        return f"{header}\n\n{response_text}"

    # Always-on single-emoji prefix. Slash-command acks don't go
    # through this code path, so they keep their existing
    # formatting.
    return f"{tracker.current_state_emoji()} {response_text}"
