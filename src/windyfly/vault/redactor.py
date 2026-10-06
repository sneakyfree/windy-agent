"""By-value token redaction (strand gene G6.1; Vault plan V7.6 c2b).

Shape-based redaction (observability/redact.py) misses tokens whose format we do not know
(a git remote URL, a provider error body, ``git config`` output). Every lease value the
agent is handed is registered HERE, and ``redact`` masks it (raw, URL-encoded and
JSON-escaped forms) wherever text is about to reach the model, a log, the audit trail or the
episode store. Values live in memory only and are forgotten with the lease.
"""

from __future__ import annotations

import json
import threading
import urllib.parse

MASK = "***LEASE***"
MIN_LEN = 8          # shorter values would mask ordinary words
MAX_VALUES = 256

_lock = threading.Lock()
_values: dict[str, tuple[str, ...]] = {}   # value -> its encoded forms


def _forms(value: str) -> tuple[str, ...]:
    forms = {value, urllib.parse.quote(value, safe=""), urllib.parse.quote_plus(value),
             json.dumps(value)[1:-1]}
    return tuple(sorted((f for f in forms if f), key=len, reverse=True))


def register(value: str) -> bool:
    """Start masking ``value``. False when it is too short to mask safely."""
    if not isinstance(value, str) or len(value) < MIN_LEN:
        return False
    with _lock:
        if value not in _values and len(_values) >= MAX_VALUES:
            _values.pop(next(iter(_values)))          # oldest out
        _values[value] = _forms(value)
    return True


def forget(value: str) -> None:
    with _lock:
        _values.pop(value, None)


def clear() -> None:
    with _lock:
        _values.clear()


def active_count() -> int:
    with _lock:
        return len(_values)


def redact(text: str) -> str:
    """Mask every registered value in ``text``. A no-op (and cheap) when nothing is registered."""
    if not _values or not isinstance(text, str) or not text:
        return text
    with _lock:
        forms = sorted({f for fs in _values.values() for f in fs}, key=len, reverse=True)
    for f in forms:
        if f in text:
            text = text.replace(f, MASK)
    return text


def contains(text: str) -> bool:
    """True when ``text`` holds a registered value (used to block secret-bearing responses)."""
    return redact(text) != text
