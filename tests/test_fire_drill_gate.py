"""scripts/fire-drill.sh safety (Boss 10-21): a fresh encrypted backup before anything is stopped, an
auto-stop on the first agent DB line, and one BOARD line. Fake uv/systemctl/journalctl/probe on PATH:
nothing real is touched."""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fire-drill.sh"


def _stub(d: Path, name: str, body: str) -> None:
    p = d / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)


@pytest.fixture
def drill(tmp_path):
    b = tmp_path / "bin"
    b.mkdir()
    calls, state, journal = tmp_path / "calls.log", tmp_path / "matrix.state", tmp_path / "journal.txt"
    journal.write_text("")
    _stub(b, "uv", f"""py=$(cat); echo "uv $*" >> {calls}
if grep -q cloud_backup <<<"$py"; then echo "uv backup" >> {calls}
  case "${{FAKE_BACKUP:-taken}}" in fresh) echo BACKUP_FRESH age=5s; exit 0;; taken) echo BACKUP_TAKEN windyfly-x.enc; exit 0;; *) echo BACKUP_FAILED no key; exit 1;; esac
fi
echo "uv step" >> {calls}; exit 0
""")
    _stub(b, "systemctl", f"""echo "systemctl $*" >> {calls}
case "$*" in
  *list-jobs*) exit 0;;
  *stop*windy-0@matrix*) echo inactive > {state}; [ -n "$FAKE_DB_ON_STOP" ] && echo "windy-job: sqlite3.DatabaseError: file is not a database" >> {journal}; exit 0;;
  *is-active*windy-0@matrix*) cat {state} 2>/dev/null || echo active; exit 0;;
  *start*) echo active > {state}; exit 0;;
esac; exit 0
""")
    _stub(b, "journalctl", f"cat {journal}\n")
    _stub(b, "logger", f'echo "logger $*" >> {calls}\n')
    _stub(b, "curl", "exit 0\n")
    probe = tmp_path / "probe.sh"
    probe.write_text(f"#!/usr/bin/env bash\necho active > {state}\n")
    probe.chmod(0o755)
    board = tmp_path / "BOARD.md"
    board.write_text("")
    status = tmp_path / "fire-drill.status"
    env = {"PATH": f"{b}:{os.environ['PATH']}", "HOME": str(tmp_path), "WINDY_ENV_FILE": str(tmp_path / "none.env"),
           "WINDY_FIRE_DRILL_STATUS": str(status), "WINDY_AGENT_DIR": str(tmp_path), "WINDY_PROBE_BIN": str(probe),
           "WINDY_DRILL_BOARD_FILE": str(board), "WINDYFLY_CONFIG": str(tmp_path / "config.toml")}

    def run(**extra):
        r = subprocess.run(["bash", str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True, timeout=120)
        st = dict(ln.split("=", 1) for ln in status.read_text().splitlines() if "=" in ln)
        return r, st, calls.read_text(), board.read_text()

    return run, journal


def test_no_fresh_backup_fails_before_anything_is_stopped(drill):
    run, _ = drill
    r, st, calls, board = run(FAKE_BACKUP="fail")
    assert r.returncode == 1 and st["result"] == "FAIL" and st["step_backup"] == "FAIL"
    assert st["step_probe_heal"] == "NOT_RUN" and "no fresh encrypted backup" in st["detail"]
    assert "systemctl --user stop" not in calls  # nothing was stopped
    assert "result=FAIL" in board and board.count("\n") == 1


def test_a_db_line_stops_the_drill_after_the_step_that_caused_it(drill):
    run, journal = drill
    r, st, calls, board = run(FAKE_BACKUP="fresh", FAKE_DB_ON_STOP="1")  # the line appears when matrix stops
    assert r.returncode == 1 and st["result"] == "FAIL" and st["detail"].startswith("AUTO-STOP")
    assert st["step_probe_heal"] in ("PASS", "FAIL") and st["step_lifeboat"] == "NOT_RUN"
    assert calls.count("uv step") == 0  # lifeboat / engine_swap / turnover never ran
    assert "AUTO-STOP" in board


def test_a_clean_drill_takes_a_backup_passes_and_posts_once(drill):
    run, _ = drill
    r, st, calls, board = run(FAKE_BACKUP="taken")
    assert r.returncode == 0, r.stdout + r.stderr
    assert st["result"] == "PASS" and all(st[f"step_{k}"] == "PASS"
                                          for k in ("backup", "probe_heal", "lifeboat", "engine_swap", "turnover"))
    assert calls.index("uv backup") < calls.index("systemctl --user stop windy-0@matrix")  # backup first
    assert board.count("\n") == 1 and "result=PASS" in board


def test_without_a_board_file_nothing_is_posted(drill, tmp_path):
    run, _ = drill
    r, st, _calls, board = run(FAKE_BACKUP="fresh", WINDY_DRILL_BOARD_FILE="")
    assert st["result"] == "PASS" and board == ""


def test_a_db_line_during_the_backup_stops_before_anything_is_stopped(drill):
    run, journal = drill
    journal.write_text("windy-job: sqlite3.DatabaseError: database is locked\n")
    r, st, calls, _board = run(FAKE_BACKUP="taken")
    assert st["result"] == "FAIL" and st["step_backup"] == "PASS" and st["step_probe_heal"] == "NOT_RUN"
    assert "systemctl --user stop" not in calls
