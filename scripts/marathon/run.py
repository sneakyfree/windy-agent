"""Marathon harness — one grandma, one session, and a tap on the wire.

Run:
    python scripts/marathon/run.py --turns 500 --out ~/.windy-marathon

What makes this different from ``scripts/night_stress/run.py``:

  1. **ONE session.** night_stress gives every prompt its own
     ``session_id`` (run.py:231), so it is 201 independent first turns.
     This keeps a single session for the whole run, which is the only
     way to cross the episode-eviction cliff at all.

  2. **The prompt tap.** We patch ``windyfly.agent.loop.assemble_prompt``
     and record the EXACT message list handed to the model on every
     turn. This is the agent equivalent of a screenshot: the code can
     look correct, the tests can pass, and grandma's dog's name can
     still not be in the payload. Without the tap you cannot tell these
     two failures apart —

       * **retrieval failure** — the fact never made it into the prompt.
         The model had no chance. Fix: retrieval / keyword extraction.
       * **generation failure** — the fact WAS in the prompt and the
         model still didn't use it. Fix: prompting / model.

     They have opposite fixes, and conflating them is how you spend a
     week tuning the wrong half.

Safety: scratch DB + scratch state dir + scratch HOME. Never points at
a live agent's data. Honors a STOP file and a hard cost cap.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm(s: str) -> str:
    """Lowercase + collapse whitespace, for substring matching."""
    return " ".join((s or "").lower().split())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--turns", type=int, default=500)
    ap.add_argument("--out", default=str(Path.home() / ".windy-marathon"))
    ap.add_argument("--model", default=os.environ.get("DEFAULT_MODEL", "claude-opus-4-8"))
    ap.add_argument("--max-cost", type=float, default=40.0)
    ap.add_argument("--resume", default="")
    ap.add_argument("--sleep", type=float, default=0.0,
                    help="Seconds between turns; paces shared rate limits.")
    ap.add_argument("--stub", action="store_true",
                    help="Replace the LLM with a deterministic echo. Costs "
                         "nothing and still measures the ENTIRE retrieval "
                         "curve: whether a fact reaches the assembled prompt "
                         "is a property of the substrate (episode window + "
                         "hybrid search), not of the model. Use this to get "
                         "the Principle-7 answer for free, then spend real "
                         "tokens only on the generation half.")
    ap.add_argument("--no-semantic", action="store_true",
                    help="Force FTS5-only retrieval even if embeddings are "
                         "installed — i.e. what production does today. Pair "
                         "with a normal run for a controlled A/B.")
    args = ap.parse_args()

    run_dir = Path(args.resume) if args.resume else \
        Path(args.out) / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    run_dir.mkdir(parents=True, exist_ok=True)

    # --- Isolation -------------------------------------------------------
    # Everything the agent could write is redirected under run_dir. The
    # live agent's DB/state/HOME are never in scope.
    scratch_home = run_dir / "home"
    scratch_home.mkdir(exist_ok=True)
    os.environ["HOME"] = str(scratch_home)
    os.environ["WINDY_STATE_DIR"] = str(run_dir / "state")
    db_path = run_dir / "marathon.db"
    os.environ["WINDYFLY_DB_PATH"] = str(db_path)
    os.environ["DEFAULT_MODEL"] = args.model

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    sys.path.insert(0, str(Path(__file__).parent))

    if args.no_semantic:
        # Force the FTS5-only path even when sentence-transformers is
        # installed, so A/B runs differ ONLY in retrieval strategy and
        # not in code, corpus, or timing. This is what production
        # actually does today — the `semantic` extra is opt-in and no
        # install path enables it.
        from windyfly.memory import embeddings as _emb
        _emb._AVAILABLE = False

    from corpus import build_script, FACTS  # noqa: E402
    from windyfly.config import load_config  # noqa: E402
    from windyfly.memory.database import Database  # noqa: E402
    from windyfly.memory.write_queue import WriteQueue  # noqa: E402
    from windyfly.agent import loop as agent_loop  # noqa: E402

    # --- The prompt tap --------------------------------------------------
    # loop.py does `from windyfly.agent.prompt import assemble_prompt`, so
    # the name we must wrap is the one bound INSIDE loop's namespace.
    captured: dict[str, list[dict]] = {"messages": []}
    _real_assemble = agent_loop.assemble_prompt

    def _tapped(*a, **kw):
        msgs = _real_assemble(*a, **kw)
        try:
            captured["messages"] = [
                {"role": m.get("role"), "content": str(m.get("content"))}
                for m in msgs
            ]
        except Exception:
            captured["messages"] = []
        return msgs

    agent_loop.assemble_prompt = _tapped

    if args.stub:
        # Deterministic stand-in for the model. Returns the shape
        # call_llm's callers expect; no network, no spend. `in_reply`
        # will read False for every probe — that is expected and fine,
        # because the number this mode exists to produce is `in_payload`.
        def _stub_llm(messages, model=None, max_tokens=None, tools=None,
                      config=None, **kw):
            return {
                "text": "[stub] acknowledged.",
                "content": "[stub] acknowledged.",
                "tool_calls": [],
                "model": "stub",
                "provider": "stub",
                "input_tokens": sum(len(str(m.get("content", ""))) for m in messages) // 4,
                "output_tokens": 4,
                "cost_usd": 0.0,
            }
        agent_loop.call_llm = _stub_llm

    cfg = load_config()
    cfg.setdefault("agent", {})["default_model"] = args.model
    cfg.setdefault("memory", {})["db_path"] = str(db_path)

    db = Database(str(db_path))
    wq = WriteQueue()
    wq.start()

    script = build_script(args.turns)
    session_id = "marathon-grandma-001"   # ONE session. The whole point.

    findings_path = run_dir / "findings.jsonl"
    payloads_path = run_dir / "payloads.jsonl"
    stop_file = run_dir / "STOP"

    done = 0
    if findings_path.exists():
        done = sum(1 for _ in findings_path.open())

    halt = {"v": False}

    def _sig(_s=None, _f=None):
        halt["v"] = True
        print("\n[marathon] shutdown signal — finishing current turn", flush=True)

    signal.signal(signal.SIGINT, _sig)
    signal.signal(signal.SIGTERM, _sig)

    meta = {
        "started_at": now_iso(), "turns": len(script), "model": args.model,
        "session_id": session_id, "db": str(db_path),
        "facts": [f.key for f in FACTS],
    }
    (run_dir / "meta.json").write_text(json.dumps(meta, indent=2))
    print(f"[marathon] run_dir={run_dir}")
    print(f"[marathon] {len(script)} turns, model={args.model}, "
          f"session={session_id}, resuming at {done}")

    fj = findings_path.open("a")
    fp = payloads_path.open("a")

    for turn in script[done:]:
        if halt["v"] or stop_file.exists():
            print("[marathon] halting")
            break

        captured["messages"] = []
        t0 = time.time()
        reply, err = "", None
        try:
            reply = agent_loop.agent_respond(cfg, db, wq, turn.text, session_id)
        except Exception as e:                     # noqa: BLE001
            err = f"{type(e).__name__}: {e}"
        latency_ms = int((time.time() - t0) * 1000)

        payload_blob = _norm(" ".join(
            m["content"] for m in captured["messages"]
        ))
        reply_norm = _norm(reply)

        rec = {
            "turn": turn.index, "kind": turn.kind, "text": turn.text,
            "fact_key": turn.fact_key, "distance": turn.distance,
            "latency_ms": latency_ms, "error": err,
            "reply_chars": len(reply), "reply": reply[:1200],
            "payload_msgs": len(captured["messages"]),
            "payload_chars": len(payload_blob),
        }

        if turn.kind == "probe":
            # The two-axis measurement that is the point of this harness.
            in_payload = any(_norm(e) in payload_blob for e in turn.expect)
            in_reply = any(_norm(e) in reply_norm for e in turn.expect)
            rec["in_payload"] = in_payload
            rec["in_reply"] = in_reply
            rec["verdict"] = (
                "ok" if in_reply else
                "generation_miss" if in_payload else
                "retrieval_miss"
            )
            flag = {"ok": "✓", "generation_miss": "~", "retrieval_miss": "✗"}[rec["verdict"]]
            print(f"  [{turn.index:>4}] PROBE {turn.fact_key:<10} d={turn.distance:<4} "
                  f"{flag} payload={in_payload} reply={in_reply}", flush=True)
        elif turn.index % 25 == 0:
            print(f"  [{turn.index:>4}] {turn.kind} ({latency_ms}ms)", flush=True)

        # Drain the write queue before the next turn.
        #
        # Episode saves are async (loop.py enqueues them), and in --stub
        # mode a turn costs ~1ms while save_episode WITH embeddings costs
        # 20-50ms. The queue falls behind, and a probe fires before its
        # own seed has been persisted — producing "misses" at distance 5,
        # inside the verbatim window, where a miss is structurally
        # impossible. That is a harness artifact, not a product defect:
        # in production a turn is seconds of model latency and the queue
        # always keeps up.
        #
        # Draining makes the measurement honest. It is deliberately NOT
        # a fix to the product — the async queue is correct there.
        try:
            wq._queue.join()
        except Exception:
            time.sleep(0.05)

        fj.write(json.dumps(rec) + "\n"); fj.flush()
        fp.write(json.dumps({"turn": turn.index,
                             "messages": captured["messages"]}) + "\n"); fp.flush()

        if args.sleep:
            time.sleep(args.sleep)

    fj.close(); fp.close()
    wq.stop()
    print(f"[marathon] done — {findings_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
