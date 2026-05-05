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


# ── Recommendation catalog (grandma-first) ─────────────────────────
#
# The Lamborghini-with-Honda-dashboard rule: technical detail belongs
# in the JSON snapshot files for engineers; the brief delivered to
# the user must read like a note from a friend. No jargon, plain
# verbs, every recommendation is something the user can do with what
# they already know — message the bot, say /reset.
#
# Each entry has:
#   "diagnosis":      one short sentence; how I'm feeling, not what
#                     the metric is. "I forgot a few things" not
#                     "recall fell to 60% across rebuilds".
#   "recommendation": one short sentence; what would help. Always
#                     phrased as a thing the user can DO.
#   "user_action":    the literal words / command. "Just say /reset"
#                     not "invoke the panic handler".
#   "blast_radius":   internal — informs Layer 2 auto-repair safety.

_ORGAN_RECOMMENDATIONS: dict[str, dict[str, dict[str, str]]] = {
    "memory": {
        "yellow": {
            "diagnosis": "I've been forgetting a few things this week.",
            "recommendation": "If you say /reset, it usually clears the cobwebs. Your long-term memory and personality stay safe.",
            "user_action": "Just say /reset — takes about 30 seconds, and I'll be back fresh.",
            "blast_radius": "low",
        },
        "red": {
            "diagnosis": "My memory has been struggling. I'm probably not recalling things you told me.",
            "recommendation": "Please say /reset. That fixes memory issues almost every time. I keep my long-term memory and personality across resets.",
            "user_action": "Say /reset — that's the fix.",
            "blast_radius": "low",
        },
    },
    "heart": {
        "yellow": {
            "diagnosis": "My replies have been a little uneven this week — sometimes quick, sometimes slow.",
            "recommendation": "Probably nothing to worry about. If it bugs you, /reset usually evens me out.",
            "user_action": "Say /reset if I feel sluggish.",
            "blast_radius": "low",
        },
        "red": {
            "diagnosis": "I've been responding really inconsistently. Something's off in my rhythm.",
            "recommendation": "Please say /reset. I'll come back snappy.",
            "user_action": "Say /reset.",
            "blast_radius": "low",
        },
    },
    "voice": {
        "yellow": {
            "diagnosis": "A few of my replies came out a little weird this week — nothing harmful, just not my best work.",
            "recommendation": "If you noticed any reply that looked off, tell me about it. /reset also clears any weirdness.",
            "user_action": "Tell me what looked weird, or just say /reset.",
            "blast_radius": "low",
        },
        "red": {
            "diagnosis": "Some of my replies haven't been formatted properly.",
            "recommendation": "Please say /reset and let me know if you see anything strange afterwards.",
            "user_action": "Say /reset.",
            "blast_radius": "low",
        },
    },
    "immune": {
        "yellow": {
            "diagnosis": "I think I might have wobbled a bit when something tried to confuse me.",
            "recommendation": "/reset reloads my identity from scratch. Quick and easy fix.",
            "user_action": "Say /reset.",
            "blast_radius": "low",
        },
        "red": {
            "diagnosis": "I lost track of who I am for a moment. That shouldn't happen.",
            "recommendation": "Please say /reset right away. That will bring me back to myself.",
            "user_action": "Say /reset right now.",
            "blast_radius": "low",
        },
    },
    "brain": {
        "yellow": {
            "diagnosis": "I had a few empty or odd answers this week.",
            "recommendation": "Could be a brief hiccup with the AI service. /reset usually clears it.",
            "user_action": "Say /reset if I'm not making sense.",
            "blast_radius": "low",
        },
    },
    "lymphatic": {
        "yellow": {
            "diagnosis": "Some of my behind-the-scenes housekeeping fell behind.",
            "recommendation": "/reset cleans everything up nicely.",
            "user_action": "Say /reset.",
            "blast_radius": "low",
        },
    },
    "audit": {
        "yellow": {
            "diagnosis": "I'm having trouble keeping notes about what I do.",
            "recommendation": "Likely a disk-space issue. /reset doesn't fix that — could you ask me 'check disk' so I can report?",
            "user_action": "Ask me 'check disk' and I'll report free space.",
            "blast_radius": "low",
        },
    },
    "liver": {
        "yellow": {
            "diagnosis": "My cost-tracking has been a little off.",
            "recommendation": "Ask me '/budget' to see today's spending. /reset will also refresh things.",
            "user_action": "Say /budget or /reset.",
            "blast_radius": "low",
        },
    },
    "identity": {
        "yellow": {
            "diagnosis": "I've been a little less 'myself' than usual.",
            "recommendation": "/reset reloads my soul and personality fresh.",
            "user_action": "Say /reset.",
            "blast_radius": "low",
        },
    },
}


