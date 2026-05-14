#!/usr/bin/env python3
"""Overnight stress harness — drives the Windy 0 agent through 201 prompts.

Calls agent_respond() directly (bypassing Telegram round-trip — same
code path minus the network hop). Logs every turn to findings.jsonl
with timing, response, tool names invoked, errors, and any
"interesting" events that fired in the bot's events table during
the turn.

Restartable: pass --resume to pick up at the next un-logged prompt.
Cost-capped: halts if total cost exceeds --max-cost ($15 default).
Honors ~/.windy-stress/STOP file (halts gracefully).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

# ─── Bootstrap path + env ─────────────────────────────────────────

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "src"))

# Load bot's env (API keys, DEFAULT_MODEL, GITHUB_PAT, etc.)
_ENV_FILE = Path.home() / ".windy" / "windy-0.env"
if _ENV_FILE.exists():
    for _line in _ENV_FILE.read_text().splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

# Bump native web_search cap for the night (PR #164's WINDY_DAILY_SEARCH_CAP)
os.environ.setdefault("WINDY_DAILY_SEARCH_CAP", "500")

# ─── Logging ──────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("night_stress")

# ─── Lazy imports (after sys.path + env are set) ──────────────────


def _load_bot_module():
    from windyfly.agent.loop import agent_respond
    from windyfly.config import load_config
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.memory.write_queue import WriteQueue
    return agent_respond, load_config, Database, save_episode, WriteQueue


# ─── Helpers ──────────────────────────────────────────────────────

STRESS_DIR = Path.home() / ".windy-stress"
STOP_FILE = STRESS_DIR / "STOP"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def check_stop() -> bool:
    return STOP_FILE.exists()


def cost_so_far(db) -> float:
    try:
        row = db.fetchone(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM cost_log"
        )
        return float((row or {}).get("total", 0))
    except Exception:
        return 0.0


def recent_events(db, since_iso: str) -> list[dict]:
    """Events that fired during the last turn."""
    try:
        rows = db.fetchall(
            "SELECT event_type, data, created_at FROM events "
            "WHERE created_at > ? ORDER BY id DESC LIMIT 50",
            (since_iso,),
        )
        out = []
        for r in rows:
            try:
                data = json.loads(r.get("data") or "{}")
            except Exception:
                data = {}
            out.append({
                "event_type": r["event_type"],
                "data": data,
                "created_at": r["created_at"],
            })
        return out
    except Exception:
        return []


# ─── Main ─────────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True,
                    help="Directory for findings + logs.")
    ap.add_argument("--sleep", type=int, default=60,
                    help="Seconds between prompts (default 60).")
    ap.add_argument("--max-prompts", type=int, default=10_000,
                    help="Soft prompt cap (default = whole corpus).")
    ap.add_argument("--max-cost", type=float, default=15.0,
                    help="Hard USD cap; halt if exceeded.")
    ap.add_argument("--resume", action="store_true",
                    help="Skip prompts already in findings.jsonl.")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    findings_path = run_dir / "findings.jsonl"
    state_path = run_dir / "state.json"

    # Update CURRENT_RUN symlink
    current_link = STRESS_DIR / "CURRENT_RUN"
    if current_link.exists() or current_link.is_symlink():
        current_link.unlink()
    current_link.symlink_to(run_dir)

    # Determine resume index
    completed_indexes: set[int] = set()
    if args.resume and findings_path.exists():
        for line in findings_path.read_text().splitlines():
            try:
                obj = json.loads(line)
                completed_indexes.add(obj["index"])
            except Exception:
                pass
        logger.info("Resuming — %d completed", len(completed_indexes))

    # Bot setup
    agent_respond, load_config, Database, save_episode, WriteQueue = (
        _load_bot_module()
    )

    cfg = load_config(
        "/home/grantwhitmer/.local/share/windyfly/soul/config.toml"
    )
    # Use a dedicated stress DB so we don't pollute prod episodes/nodes.
    stress_db_path = run_dir / "stress.db"
    cfg["memory"]["db_path"] = str(stress_db_path)

    db = Database(str(stress_db_path))
    # One bootstrap episode bypasses the first-contact welcome
    # shortcut so the LLM is actually exercised on prompt #1.
    save_episode(db, "user", "bootstrap stress harness",
                 session_id="bootstrap-stress")

    wq = WriteQueue()
    wq.start()

    # Graceful shutdown
    _shutting_down = {"v": False}

    def _shutdown(signum=None, _frame=None):
        _shutting_down["v"] = True
        logger.warning("Shutdown signal received (%s) — flushing", signum)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Load corpus
    sys.path.insert(0, str(Path(__file__).parent))
    from corpus import get_prompts
    prompts = get_prompts()
    total = min(len(prompts), args.max_prompts)

    state = {
        "run_dir": str(run_dir),
        "started_at": now_iso(),
        "total_prompts": total,
        "completed": len(completed_indexes),
        "model_env": os.environ.get("DEFAULT_MODEL", "(unset)"),
        "search_cap": os.environ.get("WINDY_DAILY_SEARCH_CAP"),
    }
    state_path.write_text(json.dumps(state, indent=2))

    logger.info(
        "Starting run — %d prompts, sleep=%ds, max_cost=$%.2f, model=%s",
        total, args.sleep, args.max_cost, state["model_env"],
    )

    findings_fp = open(findings_path, "a", encoding="utf-8", buffering=1)

    try:
        for i in range(total):
            if check_stop():
                logger.warning("STOP file present — halting at index %d", i)
                break
            if _shutting_down["v"]:
                logger.warning("Shutting down at index %d", i)
                break
            if i in completed_indexes:
                continue

            burn = cost_so_far(db)
            if burn >= args.max_cost:
                logger.error(
                    "Cost cap hit ($%.4f >= $%.2f) — halting",
                    burn, args.max_cost,
                )
                STOP_FILE.write_text(
                    f"cost cap hit at index {i}: ${burn:.4f}\n"
                )
                break

            prompt = prompts[i]
            session_id = f"night-stress-{i:04d}"

            since_marker = now_iso()
            turn_start = time.time()
            response_text = ""
            error_str = None
            tool_names: list[str] = []

            try:
                response_text = agent_respond(
                    cfg, db, wq, prompt["text"], session_id,
                )
            except Exception as e:
                error_str = f"{type(e).__name__}: {e}"
                logger.warning(
                    "Prompt %d (%s) raised: %s",
                    i, prompt["category"], error_str,
                )

            latency_ms = int((time.time() - turn_start) * 1000)

            # Sweep events that fired during this turn
            new_events = recent_events(db, since_marker)
            tool_names = [
                e["data"].get("tool_name") or e["data"].get("name")
                for e in new_events
                if e["event_type"] in (
                    "tool_invoked", "skill.evaluate", "memory.write",
                )
                and isinstance(e.get("data"), dict)
            ]
            tool_names = [n for n in tool_names if n]

            finding = {
                "index": i,
                "category": prompt["category"],
                "prompt": prompt["text"],
                "response_len": len(response_text or ""),
                "response_preview": (response_text or "")[:300],
                "latency_ms": latency_ms,
                "error": error_str,
                "tool_names": tool_names,
                "events": [
                    {"type": e["event_type"], "data": e["data"]}
                    for e in new_events
                ],
                "started_at": since_marker,
                "cost_so_far": cost_so_far(db),
            }
            findings_fp.write(json.dumps(finding, default=str) + "\n")

            logger.info(
                "%3d/%d [%s] %sms %s — %s",
                i + 1, total, prompt["category"], latency_ms,
                "ERR" if error_str else "OK",
                prompt["text"][:60],
            )

            # Sleep between prompts (skip if last)
            if i + 1 < total:
                for _ in range(args.sleep):
                    if check_stop() or _shutting_down["v"]:
                        break
                    time.sleep(1)
    finally:
        findings_fp.close()
        try:
            wq.stop()
        except Exception:
            pass
        try:
            db.close()
        except Exception:
            pass
        state["finished_at"] = now_iso()
        state["final_cost"] = cost_so_far(Database(str(stress_db_path)))
        state_path.write_text(json.dumps(state, indent=2, default=str))
        logger.info(
            "Run complete. Findings → %s, state → %s",
            findings_path, state_path,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception:
        logger.error("Harness crashed:\n%s", traceback.format_exc())
        raise
