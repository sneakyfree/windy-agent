#!/usr/bin/env python3
"""Extract the full capability matrix from windy-agent source.

Phase 3.1 of the launch gauntlet — auto-extract every slash command,
capability (tool), and channel adapter so we know the complete user-facing
surface and can build a 4-cell test matrix on top (happy + 3 failure modes
per capability).

Output: ~/.windy-stress/capability_matrix.csv

Uses AST + filesystem scan rather than importing the bot, so it runs in
~1s and doesn't require any boot machinery.
"""

from __future__ import annotations

import ast
import csv
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent  # windy-agent repo root
OUT = Path.home() / ".windy-stress" / "capability_matrix.csv"


def _literal(node: ast.AST) -> str | None:
    """Safely literal_eval a string node; return None on anything else."""
    try:
        v = ast.literal_eval(node)
    except (ValueError, SyntaxError):
        return None
    return v if isinstance(v, str) else None


def extract_commands() -> list[dict[str, str]]:
    """Scan commands/core.py + ecosystem.py for _r() and _re() calls.

    Both helpers have signature ``_r(name, desc, category, handler, ...)``
    so the first three positional args are reliable.
    """
    rows: list[dict[str, str]] = []
    for path, registrar in [
        (ROOT / "src/windyfly/commands/core.py", "_r"),
        (ROOT / "src/windyfly/commands/ecosystem.py", "_re"),
    ]:
        if not path.exists():
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == registrar
                    and len(node.args) >= 3):
                continue
            name = _literal(node.args[0])
            desc = _literal(node.args[1])
            category = _literal(node.args[2])
            if not (name and desc and category):
                continue
            rows.append({
                "type": "command",
                "name": f"/{name}",
                "category": category,
                "source": str(path.relative_to(ROOT)),
                "description": desc[:120],
            })
    return rows


def extract_capabilities() -> list[dict[str, str]]:
    """Scan agent/capabilities/ for capability registration sites.

    Captures both ``capability_registry.register(...)`` calls and direct
    ``Capability(name=..., ...)`` constructions so we count each tool
    plug-in regardless of registration style.
    """
    rows: list[dict[str, str]] = []
    cap_dir = ROOT / "src/windyfly/agent/capabilities"
    if not cap_dir.exists():
        return rows

    # Pattern: Capability(name="foo", ...) — find name= arg in literal form
    name_arg_re = re.compile(r'Capability\([^)]*?name\s*=\s*["\']([^"\']+)["\']')

    for py_file in sorted(cap_dir.glob("*.py")):
        if py_file.name == "__init__.py":
            continue
        text = py_file.read_text()
        names = name_arg_re.findall(text)
        register_calls = text.count("capability_registry.register")
        # When we can pull names, emit one row per name; otherwise count
        # the module as a registration site so it's still visible in the
        # matrix.
        if names:
            for name in names:
                rows.append({
                    "type": "capability",
                    "name": name,
                    "category": py_file.stem,
                    "source": str(py_file.relative_to(ROOT)),
                    "description": f"Capability registered in {py_file.stem}",
                })
        elif register_calls:
            rows.append({
                "type": "capability_module",
                "name": py_file.stem,
                "category": "registration_site",
                "source": str(py_file.relative_to(ROOT)),
                "description": (
                    f"{register_calls} capability_registry.register() calls "
                    f"(names not statically extractable)"
                ),
            })
    return rows


def extract_channels() -> list[dict[str, str]]:
    """Find channel adapter modules under channels/.

    Skip infrastructure modules (base, manager, slash_commands) — those
    aren't user-facing channels.
    """
    rows: list[dict[str, str]] = []
    ch_dir = ROOT / "src/windyfly/channels"
    if not ch_dir.exists():
        return rows
    infrastructure = {
        "__init__.py", "base.py", "manager.py", "slash_commands.py",
    }
    for py_file in sorted(ch_dir.glob("*.py")):
        if py_file.name in infrastructure:
            continue
        rows.append({
            "type": "channel",
            "name": py_file.stem,
            "category": "channel_adapter",
            "source": str(py_file.relative_to(ROOT)),
            "description": f"channel adapter ({py_file.name})",
        })
    return rows


def main() -> int:
    commands = extract_commands()
    capabilities = extract_capabilities()
    channels = extract_channels()
    rows = commands + capabilities + channels

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["type", "name", "category", "source", "description"],
        )
        writer.writeheader()
        for row in sorted(rows, key=lambda r: (r["type"], r["name"])):
            writer.writerow(row)

    counts: dict[str, int] = {}
    for r in rows:
        counts[r["type"]] = counts.get(r["type"], 0) + 1

    print(f"Extracted {len(rows)} capabilities → {OUT}")
    for t, c in sorted(counts.items()):
        print(f"  {t:>20}: {c}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
