"""One hallway, one certifying authority (Grant; ADR-059): windyfly never mints a passport.

Every agent is born through Hub's hatch ceremony (windyfly.hub_hatch). Eternitas mints there. This tripwire
fails if a direct minting door to Eternitas (/bots/register, /bots/auto-hatch) or a client method for one
comes back into windyfly.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import windyfly
from windyfly.eternitas.client import EternitasClient

SRC = Path(windyfly.__file__).resolve().parent
MINT_PATHS = re.compile(r"/bots/(register|auto-hatch)\b")


def _code_strings(tree: ast.AST):
    """Every string constant in the code, docstrings left out (they may name the removed doors)."""
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs:
            yield node


def test_no_source_file_calls_an_eternitas_minting_door():
    hits = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits += [f"{path.relative_to(SRC)}:{n.lineno}" for n in _code_strings(tree) if MINT_PATHS.search(n.value)]
    assert hits == [], f"direct passport minting is back: {hits}"


def test_the_eternitas_client_has_no_minting_method():
    for name in ("register", "auto_hatch", "provision"):
        assert not hasattr(EternitasClient, name), name


def test_the_hatch_goes_through_hub_tickets():
    from windyfly import hub_hatch

    assert hub_hatch.TICKETS_PATH == "/api/v1/agent/hatch/tickets"
