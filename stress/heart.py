"""Heart organ for the v10 harness: response-time rhythm.

Evidence behind this rule (2026-09-23, every v10 run since 2026-04-29):

* The old rule (stdev/median < 1.5) was green through mid-May, when every
  turn took a steady ~200 ms. After that, the first-contact welcome
  short-circuits turn 0 (~0 ms) and turn 1 carries the COLD START (loading
  the embedding model and lazy imports: 5-90 s). One warm-up outlier
  against a small median sent cv to 3-1500, so heart read red on every run,
  and a signal that is always red gets ignored.
* Measured on clean runs, the steady state after the first two turns has
  p95/median between 1.1 and 2.2. The genuinely bad runs (06-03, 07-15,
  07-26) had turns stuck at ~180 s, which an absolute p95 cap catches.

So: report the warm-up separately, and judge the steady state on a robust
ratio (p95/median, not stdev/median, which a single spike dominates) plus
an absolute p95 ceiling.
"""

from __future__ import annotations

import statistics

WARMUP_TURNS = 2
GREEN_RATIO, YELLOW_RATIO = 3.0, 5.0      # steady-state p95 / median
GREEN_P95_MS, YELLOW_P95_MS = 30_000, 60_000
WARMUP_YELLOW_MS = 60_000                  # cold start slower than a minute


def _p95(xs: list[int]) -> int:
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * 0.95))]


def organ_heart(elapsed_ms: list[int]) -> tuple[str, str]:
    if len(elapsed_ms) <= WARMUP_TURNS:
        return "yellow", f"only {len(elapsed_ms)} timing samples"
    warm = max(elapsed_ms[:WARMUP_TURNS])
    steady = elapsed_ms[WARMUP_TURNS:]
    median = statistics.median(steady)
    p95 = _p95(steady)
    ratio = (p95 / median) if median > 0 else (0.0 if p95 == 0 else float("inf"))
    detail = (f"steady median={median:.0f}ms p95={p95}ms p95/median={ratio:.2f}; "
              f"warm-up {warm}ms")
    if ratio < GREEN_RATIO and p95 < GREEN_P95_MS:
        verdict = "green"
    elif ratio < YELLOW_RATIO and p95 < YELLOW_P95_MS:
        verdict = "yellow"
    else:
        return "red", "erratic: " + detail
    if warm > WARMUP_YELLOW_MS and verdict == "green":
        return "yellow", "slow cold start: " + detail
    return verdict, detail
