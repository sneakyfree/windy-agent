"""The fire drill's lifeboat pass rule (scripts/fire-drill-lifeboat-check.py).

Sun 10-11 the local floor worked (auto-resurrect to llama3.2:3b) but the
drill failed: it wanted 🛟, and the auto-resurrect notice starts with 🚨.
The rule now takes either marker AND the answer to 2+2, read after the notice.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from windyfly.agent.loop import _auto_resurrect_banner

ROOT = Path(__file__).resolve().parents[1]
CHECK = ROOT / "scripts" / "fire-drill-lifeboat-check.py"
DRILL = ROOT / "scripts" / "fire-drill.sh"

_spec = importlib.util.spec_from_file_location("lifeboat_check", CHECK)
lifeboat_check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lifeboat_check)

# The real notice a dead Mind produces, built by the product code itself.
SIREN_NOTICE = _auto_resurrect_banner("llama3.2:3b", "Mind unreachable: connection refused")


def test_the_siren_notice_plus_the_answer_passes():
    ok, why = lifeboat_check.check(SIREN_NOTICE + "I'm here. 2 + 2 = 4.")
    assert ok, why


def test_the_lifebuoy_plus_the_answer_passes():
    ok, why = lifeboat_check.check("🛟 I'm alive on my backup brain. Two plus two is four.")
    assert ok, why


def test_no_marker_fails_even_with_the_answer():
    ok, why = lifeboat_check.check("I'm alive. 2 + 2 = 4.")
    assert not ok
    assert why == "no lifeboat marker"


def test_a_marker_with_no_answer_fails():
    ok, why = lifeboat_check.check(SIREN_NOTICE + "I'm alive, ask me anything.")
    assert not ok
    assert why == "marker but no answer to 2+2"


def test_a_four_in_the_notice_is_not_the_answer():
    notice = _auto_resurrect_banner("phi4", "Mind unreachable: connection refused")
    ok, why = lifeboat_check.check(notice + "I'm alive, ask me anything.")
    assert not ok
    assert why == "marker but no answer to 2+2"


def test_the_cli_exits_by_the_rule():
    good = subprocess.run([sys.executable, str(CHECK)], input=SIREN_NOTICE + "4",
                          capture_output=True, text=True, check=False)
    bad = subprocess.run([sys.executable, str(CHECK)], input="4",
                         capture_output=True, text=True, check=False)
    assert (good.returncode, bad.returncode) == (0, 1)
    assert good.stdout.startswith("LIFEBOAT_CHECK: PASS")
    assert bad.stdout.startswith("LIFEBOAT_CHECK: FAIL")


def test_a_four_inside_another_number_is_not_the_answer():
    for reply in ("2 + 2 = 40", "about 3.4", "4.5 maybe"):
        ok, _ = lifeboat_check.check("🛟 " + reply)
        assert not ok, reply


def test_the_drill_uses_the_rule_and_prints_the_whole_reply():
    whole = DRILL.read_text()
    text = whole[whole.index("drill_lifeboat() {"):whole.index("drill_engine_swap() {")]
    assert "fire-drill-lifeboat-check.py" in text
    assert "out[:200]" not in text
    assert "print(out)" in text
    # The scratch agent and the dead Mind port stay exactly as they were.
    assert 'MIND_API_URL="http://127.0.0.1:9"' in text
    assert 'WINDY_STATE_DIR="$SCRATCH/state-lifeboat"' in text
