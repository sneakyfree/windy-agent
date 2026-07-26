"""Chaos harness — kill the agent and see whether it gets back up.

The marathon (``run.py``) proves the agent THINKS well over a long
conversation. It says nothing about whether it SURVIVES being attacked,
and "honey badger" is a survival claim, not a reasoning one.

Everything here is deliberately destructive, so everything here runs
against scratch state: its own run directory, its own SQLite file, its
own HOME. It never touches a live agent.

**Why kill -9 specifically.** A graceful shutdown is the easy case and
already has a code path. The interesting question is the ungraceful one:
power cut, OOM killer, laptop lid closed — the agent dies *mid-write*,
with a half-finished SQLite transaction and a write queue holding
episodes it never flushed. That is the moment memory corrupts if it is
going to, and it is exactly the moment nobody tests.

What each round asserts after the kill:

  1. **The database is not corrupt** — ``PRAGMA integrity_check``. A
     corrupt memory file is unrecoverable; grandma loses everything.
  2. **Episodes did not go backwards** — the count after a kill must be
     >= the count before. Losing the last turn to an ungraceful kill is
     forgivable; losing older turns is memory rot.
  3. **It restarts at all** — no wedged lock file, no poisoned marker
     that makes the next boot fail. A crash you cannot restart from is
     the OpenClaw death spiral.
  4. **It picks up where it left off** — the resumed process continues
     the SAME session, and a fact established before the kill is still
     recalled after it.

Run:
    python scripts/marathon/chaos.py --rounds 8 --turns-per-round 20
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def _integrity(db_path: Path) -> str:
    """SQLite's own verdict on the file. 'ok' or a description of rot."""
    if not db_path.exists():
        return "missing"
    try:
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute("PRAGMA integrity_check").fetchone()
            return (row[0] if row else "no-result")
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        return f"unreadable: {type(e).__name__}: {e}"


def _episode_count(db_path: Path) -> int:
    if not db_path.exists():
        return 0
    try:
        con = sqlite3.connect(str(db_path))
        try:
            return int(con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0])
        except Exception:
            return -1
        finally:
            con.close()
    except Exception:
        return -1


