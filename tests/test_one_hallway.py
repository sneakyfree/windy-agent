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


# A door built at run time: "/bots/" followed by a variable that ENDS the path (f".../bots/{door}" or
# "/bots/" + door). Only a passport may sit there (GET /api/v1/bots/{passport} is a lookup, not a door).
_PASSPORTISH = re.compile(r"passport|\bpp\b|\bpid\b", re.IGNORECASE)


def _dynamic_doors(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts = node.values
            for i, part in enumerate(parts[:-1]):
                nxt, after = parts[i + 1], parts[i + 2] if i + 2 < len(parts) else None
                ends_path = after is None or (isinstance(after, ast.Constant)
                                              and re.match(r"/?(?:$|[?#])", str(after.value)) is not None)
                if (isinstance(part, ast.Constant) and str(part.value).endswith("/bots/")
                        and isinstance(nxt, ast.FormattedValue) and ends_path
                        and not _PASSPORTISH.search(ast.unparse(nxt.value))):
                    yield node
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
            left = node.left
            if isinstance(left, ast.Constant) and isinstance(left.value, str):
                plus = isinstance(node.op, ast.Add) and left.value.endswith("/bots/")
                pct = isinstance(node.op, ast.Mod) and re.search(r"/bots/%[sdr]/?(?:$|[?#])", left.value)
                if (plus or pct) and not _PASSPORTISH.search(ast.unparse(node.right)):
                    yield node
        elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "format"
              and isinstance(node.func.value, ast.Constant) and isinstance(node.func.value.value, str)
              and re.search(r"/bots/\{[^}]*\}/?(?:$|[?#])", node.func.value.value)
              and not _PASSPORTISH.search(" ".join(ast.unparse(a) for a in [*node.args, *[k.value for k in node.keywords]]))):
            yield node


def test_no_source_file_builds_a_minting_door_at_run_time():
    hits = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        hits += [f"{path.relative_to(SRC)}:{n.lineno}" for n in _dynamic_doors(tree)]
    assert hits == [], f"a /bots/<door> path is built at run time: {hits}"


def test_the_dynamic_check_catches_a_door_and_spares_real_routes():
    def found(src: str) -> int:
        return len(list(_dynamic_doors(ast.parse(src))))

    assert found('door = "register"\nurl = f"{base}/api/v1/bots/{door}"') == 1
    assert found('url = f"/api/v1/bots/{kind}?x=1"') == 1
    assert found('url = "/api/v1/bots/" + door') == 1
    assert found('url = f"{base}/api/v1/bots/{door}/"') == 1           # trailing slash
    assert found('url = "/api/v1/bots/{}".format(door)') == 1
    assert found('url = "/api/v1/bots/%s" % door') == 1
    assert found('url = "/api/v1/bots/{}/keys".format(passport)') == 0  # a route below a passport
    assert found('url = "/api/v1/bots/%s" % passport') == 0
    assert found('url = f"{base}/api/v1/bots/{passport}"') == 0       # the lookup
    assert found('url = f"/api/v1/bots/{pp}/ept/refresh"') == 0       # a route below a passport
    assert found('url = f"/api/v1/bots/{self.passport_id}/keys"') == 0


def test_the_eternitas_client_has_no_minting_method():
    for name in ("register", "auto_hatch", "provision"):
        assert not hasattr(EternitasClient, name), name


def test_the_hatch_goes_through_hub_tickets():
    from windyfly import hub_hatch

    assert hub_hatch.TICKETS_PATH == "/api/v1/agent/hatch/tickets"
