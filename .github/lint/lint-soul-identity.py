#!/usr/bin/env python3
"""Fail a PR that adds a real agent's soul or identity to this PUBLIC generic repo (Boss repo review, BOARD
2026-11-06T22:10:16Z; Windy Agent's plan 22:11:33Z).

This repo is the generic agent "class"; each real agent keeps its soul, name and ids in its own private soul repo
(CLAUDE.md, "Architectural rule"). The check looks at the PR's ADDED files and ADDED lines only, so today's tree passes.

It fails on:
  1. a NEW soul file anywhere except the repo's own default SOUL.md: SOUL.md / IDENTITY.md / ECOSYSTEM.md, *.soul.md,
     or any file inside a folder named soul, souls or *-soul;
  2. an added line with a real Eternitas passport id (ET2x-XXXX-XXXX, not a TEST one) or a real agent Matrix id
     (@agent_et2x-xxxx-xxxx, not a test one);
  3. a .toml file whose [agent] name is set, on an added line, to anything but the product default;
  4. a name from .github/lint/soul-identity-denylist.txt (real agent and owner names; public names only) on an added
     line of the default SOUL.md, the personality code, or a .toml file (where identity text would actually ship).
A line holding "soul-lint: allow" is skipped (a test fixture that needs the shape). Output never prints the matched
text, only the file, the line number and the rule.

usage: lint-soul-identity.py <base-ref>     (e.g. origin/master; compares <base-ref>...HEAD)
"""
from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

SOUL_NAMES = {"soul.md", "identity.md", "ecosystem.md"}
PRODUCT_NAMES = {"windy fly", "hifly"}
ALLOW = "soul-lint: allow"
PASSPORT = re.compile(r"\bET2\d-(?!TEST-)(?!X{4}-)[A-Z0-9]{4}-[A-Z0-9]{4}\b", re.IGNORECASE)
AGENT_MXID = re.compile(r"@agent_et2\d-(?!test-)(?!x{4}-)[a-z0-9]{4}-[a-z0-9]{4}\b", re.IGNORECASE)
TOML_NAME = re.compile(r"^\s*name\s*=\s*[\"']([^\"']*)[\"']")


def soul_file_problem(path: str) -> str | None:
    """Rule 1 for one ADDED file path (repo-relative, '/'-separated)."""
    p = PurePosixPath(path)
    if path == "SOUL.md":
        return None                       # the repo's own default soul
    if p.name.lower() in SOUL_NAMES or p.name.lower().endswith(".soul.md"):
        return "adds a soul file (a real agent's soul belongs in its own private soul repo)"
    for part in p.parts[:-1]:
        low = part.lower()
        if low in {"soul", "souls"} or low.endswith("-soul"):
            return "adds a file inside a soul folder (a real agent's soul belongs in its own private soul repo)"
    return None


def _in_denylist_scope(path: str) -> bool:
    return path == "SOUL.md" or path.startswith("src/windyfly/personality/") or path.endswith(".toml")


def line_problems(path: str, line: str, denylist: list[str]) -> list[str]:
    """Rules 2 and 4 for one ADDED line."""
    if ALLOW in line:
        return []
    out = []
    if PASSPORT.search(line):
        out.append("a real Eternitas passport id (use an ET26-TEST- or ET26-XXXX-XXXX placeholder)")
    if AGENT_MXID.search(line):
        out.append("a real agent Matrix id (use a test one)")
    if denylist and _in_denylist_scope(path):
        low = line.casefold()
        if any(name.casefold() in low for name in denylist):
            out.append("a real agent or owner name from the denylist, where identity text would ship")
    return out


def toml_agent_name_problem(toml_text: str, added: list[str]) -> str | None:
    """Rule 3: the file's [agent] name is set to a non-default name ON AN ADDED line."""
    try:
        name = (tomllib.loads(toml_text).get("agent") or {}).get("name")
    except tomllib.TOMLDecodeError:
        return None
    if not isinstance(name, str) or " ".join(name.split()).casefold() in PRODUCT_NAMES:
        return None
    for line in added:
        m = TOML_NAME.match(line)
        if m and m.group(1) == name and ALLOW not in line:
            return "sets a real agent's [agent] name (instance config belongs in its own soul repo)"
    return None


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True).stdout


def changed_files(base: str, *, added_only: bool = False) -> list[str]:
    """Paths changed in base...HEAD, NUL-separated (no quoting) and with renames OFF, so a rename counts as an added
    file (Hub on #747: `git mv notes.md souls/x.md` must not slip through)."""
    args = ["diff", "--name-only", "-z", "--no-renames"]
    if added_only:
        args.append("--diff-filter=A")
    return [p for p in _git(*args, f"{base}...HEAD").split("\0") if p]


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def parse_added(diff_text: str) -> list[tuple[int, str]]:
    """Added lines of ONE file's ``git diff --unified=0`` output: [(new line number, text)].

    Hunks are read by their counts (@@ -a,b +c,d @@ = b removed lines, then d added lines), never by guessing from a
    line's first characters: an added line whose text starts with "++ " shows as "+++ " and once ended the scan
    (Hub on #747)."""
    out: list[tuple[int, str]] = []
    lines = diff_text.split("\n")
    i = 0
    while i < len(lines):
        m = _HUNK.match(lines[i])
        i += 1
        if not m:
            continue
        removed = int(m.group(1)) if m.group(1) is not None else 1
        new_no = int(m.group(2))
        added = int(m.group(3)) if m.group(3) is not None else 1
        while removed and i < len(lines):
            if lines[i].startswith("-"):
                removed -= 1
            i += 1
        while added and i < len(lines):
            line = lines[i]
            i += 1
            if line.startswith("\\"):          # "\ No newline at end of file"
                continue
            out.append((new_no, line[1:]))
            new_no += 1
            added -= 1
    return out


def added_lines(base: str) -> dict[str, list[tuple[int, str]]]:
    """{path: [(new line number, text), ...]} for every added line in base...HEAD."""
    result: dict[str, list[tuple[int, str]]] = {}
    for path in changed_files(base):
        diff = _git("diff", "--unified=0", "--no-color", "--no-ext-diff", "--no-renames", f"{base}...HEAD", "--", path)
        lines = parse_added(diff)
        if lines:
            result[path] = lines
    return result


def main(base: str) -> int:
    root = Path(".")
    deny_file = root / ".github/lint/soul-identity-denylist.txt"
    denylist = [x.strip() for x in deny_file.read_text().splitlines() if x.strip() and not x.startswith("#")] \
        if deny_file.exists() else []
    problems: list[str] = []
    for path in changed_files(base, added_only=True):
        why = soul_file_problem(path)
        if why:
            problems.append(f"{path}: {why}")
    for path, lines in added_lines(base).items():
        if path == ".github/lint/soul-identity-denylist.txt":
            continue                      # the denylist itself
        for n, text in lines:
            for why in line_problems(path, text, denylist):
                problems.append(f"{path}:{n}: {why}")
        if path.endswith(".toml") and (root / path).exists():
            why = toml_agent_name_problem((root / path).read_text(encoding="utf-8"), [t for _, t in lines])
            if why:
                problems.append(f"{path}: {why}")
    for p in problems:
        print(f"soul-identity-lint: {p}")
    if problems:
        print(f"soul-identity-lint: {len(problems)} problem(s). This repo is PUBLIC and generic; a real agent's soul, "
              "name and ids live in its own private soul repo. Test fixtures: use TEST ids or add 'soul-lint: allow'.")
        return 1
    print("soul-identity-lint: OK (added files and lines only)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "origin/master"))
