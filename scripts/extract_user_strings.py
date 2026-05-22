#!/usr/bin/env python3
"""Extract user-facing strings from windy-agent source — Phase 8.1.

Walks `src/windyfly/` and captures string literals that look user-
facing (printed, returned from command handlers, in error messages,
in prompt fragments). Writes to ~/.windy-stress/user_strings.txt for
the grandma-readability sweep (Phase 8.2) to chew on.

Heuristic — NOT exhaustive:
  - String literals containing emoji (✅, 🪰, etc.)
  - Strings passed to `return` in `cmd_*` async functions
  - Strings passed to `print(`, `lines.append(`, `messages.append(`
  - f-strings that look like sentences (start with capital + contain space)

Skips: docstrings, log strings (logger.x calls), SQL, regex patterns.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


REPO = Path("/home/grantwhitmer/Desktop/Grant's Folder/windy-agent")
OUT = Path.home() / ".windy-stress" / "user_strings.txt"

EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002600-\U000027BF✅✨⚠⭐✨]"
)
SENTENCE_RE = re.compile(r"^[A-Z🪰✅🔑⚠️🟢🟡🔴].{8,}\s")


def _is_user_facing(s: str) -> bool:
    if not isinstance(s, str) or len(s) < 8 or len(s) > 500:
        return False
    # Quick wins
    if EMOJI_RE.search(s):
        return True
    if SENTENCE_RE.match(s):
        # Reject obvious code/path/key strings
        if re.search(r"[/\\=:_]{3,}", s):
            return False
        if s.startswith(("http", "sk-", "ANTHROPIC", "Bearer ")):
            return False
        return True
    return False


def _walk(path: Path, sink: list[tuple[Path, int, str]]) -> None:
    try:
        text = path.read_text()
        tree = ast.parse(text)
    except (OSError, SyntaxError):
        return
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if _is_user_facing(node.value):
                sink.append((path, node.lineno, node.value))
        elif isinstance(node, ast.JoinedStr):
            # f-string — concat literal pieces to test
            literal = "".join(
                v.value for v in node.values
                if isinstance(v, ast.Constant) and isinstance(v.value, str)
            )
            if _is_user_facing(literal):
                sink.append((path, node.lineno, literal))


def main() -> int:
    sink: list[tuple[Path, int, str]] = []
    src = REPO / "src" / "windyfly"
    for py in sorted(src.rglob("*.py")):
        _walk(py, sink)
    # Dedupe + sort by file, line
    seen: set[tuple[str, int, str]] = set()
    unique: list[tuple[Path, int, str]] = []
    for p, ln, s in sink:
        key = (str(p), ln, s[:80])
        if key in seen:
            continue
        seen.add(key)
        unique.append((p, ln, s))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w") as f:
        f.write(f"# {len(unique)} user-facing strings (extracted {sys.argv[0]})\n")
        f.write("# format: <file>:<line>: <string>\n\n")
        for p, ln, s in unique:
            rel = p.relative_to(REPO)
            esc = s.replace("\n", "\\n")[:200]
            f.write(f"{rel}:{ln}: {esc}\n")
    print(f"Extracted {len(unique)} user-facing strings → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
