"""Weekly continuity battery — Principle #7 as a NUMBER (2026-07-18).

Measures cross-reset recall on the PROD install with a scratch DB:
  phase A (this process): seed 8 facts, then 20 filler turns to push
          the early facts out of the recent-episode window (stresses
          the anti-amnesia hybrid retrieval, not just the window);
          probe all 8 in-session.
  phase B (fresh process — run the script twice; state via scratch
          dir): probe all 8 after a simulated crash, then /new-style
          reset (turnover letter + new session) and probe the
          mid-flight task thread.

Score = recalled/total, emitted as a content-free continuity.score
event (metadata: score_pct + phase counts). Runs on the LOCAL free
model (llama3.2:3b) — zero marginal cost, and what's under test is
the memory SUBSTRATE, not the model.

Invoked by scripts/fire-drill's sibling timer (Sun 08:20) via:
  uv run python scripts/continuity_battery.py seed
  uv run python scripts/continuity_battery.py probe
"""
from __future__ import annotations

import json
import os
import sys
import time

SCRATCH = os.environ.get(
    "WINDY_BATTERY_SCRATCH", "/tmp/windy-continuity-battery"
)
os.makedirs(SCRATCH, exist_ok=True)
os.environ["WINDYFLY_DB_PATH"] = f"{SCRATCH}/battery.db"
os.environ.setdefault("WINDY_STATE_DIR", f"{SCRATCH}/state")
os.makedirs(os.environ["WINDY_STATE_DIR"], exist_ok=True)

FACTS = {
    "dog": ("My dog is named Biscuit.", "biscuit"),
    "birthday": ("My grandson Tommy's birthday is August 3rd.", "august 3"),
    "town": ("I live in Sheridan, Wyoming.", "sheridan"),
    "doctor": ("My doctor is Dr. Patel.", "patel"),
    "hobby": ("I'm knitting a blue baby blanket.", "blanket"),
    "car": ("I drive a green Subaru Outback.", "subaru"),
    "church": ("My church is First Baptist.", "first baptist"),
    "pie": ("My favorite pie is rhubarb.", "rhubarb"),
}
PROBES = {
    "dog": "What's my dog's name?",
    "birthday": "When is Tommy's birthday?",
    "town": "What town do I live in?",
    "doctor": "Who is my doctor?",
    "hobby": "What am I knitting?",
    "car": "What car do I drive?",
    "church": "Which church do I go to?",
    "pie": "What's my favorite pie?",
}
SESSION = "battery:cont:v1"


def boot():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/src")
    from windyfly.config import load_config
    from windyfly.memory.database import Database
    from windyfly.memory.write_queue import WriteQueue
    cfg_path = os.environ.get(
        "WINDYFLY_CONFIG",
        os.path.expanduser("~/.local/share/windyfly/soul/config.toml"),
    )
    cfg = load_config(cfg_path)
    cfg.setdefault("memory", {})["db_path"] = os.environ["WINDYFLY_DB_PATH"]
    cfg.setdefault("agent", {})["default_model"] = os.environ.get(
        "WINDY_BATTERY_MODEL", "llama3.2:3b"
    )
    db = Database(os.environ["WINDYFLY_DB_PATH"])
    wq = WriteQueue()
    wq.start()
    return cfg, db, wq


def turn(cfg, db, wq, msg):
    from windyfly.agent.loop import agent_respond
    try:
        return agent_respond(cfg, db, wq, msg, SESSION)
    except Exception as e:  # noqa: BLE001
        return f"<<EXC {e}>>"


def probe_all(cfg, db, wq, label):
    hits = {}
    for key, q in PROBES.items():
        out = turn(cfg, db, wq, q)
        hits[key] = FACTS[key][1] in out.lower()
    n = sum(hits.values())
    missed = [k for k, v in hits.items() if not v]
    print(f"[battery] {label}: {n}/{len(PROBES)} recalled"
          + (f" (missed: {','.join(missed)})" if missed else ""))
    return n


def emit(score_pct, detail):
    url = os.environ.get("WINDY_ADMIN_INGEST_URL")
    tok = os.environ.get("WINDY_ADMIN_INGEST_TOKEN")
    if not url or not tok:
        return
    import httpx
    try:
        httpx.post(
            f"{url.rstrip('/')}/v1/events",
            json={"events": [{
                "ts": __import__("datetime").datetime.now(
                    __import__("datetime").UTC).isoformat(),
                "platform": "windy-agent", "service": "fly",
                "event_type": "continuity.score", "actor_type": "agent",
                "actor_id": os.environ.get("ETERNITAS_PASSPORT", "unknown"),
                "metadata": {"score_pct": score_pct, **detail},
            }]},
            headers={"Authorization": f"Bearer {tok}"}, timeout=3.0,
        )
    except Exception:
        pass


def phase_seed():
    cfg, db, wq = boot()
    turn(cfg, db, wq, "hello")
    time.sleep(2.5)  # absorb first-contact welcome
    for key in FACTS:
        turn(cfg, db, wq, FACTS[key][0])
    # Mid-flight task thread (probed after reset in phase B)
    turn(cfg, db, wq, "Help me plan the quilt raffle. Tickets are $5. Remember that.")
    # 20 filler turns push the seeds out of the recent window
    for i in range(20):
        turn(cfg, db, wq, f"Filler question {i}: say OK and nothing else.")
    n = probe_all(cfg, db, wq, "in-session-post-eviction")
    json.dump({"in_session": n}, open(f"{SCRATCH}/phaseA.json", "w"))
    time.sleep(2)


def phase_probe():
    cfg, db, wq = boot()
    n_crash = probe_all(cfg, db, wq, "post-crash-resume")
    # /new-style reset
    from windyfly.agent.turnover import write_turnover_letter
    from windyfly.agent.session_reset import reset_session
    global SESSION
    write_turnover_letter(db, None, platform="battery", channel_id="cont",
                          session_id=SESSION)
    SESSION = reset_session("battery", "cont")
    out = turn(cfg, db, wq, "Where did we leave off with the raffle?")
    task_ok = "raffle" in out.lower() or "$5" in out or "quilt" in out.lower()
    print(f"[battery] post-reset-task: {'PASS' if task_ok else 'FAIL'}")
    a = json.load(open(f"{SCRATCH}/phaseA.json"))
    total = len(PROBES) * 2 + 1
    got = a["in_session"] + n_crash + (1 if task_ok else 0)
    score = round(100 * got / total)
    print(f"[battery] CONTINUITY SCORE: {score}% ({got}/{total})")
    emit(score, {"in_session": a["in_session"], "post_crash": n_crash,
                 "post_reset_task": int(task_ok)})
    time.sleep(2)
    sys.exit(0 if score >= 70 else 1)


if __name__ == "__main__":
    {"seed": phase_seed, "probe": phase_probe}[sys.argv[1]]()
