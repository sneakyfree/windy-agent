"""scripts/probe-telegram-liveness.sh with WINDY_LIVENESS_SKIP_TELEGRAM=1 (the Telegram channel off on purpose).

This edit ran on Windy 0 for weeks without being in the repo; reinstalling from the repo would have dropped it.
Stubs on PATH stand in for systemctl, curl and logger, so nothing real is touched.
"""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "probe-telegram-liveness.sh"


def _stub(bin_dir: Path, name: str, body: str) -> None:
    p = bin_dir / name
    p.write_text("#!/usr/bin/env bash\n" + body)
    p.chmod(0o755)


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.log"
    state = tmp_path / "dead"  # units listed here report inactive until restarted
    _stub(bin_dir, "systemctl", f"""echo "systemctl $*" >> {calls}
args="$*"; unit="${{@: -1}}"
if [[ "$args" == *is-active* ]]; then grep -qx "$unit" {state} 2>/dev/null && echo inactive || echo active; exit 0; fi
if [[ "$args" == *restart* ]]; then sed -i "/^$unit$/d" {state} 2>/dev/null; exit 0; fi
exit 0
""")
    _stub(bin_dir, "curl", f'echo "curl $*" >> {calls}; echo 000; exit 7\n')
    _stub(bin_dir, "logger", f'echo "logger $*" >> {calls}\n')
    envfile = tmp_path / "agent.env"
    envfile.write_text("SOMETHING_ELSE=1\n")  # no TELEGRAM_BOT_TOKEN
    status = tmp_path / "liveness.status"
    e = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "WINDY_ENV_FILE": str(envfile),
         "WINDY_LIVENESS_STATUS_FILE": str(status), "WINDY_LIVENESS_SKIP_TELEGRAM": "1",
         "WINDY_AGENT_UNITS": "a.service b.service", "WINDY_AGENT_SCOPE": "user",
         "WINDY_AGENT_LOG": str(tmp_path / "missing.log"), "WINDY_LIVENESS_FAIL_LIMIT": "2"}
    return e, calls, status, state


def _run(e):
    return subprocess.run(["bash", str(SCRIPT)], env=e, capture_output=True, text=True, timeout=60)


def test_skip_telegram_checks_units_only_and_never_calls_telegram(env):
    e, calls, status, _state = env
    r = _run(e)
    assert r.returncode == 0, r.stderr
    text = status.read_text()
    assert "OK" in text and "telegram checks skipped" in text
    assert "curl" not in calls.read_text()  # no getMe without a token


def test_without_the_flag_a_missing_token_is_still_fatal(env):
    e, calls, _status, _state = env
    r = _run({**e, "WINDY_LIVENESS_SKIP_TELEGRAM": "0"})
    assert r.returncode != 0 and "TELEGRAM_BOT_TOKEN not set" in r.stderr


def test_skip_telegram_still_heals_a_dead_unit(env):
    e, calls, status, state = env
    state.write_text("b.service\n")
    _run(e)                       # 1st failure: below the limit, no restart
    assert "restart" not in calls.read_text()
    _run(e)                       # 2nd failure reaches WINDY_LIVENESS_FAIL_LIMIT=2: restart b only
    log = calls.read_text()
    assert "restart b.service" in log and "restart a.service" not in log
    assert "curl" not in log
