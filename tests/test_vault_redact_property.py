"""Property tests for the normalizing redactor (Boss 2026-10-28: a list of forms always misses the next one).

Random escapings of a registered value, one or two layers deep in any order (JSON string escapes, percent-encoding,
HTML character references, with the case and the choice of escape picked per character), never get past scrub();
a value one character off is never masked (no oracle); and hostile input can't stall the decoders. Seeded, so a
failure names its seed and reproduces."""

from __future__ import annotations

import random
import time
from collections.abc import Callable

import pytest

from windyfly.vault.redact import MASK, Redactor

VALUES = [
    "sk_live_51AbCdEfGhIjKlMn0123",
    'pa"ss\\wörd-0123456789xyz',             # quote, backslash, non-ASCII
    "ghp_A/b+C=d%41&amp;<x>'y'-0123456789",  # raw %XX and &entity; inside the value itself
    "tök€n-\U0001F600-0123456789abcdef",     # 2-, 3- and 4-byte UTF-8 (a surrogate pair in JSON)
    "a b c d e f g h i j",                   # spaces
]
PREFIX, SUFFIX = "ECHO[", "]END"
RUNS = 400


def _hexcase(rng: random.Random, digits: str) -> str:
    return "".join(c.upper() if rng.random() < 0.5 else c.lower() for c in digits)


def _json(rng: random.Random, text: str) -> str:
    out = []
    for c in text:
        cp = ord(c)
        if c in '"\\' or cp < 0x20:      # must be escaped
            short = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\t": "\\t"}.get(c)
            out.append(short if short and rng.random() < 0.5 else "\\u" + _hexcase(rng, f"{cp:04x}"))
        elif c == "/" and rng.random() < 0.5:
            out.append("\\/")
        elif rng.random() < 0.4:
            if cp > 0xFFFF:
                v = cp - 0x10000
                out.append("\\u" + _hexcase(rng, f"{0xD800 + (v >> 10):04x}") +
                           "\\u" + _hexcase(rng, f"{0xDC00 + (v & 0x3FF):04x}"))
            else:
                out.append("\\u" + _hexcase(rng, f"{cp:04x}"))
        else:
            out.append(c)
    return "".join(out)


def _pct(rng: random.Random, text: str) -> str:
    out = []
    for c in text:
        if c != "%" and rng.random() < 0.5:  # a raw '%' would make the text ambiguous: always encoded
            out.append(c)
        else:
            out.append("".join("%" + _hexcase(rng, f"{b:02x}") for b in c.encode("utf-8")))
    return "".join(out)


def _html(rng: random.Random, text: str) -> str:
    named = {'"': "&quot;", "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&apos;"}
    out = []
    for c in text:
        r = rng.random()
        if c in named and r < 0.4:
            out.append(named[c])
        elif c == "&" or r < 0.3:            # a raw '&' would make the text ambiguous: always encoded
            out.append(f"&#{ord(c)};" if rng.random() < 0.5 else
                       "&#" + rng.choice("xX") + _hexcase(rng, f"{ord(c):x}") + ";")
        else:
            out.append(c)
    return "".join(out)


LAYERS: list[Callable[[random.Random, str], str]] = [_json, _pct, _html]


def _encode(rng: random.Random, value: str) -> tuple[str, list[str]]:
    layers = [rng.choice(LAYERS) for _ in range(rng.choice((1, 2)))]
    text = value
    for layer in layers:
        text = layer(rng, text)
    return text, [f.__name__ for f in layers]


@pytest.mark.parametrize("value", VALUES)
def test_random_escapings_never_get_past_scrub(value):
    r = Redactor()
    r.register(value)
    for seed in range(RUNS):
        rng = random.Random(f"{value}|{seed}")
        encoded, layers = _encode(rng, value)
        out = r.scrub(PREFIX + encoded + SUFFIX)
        assert out == PREFIX + MASK + SUFFIX, f"seed {seed}, layers {layers}: not masked whole"


@pytest.mark.parametrize("value", VALUES)
def test_a_value_one_character_off_is_never_masked(value):
    # The anti-oracle half: only the WHOLE value is masked, however it is escaped.
    r = Redactor()
    r.register(value)
    for seed in range(RUNS):
        rng = random.Random(f"near|{value}|{seed}")
        i = rng.randrange(len(value))
        near = value[:i] + ("Q" if value[i] != "Q" else "R") + value[i + 1:]
        encoded, layers = _encode(rng, near)
        text = PREFIX + encoded + SUFFIX
        assert r.scrub(text) == text, f"seed {seed}, layers {layers}: a near miss was masked"


def test_two_values_in_one_text_are_both_masked():
    r = Redactor()
    r.register(VALUES[0])
    r.register(VALUES[1])
    rng = random.Random("two")
    a, _ = _encode(rng, VALUES[0])
    b, _ = _encode(rng, VALUES[1])
    assert r.scrub(f"x{a}y{b}z") == f"x{MASK}y{MASK}z"


@pytest.mark.parametrize("hostile", [
    "\\" * (1 << 20),
    "\\u" * (1 << 19),
    "\\uD800" * (1 << 17),
    "%" * (1 << 20),
    "%e2%82" * (1 << 17),
    "&#" * (1 << 19),
    "&#x1F600;" * (1 << 16),
    ("%5Cu00" + "&#38;") * (1 << 16),
], ids=["backslashes", "bare-u", "lone-surrogates", "percents", "cut-utf8", "bare-refs", "emoji-refs", "mixed"])
def test_hostile_input_cannot_stall_the_decoders(hostile):
    r = Redactor()
    r.register(VALUES[1])
    t0 = time.monotonic()
    assert MASK not in r.scrub(hostile)
    assert time.monotonic() - t0 < 10, "the scrub of 1 MiB took too long"


def test_a_match_inside_another_match_is_one_mask():
    # Two leased values, one inside the other, found in different layers: the raw text shows the inner one, the
    # HTML-decoded text the outer one. The inner span lies inside the outer: one mask, nothing repeated.
    inner = VALUES[0]
    outer = "pre" + inner + "post"
    r = Redactor()
    r.register(inner)
    r.register(outer)
    assert r.scrub("<" + "&#112;re" + inner + "post" + ">") == "<" + MASK + ">"
