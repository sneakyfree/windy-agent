"""scripts/windy-qa-battery.sh runs ONLY as the lab agent (11-04: its 11-01 run minted AS Zero with Zero's key and
spent Grant's own provider keys). The script is run here for real, with a fake python that records what it saw."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "windy-qa-battery.sh"

FAKE_PY = """#!/usr/bin/env bash
# Records each call; '-m windyfly.lab_guard' answers with $GUARD_RC.
{ printf 'ARGS %s\\n' "$*"
  for v in HOME WINDY_STATE_DIR WINDY_ENV_FILE WINDY_CREDENTIALS_FILE WINDY_SYNTHETIC ANTHROPIC_API_KEY \\
           OPENAI_API_KEY GEMINI_API_KEY WINDY_LAB_GUARD_REAL_STATE; do printf '%s=%s\\n' "$v" "${!v-<unset>}"; done
} >> "$CALLS"
if [[ "$1" == "-m" && "$2" == "windyfly.lab_guard" ]]; then
  if [[ "$GUARD_RC" != 0 ]]; then echo "lab guard: REFUSED: the agent key file is the real agent's" >&2; fi
  exit "$GUARD_RC"
fi
exit 0
"""


def _exe(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


@pytest.fixture
def box(tmp_path):
    real = tmp_path / "grant"
    lab = real / ".local" / "share" / "zero-lab"
    (lab / ".windy").mkdir(parents=True)
    (lab / ".windy" / "agent.env").write_text("ETERNITAS_PASSPORT=ET26-TEST-M0NF\n", encoding="utf-8")
    (lab / "agent").mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _exe(bin_dir / "logger", "#!/usr/bin/env bash\necho \"$*\" >> \"$LOGS\"\n")
    _exe(tmp_path / "py", FAKE_PY)
    env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "HOME": str(real), "CALLS": str(tmp_path / "calls"),
           "LOGS": str(tmp_path / "logs"), "WINDY_VENV_PYTHON": str(tmp_path / "py"), "SKIP_V14": "1",
           "ANTHROPIC_API_KEY": "sk-grant-own", "OPENAI_API_KEY": "sk-grant-own", "GEMINI_API_KEY": "g-own"}
    (real / ".windy-stress").mkdir(parents=True)
    return {"env": env, "tmp": tmp_path, "lab": lab, "real": real}


def _run(box, guard_rc: int) -> subprocess.CompletedProcess:
    env = dict(box["env"], GUARD_RC=str(guard_rc))
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def test_a_guard_refusal_stops_the_run_before_any_harness(box):
    out = _run(box, 2)
    assert out.returncode == 2
    calls = (box["tmp"] / "calls").read_text()
    assert calls.count("ARGS ") == 1 and "ARGS -m windyfly.lab_guard --mint" in calls   # the guard, nothing else
    assert "lab guard refused" in (box["tmp"] / "logs").read_text()


def test_the_run_lives_in_the_lab_home_with_no_persons_keys(box):
    out = _run(box, 0)
    assert out.returncode == 0, out.stderr
    calls = (box["tmp"] / "calls").read_text()
    lab = str(box["lab"])
    assert f"HOME={lab}\n" in calls and f"WINDY_STATE_DIR={lab}/.windy\n" in calls
    assert f"WINDY_ENV_FILE={lab}/.windy/agent.env\n" in calls
    assert f"WINDY_CREDENTIALS_FILE={lab}/.windy/credentials.json\n" in calls
    assert f"WINDY_LAB_GUARD_REAL_STATE={box['real']}/.windy\n" in calls
    for key in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY"):
        assert f"{key}=<unset>\n" in calls and "sk-grant-own" not in calls
    assert "WINDY_SYNTHETIC=1\n" in calls
    assert "stress_v13_qa_battery.py" in calls                          # the harness ran only after the guard


def test_no_lab_env_file_refuses(box):
    (box["lab"] / ".windy" / "agent.env").unlink()
    out = _run(box, 0)
    assert out.returncode == 2 and not (box["tmp"] / "calls").exists()
