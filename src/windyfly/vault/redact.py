"""Redact a leased value from anything a call returns (Vault plan V7.6 c2b, triggered by vault.call_api).

WHOLE FORMS ONLY (design rev 4, Hub on #655). Masking partial runs of a value in place is an ORACLE: a model that can
make a server echo its guesses learns the key one character per response from which guesses come back masked.
So only complete encodings of the value are masked: raw, hex (both cases), base64 and base64url at byte offsets
0, 1 and 2 (so the value is still found inside a larger base64 blob; the unstable edge characters are trimmed),
JSON-escaped, percent-encoded (upper and lower case), and base64 of ``user:value`` for basic auth. A value shorter
than MIN_SECRET is refused at registration (call_api refuses that item). A server that echoes a short fragment of
a key (a "last 4" display) is accepted leakage: it shows that much anyway. The caller scrubs the decoded body,
every header (a redirect's Location included) and error text before anything reaches the model, a log, an audit
row or a stored episode. A Redactor never prints its values (``repr`` gives a count only).
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from urllib.parse import quote

MASK = "[REDACTED]"
MIN_SECRET = 16


class SecretTooShort(ValueError):
    """The value is shorter than MIN_SECRET: whole-form masking can't protect it, so it is not used."""


def _b64_at_offsets(raw: bytes) -> set[str]:
    """The base64/base64url text of ``raw`` that is the same whatever bytes surround it, for offsets 0, 1, 2."""
    out: set[str] = set()
    for enc in (base64.b64encode, base64.urlsafe_b64encode):
        for k in (0, 1, 2):
            text = enc(b"\0" * k + raw).decode().rstrip("=")
            start = math.ceil(4 * k / 3)               # characters that still hold prefix bits are unstable
            end = ((k + len(raw)) * 8) // 6            # characters that would hold bits of what follows are too
            piece = text[start:end]
            if len(piece) >= MIN_SECRET:
                out.add(piece)
        out.add(enc(raw).decode())                     # the plain, padded form
    return out


def _upper_u(m: re.Match[str]) -> str:
    return "\\u" + m.group(0)[2:].upper()


def _forms(value: str, username: str | None) -> set[str]:
    raw = value.encode("utf-8")
    pct = quote(value, safe="")
    forms = {
        value,
        binascii.hexlify(raw).decode(),
        binascii.hexlify(raw).decode().upper(),
        json.dumps(value)[1:-1],
        # JSON with non-ASCII left as-is (JSON.stringify, most servers) and with upper-case \uXXXX escapes.
        json.dumps(value, ensure_ascii=False)[1:-1],
        re.sub(r"\\u[0-9a-f]{4}", _upper_u, json.dumps(value)[1:-1]),
        pct,
        re.sub(r"%[0-9A-F]{2}", lambda m: m.group(0).lower(), pct),
        *_b64_at_offsets(raw),
    }
    if username is not None:
        basic = base64.b64encode(f"{username}:{value}".encode("utf-8")).decode()
        forms |= {basic, basic.rstrip("=")}
    return {f for f in forms if f}


class Redactor:
    def __init__(self) -> None:
        self._forms: set[str] = set()
        self._re: re.Pattern[str] | None = None

    def __repr__(self) -> str:
        return f"Redactor(forms={len(self._forms)})"

    def register(self, value: str, username: str | None = None) -> None:
        if not isinstance(value, str) or len(value) < MIN_SECRET:
            raise SecretTooShort("secret_too_short")
        self._forms |= _forms(value, username)
        # Longest first, so a form that contains another is masked whole.
        self._re = re.compile("|".join(re.escape(f) for f in sorted(self._forms, key=len, reverse=True)))

    def scrub(self, text: str) -> str:
        if not text or self._re is None:
            return text
        return self._re.sub(MASK, text)

    def scrub_obj(self, obj):
        """Scrub every string inside a JSON-like value (dict keys too)."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {self.scrub(k) if isinstance(k, str) else k: self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub_obj(v) for v in obj]
        return obj
