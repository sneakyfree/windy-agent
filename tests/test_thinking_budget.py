"""Unit tests for the extended-thinking budget mapping.

Covers the reasoning_depth → thinking-budget curve and the model gating
introduced when opus-4-8 became the live Windy 0 model (extends the
opus-4-7-only thinking path to the whole 4.7+ Opus line).
"""

import pytest

from windyfly.agent.models import _thinking_budget


@pytest.mark.parametrize("model", ["claude-opus-4-7", "claude-opus-4-8"])
def test_thinking_capable_opus_scales_with_depth(model):
    # Below the floor → no thinking (fast/cheap path).
    assert _thinking_budget(model, 0) == 0
    assert _thinking_budget(model, 3) == 0
    # Floor at depth 4 is Anthropic's minimum (1024).
    assert _thinking_budget(model, 4) == 1024
    # Monotonic increase up to the ultrathink ceiling.
    assert _thinking_budget(model, 7) == 4608
    assert _thinking_budget(model, 10) == 8192
    # Unspecified depth (None) → mid default (5), thinking on.
    assert _thinking_budget(model, None) == _thinking_budget(model, 5) > 0


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-haiku-4-5", "gpt-4o-mini"])
def test_non_thinking_models_never_get_a_budget(model):
    for depth in (0, 4, 5, 10, None):
        assert _thinking_budget(model, depth) == 0


def test_depth_is_clamped_to_0_10():
    # Out-of-range values don't blow past the ceiling or below the floor.
    assert _thinking_budget("claude-opus-4-8", 99) == _thinking_budget("claude-opus-4-8", 10)
    assert _thinking_budget("claude-opus-4-8", -5) == 0


def test_dated_model_variant_still_matches():
    # Lenient prefix match so claude-opus-4-8-2026xxxx still gets thinking.
    assert _thinking_budget("claude-opus-4-8-20260115", 10) == 8192
