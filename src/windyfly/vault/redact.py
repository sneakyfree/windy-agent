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

NORMALIZED, not only listed (Boss 10-28: a list of forms always misses the next one). Besides the listed forms in the
raw text, scrub() DECODES the text through JSON string escapes (\\uXXXX in any case, surrogate pairs, \\" \\\\ \\/ and
the rest), percent-encoding (any case, UTF-8) and HTML character references, one layer and every ordered pair of
two layers, finds the whole forms in each decoded text, and masks the exact source span each one came from. The
decoders are single linear passes (no backtracking pattern a hostile server could stall).
"""

from __future__ import annotations

import base64
import binascii
import json
import math
import re
from bisect import bisect_right
from collections.abc import Callable, Iterator
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


class _Decoded:
    """Decoded text plus where each decoded character came from in the source text."""

    def __init__(self) -> None:
        self._parts: list[str] = []
        self._starts: list[int] = []                     # decoded offset of each part
        self._src: list[tuple[int, int, bool]] = []      # (source start, source end, literal)
        self._n = 0

    def add(self, text: str, s0: int, s1: int, literal: bool) -> None:
        if text:
            self._parts.append(text)
            self._starts.append(self._n)
            self._src.append((s0, s1, literal))
            self._n += len(text)

    def text(self) -> str:
        return "".join(self._parts)

    def _at(self, d: int) -> tuple[int, int]:
        i = bisect_right(self._starts, d) - 1
        s0, s1, literal = self._src[i]
        if literal:  # a literal run maps one to one
            o = s0 + d - self._starts[i]
            return o, o + 1
        return s0, s1  # one decoded character from one escape

    def span(self, d0: int, d1: int) -> tuple[int, int]:
        return self._at(d0)[0], self._at(d1 - 1)[1]


_JSON_ESC = re.compile(r'\\(?:u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})|u([0-9a-fA-F]{4})|(["\\/bfnrt]))')
_JSON_SHORT = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_HTML_REF = re.compile(r"&(?:#[xX]([0-9a-fA-F]{1,6})|#([0-9]{1,7})|(quot|amp|lt|gt|apos));")
_HTML_NAMED = {"quot": '"', "amp": "&", "lt": "<", "gt": ">", "apos": "'"}
_PCT_RUN = re.compile(r"(?:%[0-9a-fA-F]{2})+")


def _decode_json(text: str) -> _Decoded | None:
    if "\\" not in text:
        return None
    out, pos = _Decoded(), 0
    for m in _JSON_ESC.finditer(text):
        out.add(text[pos:m.start()], pos, m.start(), True)
        hi, lo, cp, short = m.groups()
        if hi:
            ch = chr(0x10000 + ((int(hi, 16) - 0xD800) << 10) + (int(lo, 16) - 0xDC00))
        elif cp:
            ch = chr(int(cp, 16))
        else:
            ch = _JSON_SHORT[short]
        out.add(ch, m.start(), m.end(), False)
        pos = m.end()
    out.add(text[pos:], pos, len(text), True)
    return out


def _decode_html(text: str) -> _Decoded | None:
    if "&" not in text:
        return None
    out, pos = _Decoded(), 0
    for m in _HTML_REF.finditer(text):
        hexa, dec, name = m.groups()
        cp = int(hexa, 16) if hexa else int(dec) if dec else -1
        if name:
            ch = _HTML_NAMED[name]
        elif 0 <= cp <= 0x10FFFF:
            ch = chr(cp)
        else:
            continue  # not a character: leave it as text
        out.add(text[pos:m.start()], pos, m.start(), True)
        out.add(ch, m.start(), m.end(), False)
        pos = m.end()
    out.add(text[pos:], pos, len(text), True)
    return out


def _utf8_len(lead: int) -> int:
    return 2 if 0xC2 <= lead <= 0xDF else 3 if 0xE0 <= lead <= 0xEF else 4 if 0xF0 <= lead <= 0xF4 else 1


def _decode_pct(text: str) -> _Decoded | None:
    if "%" not in text:
        return None
    out, pos = _Decoded(), 0
    for m in _PCT_RUN.finditer(text):
        out.add(text[pos:m.start()], pos, m.start(), True)
        run, raw = m.group(0), bytes.fromhex(m.group(0).replace("%", ""))
        i = 0
        while i < len(raw):
            n, s = _utf8_len(raw[i]), m.start() + 3 * i
            try:
                ch = raw[i:i + n].decode("utf-8") if i + n <= len(raw) else ""
            except UnicodeDecodeError:
                ch = ""
            if len(ch) == 1:
                out.add(ch, s, s + 3 * n, False)
                i += n
            else:  # not a whole UTF-8 character: keep the %XX as text
                out.add(run[3 * i:3 * i + 3], s, s + 3, True)
                i += 1
        pos = m.end()
    out.add(text[pos:], pos, len(text), True)
    return out


_DECODERS = (_decode_json, _decode_pct, _decode_html)
Mapper = Callable[[int, int], tuple[int, int]]


def _layers(text: str) -> Iterator[tuple[str, Mapper]]:
    """Every decoding of ``text`` one layer and two layers deep (each ordered pair), with a span map back to it."""
    for first in _DECODERS:
        a = first(text)
        if a is None:
            continue
        t1 = a.text()
        yield t1, a.span
        for second in _DECODERS:
            b = second(t1)
            if b is not None:
                yield b.text(), _compose(a, b)


def _compose(outer: _Decoded, inner: _Decoded) -> Mapper:
    def back(d0: int, d1: int) -> tuple[int, int]:
        return outer.span(*inner.span(d0, d1))

    return back


def _mask(text: str, spans: list[tuple[int, int]]) -> str:
    out, pos = [], 0
    for s0, s1 in sorted(spans):
        if s1 <= pos:
            continue
        out.append(text[pos:max(s0, pos)])
        if s0 >= pos:
            out.append(MASK)
        pos = s1
    out.append(text[pos:])
    return "".join(out)


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
        spans = [m.span() for m in self._re.finditer(text)]
        for decoded, back in _layers(text):
            spans += [back(*m.span()) for m in self._re.finditer(decoded)]
        return _mask(text, spans) if spans else text

    def scrub_obj(self, obj):
        """Scrub every string inside a JSON-like value (dict keys too)."""
        if isinstance(obj, str):
            return self.scrub(obj)
        if isinstance(obj, dict):
            return {self.scrub(k) if isinstance(k, str) else k: self.scrub_obj(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [self.scrub_obj(v) for v in obj]
        return obj
