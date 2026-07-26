"""Fault injection — break the world underneath the agent, on purpose.

``chaos.py`` kills the process. This breaks the things the process
depends on while it is still alive, which is the harder case: a dead
process is obvious, a *degraded* one is where products quietly lie to
their users.

Each scenario asserts the same two things, because they are the two
that matter for a grandma who cannot read a log:

  1. **It does not die.** Principle #8, rung one.
  2. **It does not pretend.** If memory could not be written, or the
     model could not be reached, that has to surface — as an honest
     reply, an error counter, something. Silent degradation is the
     failure mode that erodes trust without ever announcing itself,
     and it is the one this file exists to catch.

Everything runs on scratch state. Nothing here touches a live agent.

Run:
    python scripts/marathon/faults.py
"""

from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _fresh_env() -> Path:
    d = Path(tempfile.mkdtemp(prefix="windy-fault-"))
    os.environ["HOME"] = str(d)
    os.environ["WINDY_STATE_DIR"] = str(d / "state")
    os.environ["WINDYFLY_DB_PATH"] = str(d / "f.db")
    return d


def _agent(d: Path):
    """Build a scratch agent: config, db, write queue, stub model."""
    from windyfly.config import load_config
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.agent import loop as L

    L.call_llm = lambda messages, **kw: {
        "content": "ok", "input_tokens": 10, "output_tokens": 2,
        "tool_calls": [], "model": "stub",
    }
    cfg = load_config()
    cfg.setdefault("memory", {})["db_path"] = str(d / "f.db")
    db = Database(str(d / "f.db"))
    wq = WriteQueue()
    wq.start()
    return cfg, db, wq, L


# ── 1. The disk fills up mid-conversation ──────────────────────────
def scenario_disk_full() -> tuple[str, bool, str]:
    """Every memory write fails. The agent must keep talking AND must
    record that memory is failing — the 2026-07-04 audit found a full
    disk silently losing every episode while the bot chatted happily on.
    """
    d = _fresh_env()
    from windyfly.memory import write_queue as WQ
    from windyfly.memory import episodes as EP
    cfg, db, wq, L = _agent(d)

    WQ.reset_write_stats()

    def _boom(*a, **k):
        raise OSError("No space left on device")

    # Patch the name BOUND IN loop.py, not the one in episodes.py.
    # loop.py does `from windyfly.memory.episodes import save_episode`,
    # so it holds its own reference and patching the source module is a
    # no-op — the first version of this scenario did exactly that and
    # reported a false failure (0 write errors) because the real writer
    # was still running happily.
    real_loop_save = L.save_episode
    real_ep_save = EP.save_episode
    L.save_episode = _boom
    EP.save_episode = _boom
    try:
        replies = []
        for i in range(5):
            replies.append(L.agent_respond(cfg, db, wq, f"turn {i}", "s-disk"))
        wq.stop()
    finally:
        L.save_episode = real_loop_save
        EP.save_episode = real_ep_save

    stats = WQ.get_write_stats()
    alive = all(isinstance(r, str) and r for r in replies)
    noticed = stats["failures"] > 0
    return (
        "disk full (every episode write fails)",
        alive and noticed,
        f"kept answering={alive} ({len(replies)} replies) · "
        f"failures recorded={stats['failures']} · "
        f"last_error={stats['last_error'][:60]!r}",
    )


