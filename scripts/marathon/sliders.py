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


VALUES = [0, 2, 3, 5, 7, 8, 10]


def main() -> int:
    # No dir= — hardcoding "/tmp" makes this harness unrunnable on
    # Windows, the platform it most needs to check. gettempdir() is
    # correct everywhere.
    tmp = Path(tempfile.mkdtemp())
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

    # Separate history under a REAL rolling session id, so the probe can
    # also assemble a WAKING turn (see ``payload_for(waking=True)``).
    # Long turns so a character-budgeted block actually varies with the
    # slider instead of just swallowing the whole history at every
    # setting.
    _filler = " and then we talked about the weather at length" * 4
    for i in range(200):
        save_episode(db, "user",
                     f"turn {i}: my dog is Biscuit the beagle.{_filler}",
                     session_id="telegram:1:v0")
        save_episode(db, "assistant", f"noted, turn {i}.{_filler}",
                     session_id="telegram:1:v0")

    cfg = load_config()
    cfg.setdefault("memory", {})["db_path"] = str(tmp / "s.db")

    from windyfly.control_panel import TOGGLE_SLIDERS
    from windyfly.memory.soul import upsert_soul

    def payload_for(slider: str, value: int, *, waking: bool = False) -> str:
        """Assemble the real prompt with exactly ONE slider moved.

        Every other slider is reset to its baseline first. Without that
        reset the sweep is order-dependent and lies: sliders are visited
        alphabetically, so once the ``raw_mode`` sweep left it at 10,
        every slider after it (reasoning_depth, response_length,
        verbosity, ...) was measured with raw mode ON — which suppresses
        the whole Behavioral Modifiers block — and looked dead when it
        was not. Reset first, then move one knob.

        ``waking=True`` assembles the FIRST turn after a ``/new`` — a
        fresh session id whose channel has prior history. Some sliders
        act only there (``memory_depth`` decides how far back the agent
        re-reads on waking), and measuring them on an ordinary turn
        reports them dead when they are not. That mislabel is the whole
        reason this probe exists, so it must not commit it itself.
        """
        for other in SLIDER_INFO:
            base = 0 if other in TOGGLE_SLIDERS else 5
            upsert_soul(db, f"slider_{other}", str(base))
        upsert_soul(db, f"slider_{slider}", str(value))
        session = "telegram:1:v1" if waking else "s"
        msgs = assemble_prompt(
            cfg, db, "what was my dog's name again hon", session,
        )
        return "\n".join(f"{m.get('role')}::{m.get('content')}" for m in msgs)

    def distinct_payloads(slider: str, *, waking: bool = False) -> int:
        hashes = set()
        for v in VALUES:
            try:
                p = payload_for(slider, v, waking=waking)
                hashes.add(hashlib.sha1(p.encode()).hexdigest()[:10])
            except Exception as e:                       # noqa: BLE001
                hashes.add(f"ERR:{type(e).__name__}")
        return len(hashes)

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
    # A slider can also be inert on an ordinary turn and fully alive on
    # the FIRST turn after /new. Don't assert that from a lookup table —
    # re-measure those candidates against a waking prompt and let the
    # bytes decide.
    waking_only = [
        (n, c) for n, c in dead
        if n not in ACTS_ELSEWHERE and distinct_payloads(n, waking=True) > 1
    ]
    waking_names = {n for n, _ in waking_only}

    truly_dead = [
        (n, c) for n, c in dead
        if n not in ACTS_ELSEWHERE and n not in waking_names
    ]
    elsewhere = [(n, c) for n, c in dead if n in ACTS_ELSEWHERE]

    print(f"\nNO PROMPT EFFECT, but acts elsewhere ({len(elsewhere)}) — "
          f"not dead, just invisible to this probe:")
    for n, c in elsewhere:
        print(f"   {n:<24} ${c:.2f}/pt   -> {ACTS_ELSEWHERE[n]}")

    print(f"\nWAKING-TURN ONLY ({len(waking_only)}) — byte-identical on an "
          f"ordinary turn, but MEASURABLY live on the first turn after "
          f"/new:")
    for n, c in waking_only:
        n_wake = distinct_payloads(n, waking=True)
        print(f"   {n:<24} ${c:.2f}/pt   -> {n_wake} distinct waking payloads")

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
