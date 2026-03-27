"""Context header — the gas tank indicator.

Tracks token usage per session and formats the signature Windy Fly
header that shows context freshness, like a battery/gas indicator.

Header appears when:
  - 1+ hour since last header, OR
  - 10%+ context delta since last header
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


class ContextTracker:
    """Tracks session token state for gas-tank header decisions."""

    def __init__(self, max_context_tokens: int = 200_000) -> None:
        self.max_context_tokens = max_context_tokens
        self.tokens_used = 0
        self._last_header_time: float = 0.0
        self._last_header_pct: float = 100.0

    def add_tokens(self, count: int) -> None:
        """Record tokens consumed in this session."""
        self.tokens_used += count

    @property
    def pct_remaining(self) -> float:
        """Percentage of context remaining (0-100)."""
        used_pct = (self.tokens_used / self.max_context_tokens) * 100
        return max(0.0, 100.0 - used_pct)

    def should_show_header(self) -> bool:
        """Determine if the header should be shown on this response.

        Rules (OR'd):
          - 1+ hour since last header
          - 10%+ context delta since last header
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

        # Color coding
        if pct >= 50:
            indicator = "🟢"
        elif pct >= 10:
            indicator = "🟡"
        else:
            indicator = "🔴"

        self._last_header_time = time.time()
        self._last_header_pct = pct

        return f"[🪰 Windy Fly · {timestamp} · {indicator} {pct:.0f}%]"


# Module-level singleton — one tracker per process
_tracker: ContextTracker | None = None


def get_tracker(max_tokens: int = 200_000) -> ContextTracker:
    """Get or create the module-level context tracker."""
    global _tracker
    if _tracker is None:
        _tracker = ContextTracker(max_context_tokens=max_tokens)
    return _tracker


def maybe_prepend_header(response_text: str, tokens_used: int) -> str:
    """Conditionally prepend the gas-tank header to a response.

    Args:
        response_text: The agent's raw response.
        tokens_used: Total tokens consumed in this session so far.

    Returns:
        Response with header prepended if trigger conditions met.
    """
    tracker = get_tracker()
    tracker.tokens_used = tokens_used

    if tracker.should_show_header():
        header = tracker.format_header()
        return f"{header}\n\n{response_text}"

    return response_text
