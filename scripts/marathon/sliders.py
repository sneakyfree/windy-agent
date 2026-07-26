"""Slider conformance probe — which knobs actually move the agent?

The control panel advertises 15+ personality sliders on a 0–10 dial,
each with prose describing what low and high "feel" like, and a cost
model that bills the user per point. A normie who drags a slider and
sees no change has been lied to by the UI — which is a Principle-#1
failure, not a cosmetic one.

This probe answers the question the only way worth trusting: set each
slider to a range of values, assemble the REAL prompt through the real
code path, and diff the bytes that actually reach the model.

No LLM calls. Prompt assembly is deterministic, so this is free and
exact — the assembled payload either differs or it doesn't.

Run:
    python scripts/marathon/sliders.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

VALUES = [0, 2, 3, 5, 7, 8, 10]


def main() -> int:
    tmp = Path(tempfile.mkdtemp(dir="/tmp"))
    os.environ["HOME"] = str(tmp)
    os.environ["WINDY_STATE_DIR"] = str(tmp / "state")
    os.environ["WINDYFLY_DB_PATH"] = str(tmp / "s.db")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

    from windyfly.config import load_config
    from windyfly.control_panel import SLIDER_INFO, _COST_PER_POINT
    from windyfly.memory.database import Database
    from windyfly.memory.episodes import save_episode
    from windyfly.agent.prompt import assemble_prompt

    db = Database(str(tmp / "s.db"))
    # A little history so the memory-shaped sliders have something to act on.
    for i in range(60):
        save_episode(db, "user", f"turn {i}: my dog is Biscuit the beagle",
                     session_id="s")
        save_episode(db, "assistant", f"noted, turn {i}", session_id="s")

    cfg = load_config()
    cfg.setdefault("memory", {})["db_path"] = str(tmp / "s.db")

    from windyfly.control_panel import TOGGLE_SLIDERS
    from windyfly.memory.soul import upsert_soul

    def payload_for(slider: str, value: int) -> str:
        """Assemble the real prompt with exactly ONE slider moved.

        Every other slider is reset to its baseline first. Without that
        reset the sweep is order-dependent and lies: sliders are visited
        alphabetically, so once the ``raw_mode`` sweep left it at 10,
        every slider after it (reasoning_depth, response_length,
        verbosity, ...) was measured with raw mode ON — which suppresses
        the whole Behavioral Modifiers block — and looked dead when it
        was not. Reset first, then move one knob.
        """
        for other in SLIDER_INFO:
            base = 0 if other in TOGGLE_SLIDERS else 5
            upsert_soul(db, f"slider_{other}", str(base))
        upsert_soul(db, f"slider_{slider}", str(value))
        msgs = assemble_prompt(cfg, db, "what was my dog's name again hon", "s")
        return "\n".join(f"{m.get('role')}::{m.get('content')}" for m in msgs)

    names = sorted(set(list(SLIDER_INFO.keys())))
    print(f"\n{'='*78}")
    print("SLIDER CONFORMANCE — does moving it change what reaches the model?")
    print(f"{'='*78}")
    print(f"{'slider':<24} {'$/pt':>5}  {'distinct payloads':>17}   active range")
    print("-" * 78)

    dead, stepped, live = [], [], []
    for name in names:
        seen: dict[str, list[int]] = {}
        for v in VALUES:
            try:
                h = hashlib.sha1(payload_for(name, v).encode()).hexdigest()[:10]
            except Exception as e:                       # noqa: BLE001
                h = f"ERR:{type(e).__name__}"
            seen.setdefault(h, []).append(v)
        cost = _COST_PER_POINT.get(name, 0.0)
        n = len(seen)
        # Describe which value-bands are indistinguishable.
        bands = " | ".join(
            ",".join(str(v) for v in vs) for vs in seen.values()
        )
        verdict = "DEAD" if n == 1 else ("step" if n <= 3 else "graded")
        print(f"{name:<24} {cost:>5.2f}  {n:>17}   {verdict}: {bands}")
        (dead if n == 1 else (stepped if n <= 3 else live)).append((name, cost))

    print("-" * 78)
    # A slider can legitimately leave the PROMPT untouched and still do
    # real work by shaping the model CALL or a background job. This
    # probe only sees the prompt, so those must not be reported as dead
    # — the honest label is "acts elsewhere", with the call site named
    # so the claim is checkable.
    ACTS_ELSEWHERE = {
        "creativity": "loop.py — LLM temperature",
        "response_length": "loop.py — max_tokens",
        "tool_reloop_rounds": "loop.py — max tool rounds",
        "emotional_sensitivity": "loop.py — emotional context detection",
        "memory_retention": "memory/decay.py — background decay job",
        "shape_shift_bias": "agent/shape_shift.py",
        "adaptive_mode": "deprecated; also env-gated by "
                         "WINDY_ADAPTIVE_MODE_ENABLED=1 (correctly inert)",
    }
    truly_dead = [(n, c) for n, c in dead if n not in ACTS_ELSEWHERE]
    elsewhere = [(n, c) for n, c in dead if n in ACTS_ELSEWHERE]

    print(f"\nNO PROMPT EFFECT, but acts elsewhere ({len(elsewhere)}) — "
          f"not dead, just invisible to this probe:")
    for n, c in elsewhere:
        print(f"   {n:<24} ${c:.2f}/pt   -> {ACTS_ELSEWHERE[n]}")

    print(f"\nTRULY DEAD ({len(truly_dead)}) — no consumer anywhere in src/:")
    for n, c in truly_dead:
        flag = "  <-- AND IT IS BILLED" if c > 0 else ""
        print(f"   {n:<24} ${c:.2f}/point{flag}")
    print(f"\nSTEP ({len(stepped)}) — only 2-3 distinct behaviors across 0-10:")
    for n, c in stepped:
        print(f"   {n:<24} ${c:.2f}/point")
    print(f"\nGRADED ({len(live)}) — genuinely responsive:")
    for n, c in live:
        print(f"   {n:<24} ${c:.2f}/point")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
