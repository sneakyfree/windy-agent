"""Universal outgoing-message sanitizer.

Every bot reply runs through ``sanitize_outgoing`` before reaching
the user. The goal: a grandma in a ballroom can't be shown a Python
traceback, an API key, a 50-screen wall of text, or an empty bubble
no matter what fails inside the agent.

Sanitization layers (applied in order):
  1. Strip Python tracebacks ("Traceback (most recent call last):"
     through the final "<ExceptionType>: ..." line)
  2. Strip leaked credential strings via the same regex set used by
     the log-redaction filter
  3. Strip ASCII control chars except newline / tab
  4. Collapse runs of 4+ newlines to 3 (preserves intentional
     paragraph breaks; kills spam-newlines)
  5. Truncate to Telegram's 4096-char hard limit with a continuation
     marker
  6. Fallback to a polite "I'm having trouble" message when the
     sanitized result is empty / whitespace-only

The function never raises — even if the input is None, a non-string,
or fights every regex, the worst outcome is the fallback message.
That makes it safe to wrap every reply path unconditionally.
"""

from __future__ import annotations

import logging
import re

from windyfly.observability.redact import redact

logger = logging.getLogger(__name__)

# Telegram's hard limit on a single message body. Anything longer
# raises BadRequest from the Bot API; truncating client-side avoids
# the round-trip and gives us control over the continuation marker.
TELEGRAM_MAX_MESSAGE = 4096

# Python traceback signature. We strip from the start of a Traceback
# block through the LAST "ExceptionName: ..." line that follows it.
# Conservative regex — better to keep the bottom of a long message
# than to gobble user content.
_TRACEBACK_RE = re.compile(
    r"Traceback \(most recent call last\):.*?(?:^[A-Z][A-Za-z_]+(?:Error|Exception|Warning):[^\n]*$)",
    re.DOTALL | re.MULTILINE,
)

# Bare error prefixes that shouldn't reach a user.
_ERROR_PREFIX_RE = re.compile(
    r"^\s*(?:Error|Exception|FATAL|CRITICAL):\s*",
    re.IGNORECASE,
)

# Control chars except \t, \n, \r (and char codes >= 0x20).
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Run of 4+ newlines collapsed to 3 to preserve paragraph breaks
# without letting spam newlines blow up message height.
_EXCESS_NEWLINES_RE = re.compile(r"\n{4,}")

_TRUNCATION_MARKER = "\n\n…[truncated]"
_FALLBACK_REPLY = (
    "Sorry, I had a hiccup answering that one. Please try again, "
    "or rephrase what you wanted?"
)


def sanitize_outgoing(
    text: str | None,
    max_length: int = TELEGRAM_MAX_MESSAGE,
) -> str:
    """Clean a bot reply for safe delivery to the user.

    Returns a non-empty string of length <= max_length. Never raises.

    Args:
        text: Whatever the agent produced. None / non-str tolerated.
        max_length: Hard truncation ceiling. Defaults to Telegram's
            4096-char limit.
    """
    try:
        return _sanitize(text, max_length)
    except Exception as exc:
        # Sanitizer must never be the failure mode — log and fall back.
        logger.warning("sanitize_outgoing failed (%s); using fallback", exc)
        return _FALLBACK_REPLY


def split_for_telegram(
    text: str,
    max_chunk: int = 4000,
) -> list[str]:
    """Split a long reply into Telegram-sized chunks.

    Used when the agent's response exceeds the per-message ceiling —
    instead of truncating with a "…[truncated]" marker, we send
    multiple messages so grandma sees the whole thing.

    Split priority (latest match wins, must be in second half of the
    chunk so we don't make microscopic chunks at the start):
      1. Paragraph break (``\\n\\n``)
      2. Line break (``\\n``)
      3. Sentence end (``. ``)
      4. Word break (single space)
      5. Hard cut at ``max_chunk`` if no natural boundary found

    Each chunk is guaranteed to be <= max_chunk chars. The default
    4000 leaves headroom under Telegram's 4096-char hard limit so we
    survive a few extra chars Telegram might add (link previews etc).
    """
    if not text:
        return []
    if len(text) <= max_chunk:
        return [text]

    chunks: list[str] = []
    remaining = text
    min_split = max_chunk // 2  # don't split before halfway through
    while len(remaining) > max_chunk:
        cut = max_chunk
        for sep in ("\n\n", "\n", ". ", " "):
            idx = remaining.rfind(sep, 0, max_chunk)
            if idx >= min_split:
                cut = idx + len(sep)
                break
        piece = remaining[:cut].rstrip()
        if piece:
            chunks.append(piece)
        remaining = remaining[cut:].lstrip()

    if remaining:
        chunks.append(remaining)
    return chunks


def _sanitize(text: str | None, max_length: int) -> str:
    if text is None:
        return _FALLBACK_REPLY
    if not isinstance(text, str):
        text = str(text)

    # 1. Strip Python tracebacks. Repeatable in case multiple were
    # concatenated.
    while True:
        cleaned = _TRACEBACK_RE.sub("", text, count=1).strip()
        if cleaned == text.strip():
            break
        text = cleaned

    # 2. Redact any credential-shaped strings via the shared pattern set.
    text = redact(text)

    # 3. Strip leaked error prefixes from the very beginning.
    text = _ERROR_PREFIX_RE.sub("", text)

    # 4. Strip control chars.
    text = _CONTROL_CHAR_RE.sub("", text)

    # 5. Collapse runs of newlines.
    text = _EXCESS_NEWLINES_RE.sub("\n\n\n", text)

    # 6. Trim and check for emptiness.
    text = text.strip()
    if not text:
        return _FALLBACK_REPLY

    # 7. Truncate to max_length, leaving room for the marker.
    if len(text) > max_length:
        keep = max_length - len(_TRUNCATION_MARKER)
        text = text[:keep].rstrip() + _TRUNCATION_MARKER

    return text
