"""Marathon analyzer — the recall-vs-distance curve.

Reads ``findings.jsonl`` and answers the question the whole product
rests on: **as a conversation gets longer, when does the agent stop
remembering, and whose fault is it?**

Every probe lands in one of three buckets:

  ``ok``               the fact came back in the reply.
  ``generation_miss``  the fact WAS in the assembled prompt and the
                       model still didn't use it. Prompting/model
                       problem.
  ``retrieval_miss``   the fact never reached the prompt at all. The
                       model never had a chance. Substrate problem —
                       this is real amnesia, and it is the one that
                       matters for Principle #7.

The distinction only exists because run.py taps the wire. Without it
every miss looks the same and you tune the wrong half.
"""

from __future__ import annotations

import sys

import argparse
import json
from collections import defaultdict
from pathlib import Path

# Windows consoles default to cp1252, and this harness prints the
# agent's own replies — which contain the 🪰 emoji and em-dashes. Without
# this the script dies with:
#   UnicodeEncodeError: 'charmap' codec can't encode character '\U0001fab0'
# i.e. exactly the class of bug the src/ encoding sweep fixed, in the
# tooling that was supposed to be checking for it. errors="replace" so a
# stray glyph degrades to a placeholder instead of killing the run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    args = ap.parse_args()
    run_dir = Path(args.run_dir)

    rows = [json.loads(x) for x in (run_dir / "findings.jsonl").open() if x.strip()]
    probes = [r for r in rows if r.get("kind") == "probe"]
    errors = [r for r in rows if r.get("error")]

    print(f"\n{'='*66}")
    print(f"MARATHON REPORT — {run_dir.name}")
    print(f"{'='*66}")
    print(f"turns={len(rows)}  probes={len(probes)}  errors={len(errors)}")

    lat = sorted(r["latency_ms"] for r in rows if r.get("latency_ms"))
    if lat:
        print(f"latency  p50={lat[len(lat)//2]}ms  "
              f"p95={lat[int(len(lat)*0.95)]}ms  max={lat[-1]}ms")

    # --- Prompt growth: is the payload actually bounded? ---------------
    print("\n--- PAYLOAD SIZE (is the prompt bounded by construction?) ---")
    buckets: dict[int, list[int]] = defaultdict(list)
    for r in rows:
        if r.get("payload_chars"):
            buckets[r["turn"] // 50 * 50].append(r["payload_chars"])
    for start in sorted(buckets):
        vals = buckets[start]
        print(f"  turns {start:>4}-{start+49:<4}  "
              f"avg={sum(vals)//len(vals):>7} chars   "
              f"max={max(vals):>7}")

    # --- The curve ------------------------------------------------------
    print("\n--- RECALL vs DISTANCE (turns since the fact was told) ---")
    by_dist: dict[int, list[dict]] = defaultdict(list)
    for p in probes:
        by_dist[p["distance"]].append(p)

    print(f"  {'dist':>5} {'n':>4} {'recall':>8} {'retr.miss':>10} {'gen.miss':>9}   bar")
    for d in sorted(by_dist):
        ps = by_dist[d]
        n = len(ps)
        ok = sum(1 for p in ps if p.get("verdict") == "ok")
        rm = sum(1 for p in ps if p.get("verdict") == "retrieval_miss")
        gm = sum(1 for p in ps if p.get("verdict") == "generation_miss")
        pct = ok / n * 100 if n else 0
        bar = "█" * int(pct / 5)
        print(f"  {d:>5} {n:>4} {pct:>7.0f}% {rm:>10} {gm:>9}   {bar}")

    # --- Per fact -------------------------------------------------------
    print("\n--- PER FACT ---")
    by_fact: dict[str, list[dict]] = defaultdict(list)
    for p in probes:
        by_fact[p["fact_key"]].append(p)
    for k in sorted(by_fact):
        ps = by_fact[k]
        ok = sum(1 for p in ps if p.get("verdict") == "ok")
        worst = [str(p["distance"]) for p in ps if p.get("verdict") == "retrieval_miss"]
        print(f"  {k:<12} {ok}/{len(ps)} recalled"
              + (f"   retrieval-miss at d={','.join(worst)}" if worst else ""))

    # --- Verdict --------------------------------------------------------
    total = len(probes)
    if total:
        ok = sum(1 for p in probes if p.get("verdict") == "ok")
        rm = sum(1 for p in probes if p.get("verdict") == "retrieval_miss")
        gm = sum(1 for p in probes if p.get("verdict") == "generation_miss")
        print(f"\n{'='*66}")
        print(f"OVERALL RECALL: {ok}/{total} = {ok/total*100:.1f}%")
        print(f"  retrieval misses (substrate — P7 failures): {rm} "
              f"({rm/total*100:.1f}%)")
        print(f"  generation misses (model had it, didn't use it): {gm} "
              f"({gm/total*100:.1f}%)")
        print(f"{'='*66}\n")

    if errors:
        print("--- ERRORS ---")
        seen: dict[str, int] = defaultdict(int)
        for e in errors:
            seen[e["error"][:90]] += 1
        for msg, c in sorted(seen.items(), key=lambda kv: -kv[1]):
            print(f"  {c:>4}x  {msg}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