# ── Grandma-friendly organ display names ───────────────────────────
#
# Used by the brief's "Organ status" section so the dashboard reads
# like a body, not a system architecture diagram.

_ORGAN_FRIENDLY_NAMES: dict[str, str] = {
    "brain":     "thinking",
    "memory":    "remembering",
    "spine":     "putting it all together",
    "heart":     "steady rhythm",
    "voice":     "talking",
    "lungs":     "listening",
    "immune":    "knowing who I am",
    "liver":     "watching what we spend",
    "lymphatic": "tidying up",
    "audit":     "keeping notes",
    "identity":  "being myself",
}


# ── Grandma-friendly status detail formatter ──────────────────────
#
# Engineering details live in the JSON snapshot. The brief gets a
# warm one-line "feeling" instead of "median=5680ms p95=11838ms".

def _grandma_detail(organ: str, verdict: str, raw_detail: str) -> str:
    """Translate engineering metrics into a feeling-statement."""
    if verdict == "green":
        return {
            "brain":     "thinking clearly",
            "memory":    "remembering well",
            "heart":     "steady pace",
            "voice":     "speaking smoothly",
            "immune":    "secure in who I am",
            "liver":     "tracking spending fine",
            "lymphatic": "tidy",
            "audit":     "keeping good notes",
            "identity":  "fully myself",
        }.get(organ, "doing well")
    if verdict == "yellow":
        return {
            "brain":     "an occasional fuzzy thought",
            "memory":    "forgot a couple of things",
            "heart":     "a bit uneven",
            "voice":     "a few odd replies",
            "immune":    "wobbled briefly",
            "liver":     "tracking is slightly off",
            "lymphatic": "a bit cluttered",
            "audit":     "missed a few notes",
            "identity":  "a little less myself",
        }.get(organ, "could be better")
    if verdict == "red":
        return {
            "brain":     "struggling to think clearly",
            "memory":    "having real trouble remembering",
            "heart":     "very uneven",
            "voice":     "garbled replies",
            "immune":    "lost track of who I am",
            "liver":     "tracking is broken",
            "lymphatic": "cluttered",
            "audit":     "can't keep notes",
            "identity":  "not feeling like myself",
        }.get(organ, "needs attention")
    return "status unclear"


_RANK = {"green": 0, "yellow": 1, "red": 2}


