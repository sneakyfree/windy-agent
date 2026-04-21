"""Logging filter that redacts secrets before they hit any handler.

Without this, httpx's default INFO logging dumps the full Telegram URL
(``…/bot8669155077:AAE-5ee2VMzkk…/getUpdates``) to the launchd log
every 10 seconds. After ~10 minutes of polling the log contains the
bot token a hundred-plus times. Same applies to ``Bearer`` tokens in
upstream HTTP calls and ``sk-…`` API keys that show up in error
strings.

The filter is installed once at the root logger's handler in main.py
so it intercepts every record from every logger (httpx, telegram.ext,
windyfly.*, third-party libs) before formatting.
"""

from __future__ import annotations

import logging
import re

# Telegram: bot<digits>:<base64-ish secret>. Keep the bot ID and the
# first 4 chars of the secret so we can still tell instances apart in
# logs without exposing the full credential.
_TELEGRAM_TOKEN_RE = re.compile(
    r"(bot\d{6,}:[A-Za-z0-9_-]{4})[A-Za-z0-9_-]{20,}"
)

# OpenAI / Anthropic / OpenRouter style: sk-..., sk-proj-..., sk-ant-..., wk_...
_API_KEY_RE = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{6})[A-Za-z0-9_-]{20,}"
)
_WK_KEY_RE = re.compile(
    r"\b(wk[_-][A-Za-z0-9_-]{4})[A-Za-z0-9_-]{16,}"
)

# Z.AI / ZhipuAI key format: <32 hex>.<suffix>
_ZAI_KEY_RE = re.compile(
    r"\b([0-9a-f]{8})[0-9a-f]{24}\.[A-Za-z0-9]{16,}"
)

# Bearer tokens in HTTP-ish strings
_BEARER_RE = re.compile(
    r"(Bearer\s+[A-Za-z0-9_-]{4})[A-Za-z0-9._-]{16,}",
    re.IGNORECASE,
)

# Authorization header values (matches "Authorization: <value>" or
# "authorization=<value>")
_AUTH_HEADER_RE = re.compile(
    r"(Authorization[:=]\s*)[^\s,;]+",
    re.IGNORECASE,
)


def redact(text: str) -> str:
    """Replace common secret patterns in ``text`` with redacted forms.

    Each pattern preserves a small prefix so log lines remain
    distinguishable across instances without leaking the secret.
    """
    text = _TELEGRAM_TOKEN_RE.sub(r"\1***REDACTED***", text)
    text = _API_KEY_RE.sub(r"\1***REDACTED***", text)
    text = _WK_KEY_RE.sub(r"\1***REDACTED***", text)
    text = _ZAI_KEY_RE.sub(r"\1***REDACTED***", text)
    text = _BEARER_RE.sub(r"\1***REDACTED***", text)
    text = _AUTH_HEADER_RE.sub(r"\1***REDACTED***", text)
    return text


class RedactingFilter(logging.Filter):
    """Filter that mutates the record's message in place before emission.

    Filters attached to a *handler* run for every record routed through
    that handler regardless of which logger created it — install this
    on the root handler to cover httpx / telegram.ext / etc.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        # If the record carries args, materialize the formatted string
        # first so we can redact across the whole composed message.
        if record.args:
            try:
                record.msg = record.getMessage()
                record.args = None
            except Exception:
                # Some loggers pass non-stringifiable args. Skip those —
                # better to log a slightly malformed line than swallow
                # it entirely.
                return True
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        return True


def install_root_redaction() -> None:
    """Attach a RedactingFilter to every handler on the root logger.

    Idempotent — calling more than once just reuses one filter
    instance per handler.
    """
    root = logging.getLogger()
    f = RedactingFilter()
    for handler in root.handlers:
        # Avoid double-stacking the same filter on repeat calls
        if not any(isinstance(existing, RedactingFilter) for existing in handler.filters):
            handler.addFilter(f)
