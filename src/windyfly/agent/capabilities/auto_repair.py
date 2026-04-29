"""Ring 2 — bounded auto-apply with harness-gated rollback.

Default OFF. Enable per-instance with the env var:

    WINDY_RING2_ENABLED=1

When enabled, the agent gains a single new capability,
``health.apply_recommendation``, that:

  1. Snapshots the current value of a whitelisted config knob
  2. Applies a single change, ONE knob at a time
  3. Returns immediately to the LLM with a "I changed X, will
     verify in the next checkup" status
  4. The next v10 run determines if the change helped — if any
     organ went green→yellow or yellow→red since the change, the
     bot rolls back automatically (rollback runs from the same
     snapshot on disk; no LLM involvement).

Hard guarantees:
  - Whitelist of knobs: only personality sliders + memory window
    sizes can be changed. NEVER credentials, capabilities, code,
    or anything that touches an external resource.
  - Bounds: every knob has an inclusive [min, max]. Out-of-range
    requests are rejected.
  - Cooldown: 72 hours between auto-apply attempts. Prevents the
    agent from going on a tuning rampage.
  - Audit: every apply (and every rollback) is logged to a
    durable journal at ~/.windy/auto-repair-journal.jsonl so
    operators can see what was tried.
  - Feature flag: the entire capability is hidden behind
    WINDY_RING2_ENABLED=1. Default-off means a fresh deployment
    can NEVER auto-mutate without an explicit operator decision.

The "harness-gated rollback" half is intentionally NOT enabled in
this PR — it ships in a follow-up after we have a few weeks of
baseline scorecards from Ring 1. For now, applies happen with
human user approval (the LLM asks "should I do it?" and applies on
explicit yes), and rollback is manual via /reset. Layer 2 proper
adds the auto-rollback after we trust the recommendations.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


def _enabled() -> bool:
    """Layer 2 is opt-in via env var. Default off."""
    return os.environ.get("WINDY_RING2_ENABLED", "0") in ("1", "true", "True")


def _journal_path() -> Path:
    return Path(os.environ.get(
        "WINDY_AUTO_REPAIR_JOURNAL",
        "/home/grantwhitmer/.windy/auto-repair-journal.jsonl",
    ))


# ── Whitelist of safe knobs ────────────────────────────────────────
#
# Each entry is (config_path, min, max, type). config_path is a
# tuple of dict keys for nested access in the runtime config.
# Limits chosen for safety: a wide-open slider can't crash the bot
# but COULD make it weird; bounds keep weird-but-not-broken.

_KNOB_WHITELIST: dict[str, dict[str, Any]] = {
    "context_window": {
        "config_path": ("personality_sliders", "context_window"),
        "min": 0,
        "max": 10,
        "type": "int",
        "description": "How much conversation history I include in my context",
    },
    "epistemic_strictness": {
        "config_path": ("personality_sliders", "epistemic_strictness"),
        "min": 0,
        "max": 10,
        "type": "int",
        "description": "How strict I am about confirming facts",
    },
    "verbosity": {
        "config_path": ("personality_sliders", "verbosity"),
        "min": 0,
        "max": 10,
        "type": "int",
        "description": "How much I say per response",
    },
    "max_episodes_per_context": {
        "config_path": ("memory", "max_episodes_per_context"),
        "min": 5,
        "max": 50,
        "type": "int",
        "description": "How many recent messages I consider per turn",
    },
    "max_nodes_per_context": {
        "config_path": ("memory", "max_nodes_per_context"),
        "min": 5,
        "max": 30,
        "type": "int",
        "description": "How many extracted facts I consider per turn",
    },
}


_COOLDOWN_HOURS = 72


def _journal(event: dict) -> None:
    """Append-only audit log of every apply/rollback. Best-effort —
    a journal failure must not block the apply itself."""
    event = {**event, "ts": datetime.now(timezone.utc).isoformat()}
    try:
        path = _journal_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps(event) + "\n")
    except Exception as e:
        logger.debug("auto-repair journal write failed: %s", e)


def _last_apply_age_hours() -> float | None:
    """Hours since the most recent apply event, or None if no
    journal exists yet."""
    path = _journal_path()
    if not path.exists():
        return None
    try:
        last_apply_ts: str | None = None
        for line in path.read_text().splitlines():
            try:
                evt = json.loads(line)
            except json.JSONDecodeError:
                continue
            if evt.get("kind") == "apply":
                last_apply_ts = evt.get("ts")
        if not last_apply_ts:
            return None
        last = datetime.fromisoformat(last_apply_ts.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - last
        return delta.total_seconds() / 3600
    except Exception:
        return None


def register_auto_repair_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register health.apply_recommendation IFF Ring 2 is enabled.

    Default off. The capability is invisible to the LLM unless the
    operator explicitly turns it on. Means a fresh install can't
    accidentally auto-mutate.
    """
    if not _enabled():
        logger.info(
            "Ring 2 (auto-repair) NOT registered — "
            "set WINDY_RING2_ENABLED=1 to opt in"
        )
        return

    logger.info("Registering Ring 2 health.apply_recommendation (auto-repair)")

    def list_applicable_knobs() -> dict[str, Any]:
        """LLM-readable snapshot of what auto-repair CAN change."""
        return {
            "ok": True,
            "enabled": True,
            "cooldown_hours": _COOLDOWN_HOURS,
            "last_apply_age_hours": _last_apply_age_hours(),
            "knobs": {
                name: {
                    "min": k["min"],
                    "max": k["max"],
                    "type": k["type"],
                    "description": k["description"],
                }
                for name, k in _KNOB_WHITELIST.items()
            },
        }

    def apply_recommendation(
        *, knob: str, value: Any, reason: str = "",
    ) -> dict[str, Any]:
        """Apply a single change. STRICTLY bounded, journaled.

        Returns:
          ok=True with the prior+new values on success
          ok=False with explanation if rejected
        """
        if knob not in _KNOB_WHITELIST:
            return {
                "ok": False,
                "reason": f"knob {knob!r} not whitelisted; available: "
                          f"{sorted(_KNOB_WHITELIST.keys())}",
            }
        spec = _KNOB_WHITELIST[knob]

        # Type coerce
        try:
            if spec["type"] == "int":
                value = int(value)
            elif spec["type"] == "float":
                value = float(value)
        except (TypeError, ValueError) as e:
            return {"ok": False, "reason": f"value type mismatch: {e}"}

        if not (spec["min"] <= value <= spec["max"]):
            return {
                "ok": False,
                "reason": (
                    f"{knob}={value} out of range "
                    f"[{spec['min']}, {spec['max']}]"
                ),
            }

        # Cooldown
        age = _last_apply_age_hours()
        if age is not None and age < _COOLDOWN_HOURS:
            return {
                "ok": False,
                "reason": (
                    f"cooldown active: last apply {age:.1f}h ago, "
                    f"need {_COOLDOWN_HOURS}h between changes"
                ),
                "cooldown_hours_remaining": _COOLDOWN_HOURS - age,
            }

        # Read current value (best-effort; if config layout is
        # different, journal the request and bail out cleanly).
        prior_value: Any = None
        try:
            cur = config or {}
            for key in spec["config_path"][:-1]:
                cur = cur.get(key, {})
            prior_value = cur.get(spec["config_path"][-1])
        except Exception:
            prior_value = "unknown"

        # ── This is where Layer 2 proper would WRITE to the
        # ── personality DB / config file. Intentionally NOT done
        # ── in this PR — see module docstring. We journal the
        # ── INTENT only, returning a "would change" preview.
        _journal({
            "kind": "apply_proposed",
            "knob": knob,
            "prior_value": prior_value,
            "new_value": value,
            "reason": reason,
            "applied": False,
            "note": (
                "Ring 2 default-off: this PR ships the surface area + "
                "guards + journal only. Actual mutation enabled in a "
                "follow-up after Layer 1 baseline."
            ),
        })

        return {
            "ok": True,
            "applied": False,  # ← NOT yet — see module docstring
            "would_change": {
                "knob": knob,
                "from": prior_value,
                "to": value,
            },
            "next_step": (
                "Layer 2 not yet enabled to actually mutate config. "
                "The change is journaled but not applied. Reset is "
                "always available if anything ever feels off."
            ),
        }

    registry.register(Capability(
        id="health.list_repair_knobs",
        description=(
            "List what knobs the agent can auto-tune (Ring 2). Use "
            "when the user asks 'what can you change about yourself' "
            "or 'show me what you can adjust'."
        ),
        handler=list_applicable_knobs,
        tier=Tier.PURE_COMPUTE,
        scope="introspection",
        audit_required=True,
        input_schema={"type": "object", "properties": {}, "required": []},
    ))

    registry.register(Capability(
        id="health.apply_recommendation",
        description=(
            "Propose a bounded auto-repair change (Ring 2 — opt-in). "
            "Only available when WINDY_RING2_ENABLED=1. Whitelist of "
            "personality sliders + memory window sizes only. Every "
            "request is journaled to ~/.windy/auto-repair-journal.jsonl. "
            "72-hour cooldown between applies. NEVER touches code, "
            "credentials, or external resources."
        ),
        handler=apply_recommendation,
        tier=Tier.WRITE_DESTRUCTIVE,
        scope="self_repair",
        audit_required=True,
        input_schema={
            "type": "object",
            "properties": {
                "knob":   {"type": "string", "description": "knob name from the whitelist"},
                "value":  {"description": "new value"},
                "reason": {"type": "string", "description": "why apply this change"},
            },
            "required": ["knob", "value"],
        },
    ))
