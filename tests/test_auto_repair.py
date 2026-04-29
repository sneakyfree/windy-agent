"""Ring 2 auto-repair regression tests.

Locks the safety contract:
  - default OFF (capability is invisible without WINDY_RING2_ENABLED=1)
  - whitelist enforced (no arbitrary knobs)
  - bounds enforced (no out-of-range values)
  - cooldown enforced (no rampage)
  - journal append-only (audit trail survives crashes)
  - this PR does not actually mutate (will in follow-up)
"""

from __future__ import annotations

import json

import pytest

from windyfly.agent.capabilities import (
    Band,
    CapabilityRegistry,
)
from windyfly.agent.capabilities.auto_repair import (
    register_auto_repair_capabilities,
)


@pytest.fixture
def reg_disabled(monkeypatch):
    monkeypatch.delenv("WINDY_RING2_ENABLED", raising=False)
    r = CapabilityRegistry()
    register_auto_repair_capabilities(r, config={})
    return r


@pytest.fixture
def reg_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("WINDY_RING2_ENABLED", "1")
    monkeypatch.setenv(
        "WINDY_AUTO_REPAIR_JOURNAL",
        str(tmp_path / "journal.jsonl"),
    )
    r = CapabilityRegistry()
    register_auto_repair_capabilities(
        r,
        config={
            "personality_sliders": {"context_window": 5, "verbosity": 5},
            "memory": {"max_episodes_per_context": 20},
        },
    )
    return r, tmp_path


# ── Default-off contract ───────────────────────────────────────────


def test_default_off_does_not_register(reg_disabled):
    """Without WINDY_RING2_ENABLED=1, the capabilities must be
    completely invisible — the LLM cannot even see them."""
    assert reg_disabled.get("health.apply_recommendation") is None
    assert reg_disabled.get("health.list_repair_knobs") is None


# ── Whitelist enforcement ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_knob_rejected(reg_enabled):
    r, _ = reg_enabled
    result = await r.invoke(
        "health.apply_recommendation",
        {"knob": "secret_admin_password", "value": "hunter2"},
        Band.OWNER,
    )
    assert result["ok"] is False
    assert "not whitelisted" in result["reason"]


@pytest.mark.asyncio
async def test_known_knob_accepted(reg_enabled):
    r, _ = reg_enabled
    result = await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": 7, "reason": "memory yellow"},
        Band.OWNER,
    )
    assert result["ok"] is True


# ── Bounds enforcement ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_value_above_max_rejected(reg_enabled):
    r, _ = reg_enabled
    result = await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": 999},
        Band.OWNER,
    )
    assert result["ok"] is False
    assert "out of range" in result["reason"]


@pytest.mark.asyncio
async def test_value_below_min_rejected(reg_enabled):
    r, _ = reg_enabled
    result = await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": -5},
        Band.OWNER,
    )
    assert result["ok"] is False
    assert "out of range" in result["reason"]


@pytest.mark.asyncio
async def test_string_value_for_int_knob_rejected(reg_enabled):
    r, _ = reg_enabled
    result = await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": "tomato"},
        Band.OWNER,
    )
    assert result["ok"] is False


# ── Cooldown enforcement ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_cooldown_blocks_second_apply(reg_enabled):
    """Two applies within the cooldown window — second is rejected.
    This is the anti-rampage guard."""
    r, tmp_path = reg_enabled
    first = await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": 6},
        Band.OWNER,
    )
    assert first["ok"] is True

    # Manually log an apply event to simulate prior history.
    journal = tmp_path / "journal.jsonl"
    journal.write_text(json.dumps({
        "kind": "apply",
        "ts": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    }) + "\n")

    second = await r.invoke(
        "health.apply_recommendation",
        {"knob": "verbosity", "value": 6},
        Band.OWNER,
    )
    assert second["ok"] is False
    assert "cooldown" in second["reason"].lower()


# ── Journal contract ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_writes_journal_entry(reg_enabled):
    r, tmp_path = reg_enabled
    await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": 7, "reason": "test"},
        Band.OWNER,
    )
    journal = tmp_path / "journal.jsonl"
    assert journal.exists()
    entries = [json.loads(l) for l in journal.read_text().splitlines() if l.strip()]
    assert len(entries) == 1
    e = entries[0]
    assert e["kind"] == "apply_proposed"
    assert e["knob"] == "context_window"
    assert e["new_value"] == 7
    assert "ts" in e


# ── Layer 2 guarantee: this PR does not mutate ────────────────────


@pytest.mark.asyncio
async def test_apply_does_not_actually_mutate_yet(reg_enabled):
    """Module docstring is explicit: this PR ships surface + guards
    + journal but does NOT yet mutate config. The follow-up turns
    on actual mutation. Pin that contract here so a future change
    can't accidentally enable it before harness-gated rollback is
    also in place."""
    r, _ = reg_enabled
    result = await r.invoke(
        "health.apply_recommendation",
        {"knob": "context_window", "value": 7},
        Band.OWNER,
    )
    assert result["ok"] is True
    assert result["applied"] is False
    assert "would_change" in result
    assert "Layer 2 not yet enabled" in result["next_step"]


# ── List capability ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_returns_whitelist(reg_enabled):
    r, _ = reg_enabled
    result = await r.invoke("health.list_repair_knobs", {}, Band.OWNER)
    assert result["ok"] is True
    assert result["enabled"] is True
    assert "context_window" in result["knobs"]
    cw = result["knobs"]["context_window"]
    assert cw["min"] == 0 and cw["max"] == 10
