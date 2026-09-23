"""scripts/windy-retry-on-429.sh — weekly health jobs vs Anthropic 429s.

A fake job replays a scripted sequence of outcomes (one per attempt), so
the wrapper's retry / skip / fail-loudly rules are exercised without
running the real fire drill or continuity battery.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

WRAPPER = Path(__file__).resolve().parents[1] / "scripts" / "windy-retry-on-429.sh"

OUT_429 = ("Provider anthropic (claude-opus-5) failed: Error code: 429 - "
           "{'type': 'error', 'error': {'type': 'rate_limit_error'}}")
OUT_401 = ("Provider anthropic (claude-opus-5) failed: Error code: 401 - "
           "{'type': 'error', 'error': {'type': 'authentication_error'}}")


def _fake_job(tmp_path: Path, outcomes: list[str]) -> Path:
    """outcomes: per attempt, '429' | '401' | 'ok' | 'fail' (plain failure)."""
    seq = tmp_path / "seq"
    seq.write_text("\n".join(outcomes) + "\n")
    count = tmp_path / "count"
    count.write_text("0")
    job = tmp_path / "job.sh"
    job.write_text(f"""#!/usr/bin/env bash
n=$(( $(cat {count}) + 1 )); echo $n > {count}
o=$(sed -n "${{n}}p" {seq})
case "$o" in
  ok)   echo "[fire-drill] engine_swap: PASS"; exit 0 ;;
  429)  echo "{OUT_429}"; echo "[fire-drill] engine_swap: FAIL"; exit 1 ;;
  401)  echo "{OUT_401}"; echo "[fire-drill] engine_swap: FAIL"; exit 1 ;;
  *)    echo "[fire-drill] turnover: FAIL (disk full)"; exit 3 ;;
esac
""")
    job.chmod(0o755)
    return job


def _run(tmp_path: Path, outcomes: list[str], status: Path | None = None):
    job = _fake_job(tmp_path, outcomes)
    args = ["bash", str(WRAPPER), "windy-test-job"]
    if status:
        args += ["--status-file", str(status)]
    args += ["--", str(job)]
    env = {**os.environ, "WINDY_429_BACKOFFS": "0 0"}
    p = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
    attempts = int((tmp_path / "count").read_text())
    return p, attempts


def test_success_first_try_no_retry(tmp_path):
    p, attempts = _run(tmp_path, ["ok"])
    assert p.returncode == 0 and attempts == 1


def test_429_twice_then_ok_retries_and_passes(tmp_path):
    p, attempts = _run(tmp_path, ["429", "429", "ok"])
    assert p.returncode == 0
    assert attempts == 3
    assert "passed on attempt 3" in p.stdout
    assert "RATE-LIMITED" not in p.stdout


def test_429_every_time_is_skipped_honestly(tmp_path):
    status = tmp_path / "fire-drill.status"
    status.write_text("ts=x\nresult=FAIL\nstep_probe_heal=PASS\nstep_engine_swap=FAIL\n")
    p, attempts = _run(tmp_path, ["429", "429", "429"], status=status)
    assert p.returncode == 0
    assert attempts == 3
    assert "RATE-LIMITED: skipped (Anthropic 429 x3), not a regression" in p.stdout
    s = status.read_text()
    assert "result=RATE_LIMITED" in s
    assert "detail=RATE-LIMITED: skipped (Anthropic 429 x3), not a regression" in s
    assert "step_engine_swap=FAIL" in s  # per-step detail kept for the digest


def test_status_file_created_when_missing(tmp_path):
    status = tmp_path / "sub" / "continuity-battery.status"
    p, _ = _run(tmp_path, ["429", "429", "429"], status=status)
    assert p.returncode == 0
    assert "result=RATE_LIMITED" in status.read_text()


def test_real_failure_is_not_retried_and_keeps_its_exit_code(tmp_path):
    p, attempts = _run(tmp_path, ["fail", "ok"])
    assert p.returncode == 3
    assert attempts == 1
    assert "real failure" in p.stdout


def test_deliberate_401_is_not_treated_as_rate_limit(tmp_path):
    # The fire drill's lifeboat step uses a fake dead key on purpose (401).
    p, attempts = _run(tmp_path, ["401", "ok"])
    assert p.returncode == 1
    assert attempts == 1


def test_429_then_real_failure_pages(tmp_path):
    p, attempts = _run(tmp_path, ["429", "fail"])
    assert p.returncode == 3
    assert attempts == 2


def test_output_is_streamed_through(tmp_path):
    p, _ = _run(tmp_path, ["ok"])
    assert "[fire-drill] engine_swap: PASS" in p.stdout
