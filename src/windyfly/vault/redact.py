"""Redact a leased value from anything a call returns (Vault plan V7.6 c2b, triggered by vault.call_api).

Every form a server can echo is registered BEFORE the call: raw, hex (both cases), base64, base64url (padded and
not), JSON-escaped, percent-encoded (upper and lower case), base64 of ``user:value`` for basic auth, plus any run
of 8 or more characters of any of those forms (partial echoes such as a key prefix followed by "..."). The caller
applies ``scrub`` to the decoded response body, headers and error text before anything reaches the model, a log,
an audit row or a stored episode. A Redactor never prints its values (``repr`` names counts only).
"""

from __future__ import annotations

import base64
import binascii
import json
from urllib.parse import quote

MASK = "[REDACTED]"
MIN_RUN = 8


def _forms(value: str, username: str | None = None) -> set[str]:
    raw = value.encode("utf-8")
    pct = quote(value, safe="")
    forms = {
        value,
        binascii.hexlify(raw).decode(),
        binascii.hexlify(raw).decode().upper(),
        base64.b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode(),
        base64.urlsafe_b64encode(raw).decode().rstrip("="),
        base64.b64encode(raw).decode().rstrip("="),
        json.dumps(value)[1:-1],
        pct,
        pct.lower() if "%" in pct else pct,
    }
    if username is not None:
        basic = base64.b64encode(f"{username}:{value}".encode("utf-8")).decode()
        forms |= {basic, basic.rstrip("=")}
    return {f for f in forms if f}


class Redactor:
    def __init__(self) -> None:
        self._forms: set[str] = set()
        self._runs: set[str] = set()

    def __repr__(self) -> str:
        return f"Redactor(forms={len(self._forms)})"

    def register(self, value: str, username: str | None = None) -> None:
        for form in _forms(value, username):
            self._forms.add(form)
            for i in range(len(form) - MIN_RUN + 1):
                self._runs.add(form[i:i + MIN_RUN])

    def scrub(self, text: str) -> str:
        if not text or not self._forms:
            return text
        n = len(text)
        hit = [False] * n
        # Whole forms first (covers values shorter than MIN_RUN), then every 8-character window of any form.
        for form in self._forms:
            start = text.find(form)
            while start != -1:
                for k in range(start, start + len(form)):
                    hit[k] = True
                start = text.find(form, start + 1)
        if self._runs:
            for i in range(n - MIN_RUN + 1):
                if text[i:i + MIN_RUN] in self._runs:
                    for k in range(i, i + MIN_RUN):
                        hit[k] = True
        if not any(hit):
            return text
        out: list[str] = []
        i = 0
        while i < n:
            if hit[i]:
                while i < n and hit[i]:
                    i += 1
                out.append(MASK)
            else:
                out.append(text[i])
                i += 1
        return "".join(out)

    def scrub_obj(self, obj):
        """Scrub every string inside a JSON-like value (dict keys too)."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {self.scrub(k) if isinstance(k, str) else k: self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub_obj(v) for v in obj]
        return obj
