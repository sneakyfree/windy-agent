"""CI smoke test for the v10 organ-harmony stress harness (stress/).

The harness drives the red alarm and the weekly brief. It used to live outside
version control, and its mocked LLM stopped matching call_llm's signature on
2026-05-20 (the new session_id kwarg). From then on, 34 of 39 scheduled runs
had 23/25 turns error out, and on 2026-09-23 the red alarm emailed Grant a
false "brain/memory failing". This test runs the harness in mocked mode on a
6-turn subset, hermetically (no Windy 0 env file, no network, no embeddings),
so the next signature change fails CI instead of the alarm.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HARNESS = REPO / "stress" / "stress_v10_organ_harmony.py"


def test_v10_harness_smoke_mocked(tmp_path: Path) -> None:
    home = tmp_path / "stress"
    home.mkdir()
    shutil.copy(REPO / "stress" / "config.example.toml", home / "config.toml")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "WINDY_STRESS_HOME": str(home),
        "WINDY_ENV_FILE": str(tmp_path / "no-such.env"),   # hermetic: load nothing
        "V10_TURNS": "smoke",
        "WINDY_STRESS_NO_EMBEDDINGS": "1",
        "PYTHONPATH": str(REPO / "src"),
    }
    proc = subprocess.run(
        [sys.executable, str(HARNESS)], env=env, cwd=str(tmp_path),
        capture_output=True, text=True, timeout=240,
    )
    summaries = sorted((home / "logs").glob("v10_organ_harmony_*.summary.json"))
    assert summaries, f"no summary written\nstdout:\n{proc.stdout[-2000:]}\nstderr:\n{proc.stderr[-2000:]}"
    organs = json.loads(summaries[-1].read_text())["organs"]
    for organ in ("brain", "memory"):
        assert organs[organ]["verdict"] == "green", (
            f"{organ} is {organs[organ]['verdict']}: {organs[organ]['detail']} "
            "(did call_llm's signature change? update _make_mock in stress/)"
        )
    # Deliberately NOT asserting the overall exit code: it also reflects "heart"
    # (latency spread), which is noise on a shared CI runner with a 6-turn smoke
    # run. This test exists to catch harness/API rot (the 2026-05-20 session_id
    # mock break), and that shows up as brain/memory going red — checked above.
    # A crash (no summary written) is still caught by the assert on `summaries`.
    assert proc.returncode in (0, 1), proc.stdout[-2000:]