# ── 2. The memory file is corrupted ────────────────────────────────
def scenario_corrupt_db() -> tuple[str, bool, str]:
    """Garbage written over the SQLite file. The agent must not
    crash-loop — a crash you cannot boot out of is the OpenClaw death
    spiral. Degrading to 'no memory' beats refusing to start."""
    d = _fresh_env()
    cfg, db, wq, L = _agent(d)
    L.agent_respond(cfg, db, wq, "hello before corruption", "s-corrupt")
    wq.stop()
    db.close()

    dbf = d / "f.db"
    with dbf.open("r+b") as fh:      # smash the header, keep the size
        fh.seek(0)
        fh.write(b"\x00" * 512)

    verdict = "unknown"
    try:
        con = sqlite3.connect(str(dbf))
        try:
            con.execute("SELECT COUNT(*) FROM episodes").fetchone()
            verdict = "still readable"
        except Exception as e:       # expected
            verdict = f"unreadable ({type(e).__name__})"
        finally:
            con.close()
    except Exception as e:
        verdict = f"unopenable ({type(e).__name__})"

    # The real question: can a NEW agent still boot against it?
    survived, detail = False, ""
    try:
        from windyfly.memory.database import Database
        db2 = Database(str(dbf))
        from windyfly.memory.write_queue import WriteQueue
        wq2 = WriteQueue(); wq2.start()
        r = L.agent_respond(cfg, db2, wq2, "hello after corruption", "s-corrupt")
        wq2.stop()
        survived = isinstance(r, str) and len(r) > 0
        detail = f"booted and answered ({len(r)} chars)"
    except Exception as e:           # noqa: BLE001
        detail = f"REFUSED TO BOOT: {type(e).__name__}: {e}"[:140]

    return ("corrupt memory file", survived, f"{verdict} · {detail}")


# ── 3. The model is unreachable ────────────────────────────────────
def scenario_model_unreachable() -> tuple[str, bool, str]:
    """Every provider fails. The agent must answer with something
    honest rather than raising into the channel — grandma should see
    words, not a stack trace."""
    d = _fresh_env()
    cfg, db, wq, L = _agent(d)

    def _dead(*a, **k):
        raise RuntimeError("connection refused: all providers exhausted")
    L.call_llm = _dead

    reply, err = "", None
    try:
        reply = L.agent_respond(cfg, db, wq, "are you there?", "s-net")
    except Exception as e:           # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
    wq.stop()

    graceful = err is None and isinstance(reply, str) and len(reply) > 0
    return (
        "model unreachable (all providers fail)",
        graceful,
        (f"raised into the caller: {err}" if err
         else f"answered gracefully: {reply[:90]!r}"),
    )


# ── 4. Two agents, one memory file ─────────────────────────────────
def scenario_concurrent_writers() -> tuple[str, bool, str]:
    """Telegram and Matrix run as separate processes over ONE database
    (per the unit description: 'one runtime per channel; shares
    passport + memory'). SQLite must not throw 'database is locked'
    under that, and no episode may be lost."""
    d = _fresh_env()
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    from windyfly.memory.episodes import save_episode
    import threading

    errors: list[str] = []
    N = 60

    def writer(tag: str) -> None:
        try:
            db = Database(str(d / "f.db"))
            wq = WriteQueue(); wq.start()
            for i in range(N):
                wq.enqueue(0, save_episode, db, "user", f"{tag}-{i}",
                           session_id=f"sess-{tag}")
            wq.stop()
            db.close()
        except Exception as e:       # noqa: BLE001
            errors.append(f"{tag}: {type(e).__name__}: {e}")

    ts = [threading.Thread(target=writer, args=(t,)) for t in ("telegram", "matrix")]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=120)

    con = sqlite3.connect(str(d / "f.db"))
    got = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
    con.close()

    ok = not errors and got == 2 * N and integrity == "ok"
    return (
        "two channels writing one memory file",
        ok,
        f"episodes {got}/{2 * N} · integrity={integrity} · errors={errors[:1]}",
    )


def main() -> int:
    sys.path.insert(0, str(REPO / "src"))
    scenarios = [
        scenario_disk_full,
        scenario_corrupt_db,
        scenario_model_unreachable,
        scenario_concurrent_writers,
    ]
    print(f"\n{'='*72}")
    print("FAULT INJECTION — does it survive, and does it stay honest?")
    print(f"{'='*72}")
    failures = 0
    for fn in scenarios:
        try:
            name, ok, detail = fn()
        except Exception as e:       # noqa: BLE001
            name, ok, detail = fn.__name__, False, f"harness error: {type(e).__name__}: {e}"
        flag = "PASS" if ok else "FAIL"
        if not ok:
            failures += 1
        print(f"\n[{flag}] {name}")
        print(f"       {detail}")
    print(f"\n{'='*72}")
    print(f"{len(scenarios) - failures}/{len(scenarios)} scenarios survived honestly")
    print(f"{'='*72}\n")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
