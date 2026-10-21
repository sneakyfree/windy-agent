"""changelog.d fragments fold into CHANGELOG.md "## Unreleased" (no more one-line CHANGELOG conflicts)."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "changelog_fold.py"
_spec = importlib.util.spec_from_file_location("changelog_fold", SCRIPT)
cf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cf)

BASE = "# Changelog\n\nintro\n\n## Unreleased\n\n- **Old entry.** Already here.\n\n## 0.7.5\n\n- old release\n"


def _tree(tmp_path, frags: dict[str, str]):
    (tmp_path / "changelog.d").mkdir()
    (tmp_path / "changelog.d" / "README.md").write_text("how to\n")
    (tmp_path / "CHANGELOG.md").write_text(BASE)
    for name, body in frags.items():
        (tmp_path / "changelog.d" / name).write_text(body)
    return tmp_path


def test_fold_puts_newest_pr_first_above_existing_entries_and_deletes_fragments(tmp_path):
    root = _tree(tmp_path, {"620.md": "- **Six twenty.** A.\n", "623.md": "- **Six twenty-three.** B.\n",
                            "probe-hardening.md": "- **Named.** C.\n"})
    assert cf.fold(root) == 3
    text = (root / "CHANGELOG.md").read_text()
    order = [text.index(s) for s in ("Six twenty-three", "Six twenty.", "Named.", "Old entry", "## 0.7.5")]
    assert order == sorted(order)
    assert sorted(p.name for p in (root / "changelog.d").iterdir()) == ["README.md"]
    assert "## Unreleased\n\n- **Six twenty-three.**" in text


def test_no_fragments_changes_nothing(tmp_path):
    root = _tree(tmp_path, {})
    assert cf.fold(root) == 0 and (root / "CHANGELOG.md").read_text() == BASE


@pytest.mark.parametrize("body,problem", [("", "empty"), ("Plain text.\n", "bullet"), ("- ok\n## Heading\n", "headings")])
def test_check_refuses_a_bad_fragment(tmp_path, body, problem):
    root = _tree(tmp_path, {"9.md": body})
    assert problem in cf.check(root / "changelog.d" / "9.md")


def test_the_repo_fragments_are_valid():
    root = SCRIPT.parents[1]
    assert [m for f in cf.fragments(root / "changelog.d") if (m := cf.check(f))] == []
