"""The v10 heart organ must separate cold start from rhythm (see stress/heart.py)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "stress"))

from heart import organ_heart  # noqa: E402


def test_healthy_run_with_cold_start_is_green() -> None:
    # 2026-09-23 re-run: welcome turn ~0 ms, 9 s cold start, then steady ~500 ms.
    el = [0, 9004] + [511] * 18 + [600, 700, 855, 800, 650]
    assert organ_heart(el)[0] == "green"


def test_may_steady_run_is_green() -> None:
    assert organ_heart([205, 183] + [175, 180, 190, 200, 170] * 4 + [199, 188, 176])[0] == "green"


def test_spiky_steady_state_is_red() -> None:
    assert organ_heart([0, 9000] + [200] * 20 + [2000] * 3)[0] == "red"


def test_hung_turns_are_red_even_with_a_low_median() -> None:
    # 2026-06-03 shape: most turns ~0 ms, a few stuck at ~180 s.
    assert organ_heart([466, 1] + [0] * 18 + [186_203] * 3)[0] == "red"


def test_slow_cold_start_alone_is_yellow_not_red() -> None:
    verdict, detail = organ_heart([0, 88_000] + [400] * 23)
    assert verdict == "yellow" and "cold start" in detail


def test_too_few_samples_is_yellow() -> None:
    assert organ_heart([10, 20])[0] == "yellow"