def _turns_done(run_dir: Path) -> int:
    f = run_dir / "findings.jsonl"
    if not f.exists():
        return 0
    return sum(1 for _ in f.open(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--turns-per-round", type=int, default=20)
    ap.add_argument("--total-turns", type=int, default=200)
    ap.add_argument("--out", default="")
    # Kill timing has to land DURING conversation, not during boot.
    # First pass at this got 0 turns in all 8 rounds: cold start loads
    # the embedding model (~5s) while a --stub turn costs ~10ms, so
    # every SIGKILL hit a process that had not yet said anything. That
    # tests "killed while booting" — a real case, and it passed — but
    # not the case that matters, which is dying mid-write with a
    # half-flushed write queue.
    #
    # So: pace the turns (--turn-sleep) to widen the window a real
    # conversation would have anyway, and start killing only after
    # boot is comfortably done.
    ap.add_argument("--min-kill-delay", type=float, default=9.0)
    ap.add_argument("--max-kill-delay", type=float, default=22.0)
    ap.add_argument("--turn-sleep", type=float, default=0.15,
                    help="Seconds between turns in the child run, so kills "
                         "land mid-conversation instead of mid-boot.")
    args = ap.parse_args()

    base = Path(args.out) if args.out else (
        Path(os.environ.get("TMPDIR", "/tmp")) / "windy-chaos"
    )
    run_dir = base / f"chaos_{time.strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)
    db_path = run_dir / "marathon.db"

    print(f"[chaos] run_dir={run_dir}")
    print(f"[chaos] {args.rounds} kill rounds, SIGKILL mid-turn, scratch state only\n")

    results: list[dict] = []
    prev_episodes = 0

    for rnd in range(1, args.rounds + 1):
        before_turns = _turns_done(run_dir)
        cmd = [
            sys.executable, str(HERE / "run.py"),
            "--turns", str(args.total_turns),
            "--stub",                      # substrate test; no model spend
            "--sleep", str(args.turn_sleep),
            "--out", str(base),
            "--resume", str(run_dir),
        ]
        proc = subprocess.Popen(
            cmd, cwd=str(REPO),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True,        # kill the whole group, not just the shell
        )

        delay = random.uniform(args.min_kill_delay, args.max_kill_delay)
        time.sleep(delay)

        killed = False
        if proc.poll() is None:
            # SIGKILL the process GROUP: the write-queue thread lives in
            # the same process, and we want it dead mid-flush, not given
            # a chance to drain. That is the whole point.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                killed = True
            except ProcessLookupError:
                pass
        proc.wait(timeout=30)
        time.sleep(0.3)  # let the OS reap

        integrity = _integrity(db_path)
        episodes = _episode_count(db_path)
        after_turns = _turns_done(run_dir)

        row = {
            "round": rnd,
            "killed_after_s": round(delay, 2),
            "killed": killed,
            "exit_code": proc.returncode,
            "turns_before": before_turns,
            "turns_after": after_turns,
            "episodes": episodes,
            "episodes_prev": prev_episodes,
            "integrity": integrity,
            "db_ok": integrity == "ok",
            "no_memory_rot": episodes >= prev_episodes,
            # "No progress" is only a failure if there was work left.
            # The last round often finds the run already complete.
            "made_progress": (after_turns > before_turns
                              or after_turns >= args.total_turns),
        }
        results.append(row)

        flag = "OK " if (row["db_ok"] and row["no_memory_rot"]) else "FAIL"
        print(
            f"  [{flag}] round {rnd:>2}  killed@{delay:>4.1f}s  "
            f"turns {before_turns:>3}->{after_turns:<3}  "
            f"episodes {prev_episodes:>4}->{episodes:<4}  "
            f"integrity={integrity}"
        )
        prev_episodes = max(prev_episodes, episodes)

    # --- Final: let it run clean, then check it still remembers -------
    print("\n[chaos] final uninterrupted run to completion...")
    subprocess.run(
        [sys.executable, str(HERE / "run.py"),
         "--turns", str(args.total_turns), "--stub",
         "--sleep", str(args.turn_sleep),
         "--out", str(base), "--resume", str(run_dir)],
        cwd=str(REPO), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=1800,
    )

    rows = [json.loads(x) for x in (run_dir / "findings.jsonl").open(encoding="utf-8")]
    probes = [r for r in rows if r.get("kind") == "probe"]
    recovered = [p for p in probes if p.get("in_payload")]

    print(f"\n{'='*66}")
    print("CHAOS REPORT")
    print(f"{'='*66}")
    corrupt = [r for r in results if not r["db_ok"]]
    rot = [r for r in results if not r["no_memory_rot"]]
    stuck = [r for r in results if not r["made_progress"]]
    print(f"kill rounds:            {len(results)}")
    print(f"  database corrupted:   {len(corrupt)}   <-- must be 0")
    print(f"  memory went backwards:{len(rot)}   <-- must be 0")
    print(f"  failed to make progress after restart: {len(stuck)}")
    print(f"\nafter all that, across {len(probes)} recall probes:")
    if probes:
        print(f"  fact still reached the prompt: {len(recovered)}/{len(probes)} "
              f"({len(recovered)/len(probes)*100:.1f}%)")
    print(f"\ntotal turns survived: {_turns_done(run_dir)}")
    print(f"final integrity: {_integrity(db_path)}")
    print(f"final episodes:  {_episode_count(db_path)}")
    print(f"{'='*66}\n")

    (run_dir / "chaos_results.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")

    return 1 if (corrupt or rot) else 0


if __name__ == "__main__":
    raise SystemExit(main())