def _changed_for_worse(
    prev: dict[str, Any] | None, cur: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """Return organs whose verdict got strictly worse from prev → cur.

    Used by the mid-week red-alarm to suppress notifications when
    state is unchanged or improved. A change is "for worse" iff the
    rank in _RANK strictly increased (green → yellow, green → red,
    or yellow → red).
    """
    prev_o = (prev or {}).get("organs") or {}
    cur_o = (cur or {}).get("organs") or {}
    out: list[dict[str, str]] = []
    for organ, cur_data in cur_o.items():
        cur_v = (cur_data or {}).get("verdict")
        prev_v = (prev_o.get(organ) or {}).get("verdict", "green")
        if not cur_v:
            continue
        if _RANK.get(cur_v, 0) > _RANK.get(prev_v, 0):
            out.append({
                "organ": organ,
                "previous": prev_v,
                "current": cur_v,
            })
    return out


def build_alarm_text(
    snapshots: list[dict[str, Any]] | None = None,
) -> str:
    """Format the mid-week red-alarm message — or return ``""`` if
    the alarm should NOT fire on this comparison.

    Used by both the redalarm script (for delivery) and the v11
    grandma-readability stress harness (for grading). Single source
    of truth so the rubric grades EXACTLY what the user sees.
    """
    decision = should_fire_alarm(snapshots)
    if not decision.get("fire"):
        return ""

    cur_red = decision["current_red"]
    transitions = decision["transitions"]

    lines: list[str] = []
    if cur_red:
        lines.append("🚨 *Heads up — I need attention*")
    else:
        lines.append("⚠️ *Heads up — something feels off*")
    lines.append("")

    reported: set[str] = set()
    for organ in cur_red:
        if organ in reported:
            continue
        reported.add(organ)
        friendly = _ORGAN_FRIENDLY_NAMES.get(organ, organ)
        feeling = _grandma_detail(organ, "red", "")
        rec = (_ORGAN_RECOMMENDATIONS.get(organ, {}).get("red") or {})
        action = rec.get("user_action") or "Say /reset."
        lines.append(f"🔴 *{friendly}*: {feeling}.")
        lines.append(f"  👉 *{action}*")

    for t in transitions:
        organ = t["organ"]
        if organ in reported:
            continue
        reported.add(organ)
        friendly = _ORGAN_FRIENDLY_NAMES.get(organ, organ)
        cur_v = t["current"]
        feeling = _grandma_detail(organ, cur_v, "")
        glyph = {"yellow": "🟡", "red": "🔴"}.get(cur_v, "·")
        rec = (_ORGAN_RECOMMENDATIONS.get(organ, {}).get(cur_v) or {})
        action = rec.get("user_action") or "Say /reset."
        lines.append(f"{glyph} *{friendly}*: {feeling}.")
        lines.append(f"  👉 *{action}*")

    lines.append("")
    lines.append("_Your memory and personality stay safe across /reset._")

    return "\n".join(lines)


def should_fire_alarm(
    snapshots: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Decide whether the mid-week red-alarm should fire.

    Returns a structured alarm payload when the alarm SHOULD fire,
    or an empty dict ``{}`` when it should NOT (silent week).

    Rules (in priority order):
      1. No snapshots yet → silent (nothing to alert on)
      2. Any organ currently RED → fire (regardless of trend)
      3. Any organ got strictly worse since previous snapshot → fire
      4. Otherwise silent (no change or improvement)

    The "fire on currently-red" rule means a sustained red triggers
    every alarm-day until the user /resets — by design. Grandma
    needs the nudge if she missed Wednesday's alarm by Friday.
    """
    snaps = snapshots if snapshots is not None else _load_snapshots(limit=2)
    if not snaps:
        return {}
    cur = snaps[-1]
    prev = snaps[-2] if len(snaps) >= 2 else None

    cur_organs = cur.get("organs") or {}
    cur_red = [
        organ for organ, data in cur_organs.items()
        if (data or {}).get("verdict") == "red"
    ]
    transitions = _changed_for_worse(prev, cur)

    if not cur_red and not transitions:
        return {}

    return {
        "fire": True,
        "current_red": cur_red,
        "transitions": transitions,
        "current_run_id": cur.get("run_id"),
        "previous_run_id": (prev or {}).get("run_id"),
    }


def _build_recommendations(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Translate the latest scorecard's non-green organs into a list
    of structured recommendations the LLM can present conversationally."""
    if not snapshots:
        return [{
            "organ": None,
            "verdict": "no_data",
            "diagnosis": "I don't have any organ scorecards yet — no baseline to compare against.",
            "recommendation": "Run the v10 organ harmony harness once to establish a baseline. After that I'll have weekly data to evaluate.",
            "user_action": "Run: bash scripts/windy-health-trend.sh to see current state, or just ask me 'how are you doing' once a few harness runs have logged.",
            "blast_radius": "low",
        }]

    latest = snapshots[-1]
    organs = latest.get("organs") or {}
    recs: list[dict[str, Any]] = []

    for organ_name, organ_data in organs.items():
        verdict = (organ_data or {}).get("verdict")
        if verdict not in ("yellow", "red"):
            continue
        rec_table = _ORGAN_RECOMMENDATIONS.get(organ_name, {})
        rec = rec_table.get(verdict)
        if not rec:
            continue
        recs.append({
            "organ": organ_name,
            "verdict": verdict,
            "current_detail": (organ_data or {}).get("detail", ""),
            **rec,
        })

    # Cross-snapshot regression detection — if an organ has been
    # consistently degrading over multiple runs, flag it specifically.
    if len(snapshots) >= 3:
        rank = {"green": 0, "yellow": 1, "red": 2}
        for organ_name in (organs.keys()):
            verdicts = []
            for s in snapshots[-3:]:
                v = ((s.get("organs") or {}).get(organ_name) or {}).get("verdict")
                # FIXME(types): v is Optional[str]; rank.get tolerates
                # None at runtime via dict lookup but mypy can't prove
                # it without a default key. Pre-existing.
                verdicts.append(rank.get(v, 0))  # type: ignore[arg-type]
            # Strictly increasing = consistent decline
            if verdicts == sorted(verdicts) and verdicts[0] < verdicts[-1]:
                # Already in recs if currently yellow/red — augment the note.
                for r in recs:
                    if r["organ"] == organ_name:
                        r["trend_note"] = (
                            f"This organ has degraded over the last 3 runs "
                            f"(verdict trajectory: {verdicts}). Worth attention."
                        )
                        break

    return recs


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

    def health_weekly_brief() -> dict[str, Any]:
        """Generate a self-assessment with diagnoses and recommendations.

        This is the "weekly checkup with self-improvement suggestions"
        path. Reads recent scorecards (Ring 1 read-only data) and
        produces a structured report the LLM can format
        conversationally. The report ALWAYS includes a reset_hint so
        the user remembers /reset is the safety net.
        """
        snapshots = _load_snapshots(limit=10)
        recommendations = _build_recommendations(snapshots)

        result: dict[str, Any] = {
            "ok": True,
            "snapshot_count": len(snapshots),
            "recommendations": recommendations,
            "reset_hint": (
                "Whenever you want to roll back any change or just "
                "start fresh, say /reset — your long-term memory and "
                "personality are safe across resets."
            ),
            "applying_changes_hint": (
                "I won't apply any change without you saying so. If a "
                "recommendation looks good, just tell me to do it and "
                "I'll walk through what I'm changing before I touch "
                "anything."
            ),
        }

        if snapshots:
            latest = snapshots[-1]
            result["latest"] = _summarize_latest(latest)
            counts = result["latest"].get("verdict_counts", {})
            green = counts.get("green", 0)
            yellow = counts.get("yellow", 0)
            red = counts.get("red", 0)
            total = green + yellow + red
            # Grandma-first headlines — feeling, not metrics.
            if total > 0:
                if red > 0:
                    headline = (
                        "I'm having a tough time. Could you check on me?"
                    )
                elif yellow > 0:
                    headline = (
                        "I'm doing OK, but a few things feel off this week."
                    )
                else:
                    headline = (
                        "I've been running smoothly! Nothing to worry about."
                    )
            else:
                headline = "I haven't run a checkup yet."
            result["headline"] = headline

        return result

    registry.register(Capability(
        id="health.weekly_brief",
        description=(
            "Generate a weekly self-assessment with diagnoses and "
            "self-improvement recommendations. Use this when:\n"
            "  - The user asks 'how are you doing', 'how have you been', "
            "    'do you need anything', 'are you OK', 'weekly checkup'\n"
            "  - The user has been away for several days and you want "
            "    to give them a 'welcome back, here's how I've been' "
            "    status update\n"
            "  - The user explicitly asks for a self-assessment or "
            "    recommendations on how you could work better\n"
            "Returns a structured report with: organ status, "
            "diagnoses for non-green organs, specific recommendations "
            "the user can apply, and a reset_hint reminding them /reset "
            "is always available.\n"
            "READ-ONLY — never mutates anything. Recommendations are "
            "advisory only; the user must explicitly ask for any to "
            "be applied."
        ),
        handler=health_weekly_brief,
        tier=Tier.PURE_COMPUTE,
        scope="self_assessment",
        audit_required=False,
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
        },
    ))
