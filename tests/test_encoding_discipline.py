"""Every file I/O in src/ must name its encoding.

Python's ``open`` / ``Path.read_text`` / ``Path.write_text`` default to
``locale.getpreferredencoding()``. On Linux and macOS that is UTF-8, so
omitting it is invisible. On Windows it is **cp1252**, and this codebase
is full of em-dashes and a 🪰 emoji.

PR #324 fixed the *tests* that read source files (the "Windows cp1252
failures"). It did not touch ``src/``, which still had 74 unencoded
reads and 57 unencoded writes — including ``quickstart.py``, which
wrote grandma's ``.env`` in cp1252 and then failed reading it back as
UTF-8:

    UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97
    in position 12: invalid start byte

(0x97 is an em-dash in cp1252.) That is the very first thing a Windows
user does, and Windows is most of the world's desktops — a Principle-#1
failure aimed squarely at the biggest slice of the ballroom.

This test is deliberately a grep rather than a runtime assertion: the
defect only manifests on a cp1252 host, so a Linux/macOS CI run can
never catch it by executing the code. Static discipline is the only
thing that holds here.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "windyfly"

# Binary modes carry no encoding — correctly excluded.
_OPEN_TEXT_WRITE = re.compile(r"""open\([^)]*?['"][wa]\+?['"]""")


def _py_files() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_unencoded_read_text() -> None:
    bad = [
        f"{p.relative_to(SRC)}:{i}"
        for p in _py_files()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if ".read_text()" in line
    ]
    assert not bad, (
        "read_text() without encoding — reads as cp1252 on Windows:\n  "
        + "\n  ".join(bad)
    )


def test_no_unencoded_write_text() -> None:
    bad: list[str] = []
    for p in _py_files():
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"\.write_text\(", text):
            # Scan to the matching close paren so multi-line calls are
            # judged on the whole call, not just the first line.
            depth, j = 0, m.end() - 1
            while j < len(text):
                if text[j] == "(":
                    depth += 1
                elif text[j] == ")":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if "encoding=" not in text[m.start():j]:
                line_no = text.count("\n", 0, m.start()) + 1
                bad.append(f"{p.relative_to(SRC)}:{line_no}")
    assert not bad, (
        "write_text() without encoding — writes cp1252 on Windows:\n  "
        + "\n  ".join(bad)
    )


def test_no_unencoded_text_mode_open() -> None:
    bad = [
        f"{p.relative_to(SRC)}:{i}"
        for p in _py_files()
        for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
        if _OPEN_TEXT_WRITE.search(line) and "encoding=" not in line
    ]
    assert not bad, (
        "open(..., 'w'/'a') without encoding:\n  " + "\n  ".join(bad)
    )
