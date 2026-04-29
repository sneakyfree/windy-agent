"""health.* capability — agent self-awareness via the v10 organ
harmony scorecards.

Ring 1 of the recursive-self-improvement architecture. The agent
can read its OWN organ-health scorecards (written by
``stress_v10_organ_harmony.py``) and answer questions like:

  - "How have you been doing lately?"
  - "Are any of your organs unhealthy?"
  - "Has your memory recall been good?"

This is observe-only. The capability cannot mutate config, code, or
sliders. Future Ring 2 will add bounded knob-tuning, but THAT lives
behind a separate capability with explicit harness gating — not
this one. Keeping the surface small here is intentional: a "read
your scorecards" function CAN'T zombie-loop or break anything.

Scorecards live at ``$WINDY_HEALTH_DIR`` (default
``~/.windy-stress/health/``), one JSON file per v10 run.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from windyfly.agent.capabilities.descriptor import Capability, Tier
from windyfly.agent.capabilities.registry import CapabilityRegistry

logger = logging.getLogger(__name__)


def _health_dir() -> Path:
    """Where v10 writes scorecards."""
    return Path(os.environ.get(
        "WINDY_HEALTH_DIR",
        "/home/grantwhitmer/.windy-stress/health",
    ))


def _load_snapshots(limit: int = 10) -> list[dict[str, Any]]:
    """Load the most-recent N scorecards, oldest → newest."""
    d = _health_dir()
    if not d.exists():
        return []
    files = sorted(d.glob("*.json"))[-limit:]
    out: list[dict[str, Any]] = []
    for p in files:
        try:
            out.append(json.loads(p.read_text()))
        except Exception as e:
            logger.debug("skip unparseable health file %s: %s", p, e)
    return out


def _summarize_latest(snap: dict[str, Any]) -> dict[str, Any]:
    """Compact human-readable summary of one scorecard."""
    organs = snap.get("organs") or {}
    counts = snap.get("verdict_counts") or {}
    if not counts:
        counts = {
            "green":  sum(1 for v in organs.values() if v.get("verdict") == "green"),
            "yellow": sum(1 for v in organs.values() if v.get("verdict") == "yellow"),
            "red":    sum(1 for v in organs.values() if v.get("verdict") == "red"),
        }
    return {
        "run_id": snap.get("run_id"),
        "ts": snap.get("ts"),
        "model": snap.get("model"),
        "real_llm": snap.get("real_llm", False),
        "turns": snap.get("turns"),
        "elapsed_total_s": snap.get("elapsed_total_s"),
        "verdict_counts": counts,
        "organs": {
            name: {
                "verdict": data.get("verdict"),
                "detail": data.get("detail"),
            }
            for name, data in organs.items()
        },
    }


def _detect_regressions(
    prev: dict[str, Any], cur: dict[str, Any],
) -> list[dict[str, str]]:
    """Compare two snapshots and report any organ that got worse."""
    rank = {"green": 0, "yellow": 1, "red": 2}
    prev_o = prev.get("organs") or {}
    cur_o = cur.get("organs") or {}
    regressions: list[dict[str, str]] = []
    for organ, cur_data in cur_o.items():
        cur_v = (cur_data or {}).get("verdict")
        prev_v = (prev_o.get(organ) or {}).get("verdict")
        if cur_v and prev_v and rank.get(cur_v, 0) > rank.get(prev_v, 0):
            regressions.append({
                "organ": organ,
                "previous": prev_v,
                "current": cur_v,
                "detail": (cur_data or {}).get("detail", ""),
            })
    return regressions


def register_health_capabilities(
    registry: CapabilityRegistry,
    config: dict[str, Any] | None = None,
) -> None:
    """Register health.recent and health.trend.

    Both are READ_LOCAL — no external API, no DB writes, no
    mutation. Just file reads on the health snapshot directory.
    Safe under any band including USER.
    """
    logger.info("Registering health.* capabilities (Ring 1 self-awareness)")

    def health_recent() -> dict[str, Any]:
        """Latest organ scorecard, plus regression vs previous run."""
        snaps = _load_snapshots(limit=2)
        if not snaps:
            return {
                "ok": False,
                "reason": "no scorecards yet",
                "hint": "run stress_v10_organ_harmony.py to create one",
            }
        latest = _summarize_latest(snaps[-1])
        result = {"ok": True, "latest": latest, "snapshot_count": len(snaps)}
        if len(snaps) >= 2:
            regressions = _detect_regressions(snaps[-2], snaps[-1])
            result["regressions_since_previous"] = regressions
            result["regression_count"] = len(regressions)
        else:
            result["regressions_since_previous"] = []
            result["regression_count"] = 0
        return result

    def health_trend(*, limit: int = 10) -> dict[str, Any]:
        """Verdict-count trend across the last N runs (oldest → newest)."""
        snaps = _load_snapshots(limit=max(1, min(limit, 50)))
        if not snaps:
            return {
                "ok": False,
                "reason": "no scorecards yet",
            }
        trend = []
        for s in snaps:
            counts = s.get("verdict_counts") or {}
            if not counts:
                organs = s.get("organs") or {}
                counts = {
                    "green":  sum(1 for v in organs.values() if v.get("verdict") == "green"),
                    "yellow": sum(1 for v in organs.values() if v.get("verdict") == "yellow"),
                    "red":    sum(1 for v in organs.values() if v.get("verdict") == "red"),
                }
            trend.append({
                "run_id": s.get("run_id"),
                "ts": s.get("ts"),
                "real_llm": s.get("real_llm", False),
                "verdict_counts": counts,
            })
        return {"ok": True, "trend": trend, "count": len(trend)}

    registry.register(Capability(
        id="health.recent",
        description=(
            "Read the agent's MOST RECENT organ-health scorecard and "
            "any regressions since the previous run. Use this when "
            "the user asks 'how have you been doing?', 'are you "
            "healthy?', 'are any of your organs unhealthy?', or "
            "anything similar. Returns ok/false if no scorecards "
            "exist yet (run stress_v10_organ_harmony.py to create one)."
        ),
        handler=health_recent,
        tier=Tier.PURE_COMPUTE,
        scope="introspection",
        audit_required=False,
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ))

    registry.register(Capability(
        id="health.trend",
        description=(
            "Read the agent's organ-health TREND across the last N "
            "v10 runs. Use this when the user asks about long-term "
            "health trends, 'how have you been over the past weeks?', "
            "or whether any organ has been getting worse."
        ),
        handler=health_trend,
        tier=Tier.PURE_COMPUTE,
        scope="introspection",
        audit_required=False,
        input_schema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "Number of recent runs to include (1-50)",
                    "default": 10,
                },
            },
            "required": [],
        },
    ))
