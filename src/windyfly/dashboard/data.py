"""Dashboard data aggregation — the Trust Dashboard.

Provides a single endpoint returning all agent state needed
for the user-facing dashboard.
"""

from __future__ import annotations

from typing import Any

from windyfly.control_panel import PRESETS, estimate_monthly_cost, get_sliders
from windyfly.memory.database import Database


def get_dashboard_summary(
    db: Database,
    user_id: str = "default",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the complete dashboard summary.

    Aggregates: memory stats, cost breakdown, failure analysis,
    skills overview, intent counts, personality state, and (when
    ``config`` is supplied) the live agent config + ecosystem URLs so the
    Settings page reflects the running instance instead of UI defaults.

    Args:
        db: Database instance.
        user_id: User ID.
        config: The loaded agent config (so the dashboard can surface the
            real model / temperature / budget / ecosystem URLs).

    Returns:
        Comprehensive dashboard dict.
    """
    cfg = config or {}
    agent = cfg.get("agent", {})
    costs_cfg = cfg.get("costs", {})
    eco = cfg.get("ecosystem", {})
    matrix = cfg.get("matrix", {})
    return {
        "memory": _get_memory_stats(db, user_id),
        "costs": _get_cost_stats(db),
        "failures": _get_failure_stats(db),
        "skills": _get_skill_stats(db),
        "intents": _get_intent_stats(db, user_id),
        "personality": _get_personality_stats(db, user_id),
        "journal": _get_journal_entries(db, user_id),
        "self_assessment": _get_latest_assessment(db),
        "trust_banner": _get_trust_banner(db),
        # Live agent config + ecosystem (Settings page). Empty when no
        # config is passed (e.g. brain-offline fallbacks).
        "config": {
            "model": agent.get("default_model"),
            "temperature": agent.get("temperature"),
            "max_tokens": agent.get("max_response_tokens"),
            "daily_budget": costs_cfg.get("daily_budget_usd"),
        },
        "ecosystem": {
            "eternitas_url": eco.get("eternitas_url"),
            "windy_mail_url": eco.get("windy_mail_url"),
            "matrix_homeserver": matrix.get("homeserver"),
            "windy_cloud_url": eco.get("windy_cloud_url"),
            "windy_pro_url": eco.get("windy_pro_url"),
        },
    }


# Real Eternitas band multipliers from docs/trust-api.md.
_BAND_MULTIPLIERS: dict[str, float] = {
    "exceptional": 5.0,
    "good": 2.0,
    "fair": 1.0,
    "poor": 0.5,
    "critical": 0.0,
}

# Real Eternitas clearance-level → cumulative actions from docs/trust-api.md.
_CLEARANCE_UNLOCKS: dict[str, list[str]] = {
    "registered": ["read"],
    "verified": ["read", "send"],
    "cleared": ["read", "send", "execute", "dm_bots", "install_packages"],
    "top_secret": [
        "read", "send", "execute", "dm_bots", "install_packages",
        "commit_push", "broadcast", "mention_strangers",
    ],
    "eternal": [
        "read", "send", "execute", "dm_bots", "install_packages",
        "commit_push", "broadcast", "mention_strangers", "bypass_rate_caps",
    ],
}


def _get_trust_banner(db: Database) -> dict[str, Any]:
    """Current trust band + clearance + what each unlocks, for the banner."""
    import os
    from windyfly.trust.check import _cache_read, _ensure_table

    passport = os.environ.get("ETERNITAS_PASSPORT", "")
    _ensure_table(db)
    snap = _cache_read(passport, db=db) if passport else None

    if snap is None:
        return {
            "status": "active",
            "band": "unknown",
            "clearance_level": "unknown",
            "tier_multiplier": 0.0,
            "integrity_score": 0,
            "allowed_actions": [],
            "denied_actions": [],
            "dimensions": {},
            "band_multipliers": _BAND_MULTIPLIERS,
            "clearance_unlocks": _CLEARANCE_UNLOCKS,
            "passport": passport,
            "cache_status": "no_snapshot",
        }

    return {
        "status": snap.status,
        "band": snap.band,
        "clearance_level": snap.clearance_level,
        "tier_multiplier": snap.tier_multiplier,
        "integrity_score": snap.integrity_score,
        "allowed_actions": snap.allowed_actions,
        "denied_actions": snap.denied_actions,
        "dimensions": snap.dimensions,
        "band_multipliers": _BAND_MULTIPLIERS,
        "clearance_unlocks": _CLEARANCE_UNLOCKS,
        "passport": passport,
        "cache_status": "ok",
    }


def _get_memory_stats(db: Database, user_id: str) -> dict[str, Any]:
    """Memory statistics."""
    total_nodes = db.fetchone(
        "SELECT COUNT(*) as c FROM nodes WHERE user_id = ?", (user_id,)
    )
    total_episodes = db.fetchone(
        "SELECT COUNT(*) as c FROM episodes WHERE user_id = ?", (user_id,)
    )

    # By epistemic status
    epistemic_rows = db.fetchall(
        """SELECT epistemic_status, COUNT(*) as c FROM nodes
           WHERE user_id = ? GROUP BY epistemic_status""",
        (user_id,),
    )
    by_epistemic = {row["epistemic_status"]: row["c"] for row in epistemic_rows}

    # By scope
    scope_rows = db.fetchall(
        """SELECT scope_id, COUNT(*) as c FROM nodes
           WHERE user_id = ? GROUP BY scope_id""",
        (user_id,),
    )
    by_scope = {row["scope_id"]: row["c"] for row in scope_rows}

    return {
        "total_nodes": total_nodes["c"] if total_nodes else 0,
        "by_epistemic_status": by_epistemic,
        "by_scope": by_scope,
        "total_episodes": total_episodes["c"] if total_episodes else 0,
    }


def _get_cost_stats(db: Database) -> dict[str, Any]:
    """Cost statistics."""
    today = db.fetchone(
        """SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM cost_ledger
           WHERE created_at >= date('now', 'start of day')"""
    )
    week = db.fetchone(
        """SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM cost_ledger
           WHERE created_at >= date('now', '-7 days')"""
    )
    month = db.fetchone(
        """SELECT COALESCE(SUM(cost_usd), 0.0) as total FROM cost_ledger
           WHERE created_at >= date('now', 'start of month')"""
    )

    # By task type
    task_rows = db.fetchall(
        """SELECT COALESCE(task_type, 'chat') as tt, SUM(cost_usd) as total
           FROM cost_ledger GROUP BY tt"""
    )
    by_task = {row["tt"]: round(row["total"], 4) for row in task_rows}

    return {
        "today_usd": round(today["total"], 4) if today else 0.0,
        "this_week_usd": round(week["total"], 4) if week else 0.0,
        "this_month_usd": round(month["total"], 4) if month else 0.0,
        "by_task_type": by_task,
    }


def _get_failure_stats(db: Database) -> dict[str, Any]:
    """Failure analysis."""
    total = db.fetchone("SELECT COUNT(*) as c FROM failures")
    resolved = db.fetchone(
        "SELECT COUNT(*) as c FROM failures WHERE resolved_at IS NOT NULL"
    )
    total_c = total["c"] if total else 0
    resolved_c = resolved["c"] if resolved else 0

    # By fault type
    type_rows = db.fetchall(
        "SELECT fault_type, COUNT(*) as c FROM failures GROUP BY fault_type"
    )
    by_type = {row["fault_type"]: row["c"] for row in type_rows}

    return {
        "total": total_c,
        "resolved": resolved_c,
        "unresolved": total_c - resolved_c,
        "by_type": by_type,
        "improvement_rate": round(resolved_c / total_c, 2) if total_c > 0 else 1.0,
    }


def _get_skill_stats(db: Database) -> dict[str, Any]:
    """Skills overview."""
    total = db.fetchone("SELECT COUNT(*) as c FROM skills")
    promoted = db.fetchone("SELECT COUNT(*) as c FROM skills WHERE promoted = TRUE")

    top_5 = db.fetchall(
        """SELECT name, usage_count, success_count, failure_count
           FROM skills WHERE promoted = TRUE
           ORDER BY usage_count DESC LIMIT 5"""
    )
    top_skills = []
    for s in top_5:
        usage = s["usage_count"] or 0
        success = s["success_count"] or 0
        rate = round(success / usage, 2) if usage > 0 else 1.0
        top_skills.append({
            "name": s["name"],
            "usage_count": usage,
            "success_rate": rate,
        })

    return {
        "total": total["c"] if total else 0,
        "promoted": promoted["c"] if promoted else 0,
        "top_5_by_usage": top_skills,
    }


def _get_intent_stats(db: Database, user_id: str) -> dict[str, Any]:
    """Intent counts."""
    active = db.fetchone(
        "SELECT COUNT(*) as c FROM intents WHERE status = 'active' AND user_id = ?",
        (user_id,),
    )
    completed = db.fetchone(
        "SELECT COUNT(*) as c FROM intents WHERE status = 'completed' AND user_id = ?",
        (user_id,),
    )
    paused = db.fetchone(
        "SELECT COUNT(*) as c FROM intents WHERE status = 'paused' AND user_id = ?",
        (user_id,),
    )

    return {
        "active": active["c"] if active else 0,
        "completed": completed["c"] if completed else 0,
        "abandoned": paused["c"] if paused else 0,
    }


def get_active_intents(
    db: Database, user_id: str = "default", limit: int = 50
) -> list[dict[str, Any]]:
    """Active intents as a list — matches the ``active`` count shown on Home.

    The dashboard's intent endpoint previously used
    ``surface_pending_intents`` (only chat-inferred intents from the last
    24h), so the "Active Goals" list was empty even when Home reported N
    active. This returns all active intents and maps the table's
    ``description`` column to the ``goal`` field the UI expects.
    """
    rows = db.fetchall(
        """
        SELECT id, description, status, created_at
        FROM intents
        WHERE status = 'active' AND user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
        """,
        (user_id, limit),
    )
    return [
        {
            "id": r["id"],
            "goal": r["description"],
            "status": r["status"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]


def _get_personality_stats(db: Database, user_id: str) -> dict[str, Any]:
    """Personality state."""
    sliders = get_sliders(db, user_id=user_id)
    cost = estimate_monthly_cost(sliders)

    # Detect preset
    preset = "custom"
    for name, values in PRESETS.items():
        if all(sliders.get(k) == v for k, v in values.items()):
            preset = name
            break

    return {
        "sliders": sliders,
        "preset": preset,
        "estimated_monthly_cost": cost["estimated_usd"],
    }


def _get_journal_entries(db: Database, user_id: str) -> list[dict]:
    """Get recent journal entries for the dashboard."""
    from windyfly.memory.nodes import get_nodes_by_type
    entries = get_nodes_by_type(db, "journal_entry", limit=20)
    result = []
    for e in entries:
        metadata = e.get("metadata", "{}")
        if isinstance(metadata, str):
            import json
            try:
                metadata = json.loads(metadata)
            except (json.JSONDecodeError, TypeError):
                metadata = {}
        result.append({
            "entry": metadata.get("entry", e.get("name", "")),
            "emotional_context": metadata.get("emotional_context", "neutral"),
            "created_at": e.get("created_at", ""),
        })
    return result


def _get_latest_assessment(db: Database) -> dict[str, Any] | None:
    """Get the most recent self-assessment."""
    from windyfly.memory.nodes import get_nodes_by_type
    assessments = get_nodes_by_type(db, "self_assessment", limit=1)
    if assessments:
        import json
        meta = assessments[0].get("metadata", "{}")
        if isinstance(meta, str):
            try:
                return json.loads(meta)
            except (json.JSONDecodeError, TypeError):
                pass
    return None
