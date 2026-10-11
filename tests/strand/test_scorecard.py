"""The strand v2 scorecard is executable (Wave 5; Hub's rules 10-11).

docs/strand/STRAND_V2.md has one row per codon: id | promise | status | test. This test fails if:
- a `live` codon names no test, a test file or function that does not exist, or a test that can
  be skipped or xfailed (decorator, module/class pytestmark, or a pytest.skip/xfail/importorskip
  call): a skipped test never counts as green;
- a `retired` row has no decision and PR (`retired: <decision> (#123)`);
- a `planned` row names no owner (`planned: <owner>: <what>`);
- a codon id repeats, or a non-live row names a test;
- a test in tests/strand/test_p*.py / test_n*.py is not claimed by any live codon (orphan).
The tests themselves run in the normal suite; this file proves the doc and the tests agree.
Files are read with ast, never imported.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "strand" / "STRAND_V2.md"
ID_RE = re.compile(r"^[PN]\d+(\.[0-9A-Za-z]+)+$")
ROW_RE = re.compile(r"^\|\s*([PN]\d[^|]*?)\s*\|(.*?)\|(.*?)\|(.*?)\|\s*$")
SKIP_WORDS = ("skip", "xfail", "importorskip")


def parse_rows(text: str) -> list[dict[str, str]]:
    rows = []
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if m:
            rows.append({"id": m.group(1).strip(), "promise": m.group(2).strip(),
                         "status": m.group(3).strip(), "test": m.group(4).strip().strip("`")})
    return rows


def _mentions_skip(node: ast.AST) -> bool:
    src = ast.unparse(node)
    return any(re.search(rf"\b{w}\b", src) for w in SKIP_WORDS)


def _calls_skip(fn: ast.AST) -> bool:
    for n in ast.walk(fn):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in SKIP_WORDS:
            return True
    return False


def _module_marked(tree: ast.Module, body: list[ast.stmt]) -> bool:
    for stmt in body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark"
                                                for t in stmt.targets):
            if _mentions_skip(stmt.value):
                return True
    return False


def find_test(root: Path, node: str) -> tuple[ast.AST | None, str]:
    """(function node, problem). Problem is "" when the test exists and cannot be skipped."""
    path_part, _, rest = node.partition("::")
    path = root / path_part
    if not rest or not path.is_file() or not path_part.startswith("tests/"):
        return None, f"no such test file {path_part!r}"
    names = [n.split("[", 1)[0] for n in rest.split("::")]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    if _module_marked(tree, tree.body):
        return None, "module is marked skip/xfail"
    scope: list[ast.stmt] = tree.body
    found: ast.AST | None = None
    for i, name in enumerate(names):
        match = [n for n in scope if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                 and n.name == name]
        if not match:
            return None, f"{name!r} not found"
        found = match[0]
        if any(_mentions_skip(d) for d in found.decorator_list):
            return None, f"{name!r} is decorated skip/xfail"
        if isinstance(found, ast.ClassDef):
            if _module_marked(tree, found.body):
                return None, f"class {name!r} is marked skip/xfail"
            scope = found.body
    if not isinstance(found, (ast.FunctionDef, ast.AsyncFunctionDef)) or not found.name.startswith("test"):
        return None, "not a test function"
    if _calls_skip(found):
        return None, "calls pytest.skip/xfail"
    if "[" in node and not any("parametrize" in ast.unparse(d) for d in found.decorator_list):
        return None, "names a parameter but the test is not parametrized"
    return found, ""


def check(rows: list[dict[str, str]], root: Path) -> list[str]:
    problems: list[str] = []
    seen: set[str] = set()
    claimed: set[tuple[str, str]] = set()
    for r in rows:
        cid, status, test = r["id"], r["status"], r["test"]
        if not ID_RE.match(cid):
            problems.append(f"{cid}: bad codon id")
        if cid in seen:
            problems.append(f"{cid}: repeated")
        seen.add(cid)
        if status == "live":
            _, problem = find_test(root, test)
            if problem:
                problems.append(f"{cid}: {problem}")
            else:
                path_part, _, rest = test.partition("::")
                claimed.add((path_part, rest.split("::")[-1].split("[", 1)[0]))
        elif status.startswith("retired:"):
            if not re.search(r"\(#\d+(, ?#\d+)*\)\s*$", status) or len(status) < 20:
                problems.append(f"{cid}: retired without a decision and PR")
        elif status.startswith("planned:"):
            if not re.match(r"planned: [^:]{2,}: .{5,}", status):
                problems.append(f"{cid}: planned without an owner and what")
        else:
            problems.append(f"{cid}: unknown status {status!r}")
        if status != "live" and test not in ("", "—", "-"):
            problems.append(f"{cid}: a {status.split(':')[0]} codon names a test")
    for f in sorted((root / "tests" / "strand").glob("test_[pn]*.py")):
        rel = f.relative_to(root).as_posix()
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name.startswith("test"):
                if (rel, n.name) not in claimed:
                    problems.append(f"orphan strand test {rel}::{n.name}")
    return problems


def test_the_strand_doc_and_the_tests_agree():
    rows = parse_rows(DOC.read_text(encoding="utf-8"))
    assert rows, "no codon rows parsed"
    problems = check(rows, ROOT)
    assert not problems, "\n".join(problems)


# ── the checker itself must catch what it promises to catch ─────────────────────────────

def _tree(tmp_path: Path, body: str) -> Path:
    (tmp_path / "tests" / "strand").mkdir(parents=True)
    (tmp_path / "tests" / "strand" / "test_p9_x.py").write_text(body, encoding="utf-8")
    return tmp_path


def _row(cid: str, status: str, test: str) -> dict[str, str]:
    return {"id": cid, "promise": "p", "status": status, "test": test}


def test_checker_accepts_a_real_test(tmp_path):
    root = _tree(tmp_path, "def test_a():\n    assert True\n")
    assert check([_row("P9.1", "live", "tests/strand/test_p9_x.py::test_a")], root) == []


def test_checker_rejects_skips_xfails_missing_and_orphans(tmp_path):
    root = _tree(tmp_path, (
        "import pytest\n"
        "@pytest.mark.skip(reason='x')\ndef test_dec():\n    pass\n"
        "@pytest.mark.xfail\ndef test_xf():\n    pass\n"
        "def test_call():\n    pytest.skip('later')\n"
        "def test_orphan():\n    assert True\n"
    ))
    rows = [_row("P9.1", "live", "tests/strand/test_p9_x.py::test_dec"),
            _row("P9.2", "live", "tests/strand/test_p9_x.py::test_xf"),
            _row("P9.3", "live", "tests/strand/test_p9_x.py::test_call"),
            _row("P9.4", "live", "tests/strand/test_p9_x.py::test_gone"),
            _row("P9.5", "live", "")]
    problems = "\n".join(check(rows, root))
    for cid in ("P9.1", "P9.2", "P9.3", "P9.4", "P9.5"):
        assert f"{cid}:" in problems
    assert "orphan strand test tests/strand/test_p9_x.py::test_orphan" in problems


def test_checker_rejects_a_skipped_module(tmp_path):
    root = _tree(tmp_path, "import pytest\npytestmark = pytest.mark.skip\ndef test_a():\n    pass\n")
    assert check([_row("P9.1", "live", "tests/strand/test_p9_x.py::test_a")], root)


def test_checker_status_rules(tmp_path):
    root = _tree(tmp_path, "")
    rows = [_row("P9.1", "retired: gone", "—"),                          # no PR
            _row("P9.2", "retired: shape-shift removed (#501)", "—"),   # ok
            _row("P9.3", "planned: nobody", "—"),                       # no what
            _row("P9.4", "planned: Windy Agent: make migrations atomic", "—"),
            _row("P9.4", "wishful", "—"),                                # repeat + unknown
            _row("P9.5", "planned: Windy Agent: later work", "tests/x.py::test_y")]
    problems = "\n".join(check(rows, root))
    assert "P9.1: retired without" in problems and "P9.2:" not in problems
    assert "P9.3: planned without" in problems and "P9.4: repeated" in problems
    assert "unknown status 'wishful'" in problems and "P9.5: a planned codon names a test" in problems
