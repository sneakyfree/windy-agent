#!/usr/bin/env python3
"""Fold changelog.d/*.md fragments into CHANGELOG.md under "## Unreleased" (see changelog.d/README.md).

    python3 scripts/changelog_fold.py           fold, then delete the fragment files
    python3 scripts/changelog_fold.py --check   validate only; changes nothing

Order: numbered fragments (<pr>.md) newest PR first, then named ones alphabetically, all ABOVE the entries
already under "## Unreleased" (newer on top, as the file already reads).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

HEADING = "## Unreleased"


def fragments(dir_: Path) -> list[Path]:
    files = [p for p in dir_.glob("*.md") if p.name != "README.md"]
    numbered = sorted((p for p in files if p.stem.isdigit()), key=lambda p: int(p.stem), reverse=True)
    named = sorted((p for p in files if not p.stem.isdigit()), key=lambda p: p.name)
    return numbered + named


def check(frag: Path) -> str | None:
    text = frag.read_text(encoding="utf-8").strip()
    if not text:
        return f"{frag.name}: empty"
    if not text.startswith("- "):
        return f"{frag.name}: must start with a '- ' bullet"
    if re.search(r"^#", text, re.MULTILINE):
        return f"{frag.name}: no headings in a fragment"
    return None


def fold(root: Path) -> int:
    changelog, dir_ = root / "CHANGELOG.md", root / "changelog.d"
    frags = fragments(dir_)
    if not frags:
        return 0
    text = changelog.read_text(encoding="utf-8")
    at = text.find(f"\n{HEADING}\n")
    if at < 0:
        raise SystemExit(f'CHANGELOG.md has no "{HEADING}" heading')
    head_end = at + len(HEADING) + 2
    rest = text[head_end:].lstrip("\n")
    block = "".join(f.read_text(encoding="utf-8").strip() + "\n\n" for f in frags)
    changelog.write_text(text[:head_end] + "\n" + block + rest, encoding="utf-8")
    for f in frags:
        f.unlink()
    return len(frags)


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parents[1]
    problems = [m for f in fragments(root / "changelog.d") if (m := check(f))]
    if problems:
        print("\n".join(problems))
        return 1
    if "--check" in argv:
        print(f"{len(fragments(root / 'changelog.d'))} fragment(s) OK")
        return 0
    print(f"folded {fold(root)} fragment(s) into CHANGELOG.md")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
