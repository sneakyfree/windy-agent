"""Spend monitor + pause / kill switch.

Solves the multi-agent token-burn nightmare: when Grant has 8
agents running and one of them is in a zombie loop burning tokens
24/7 but reporting "I'm not spending anything", he needs:

  1. PER-AGENT visibility — which agent is burning, broken out by
     provider (Anthropic vs OpenAI vs Grok vs ...)
  2. ROLLING burn rate, not just cumulative — to spot spikes the
     moment they start, not after the daily budget exhausts
  3. A KILL BUTTON that actually stops LLM calls — distinct from
     killing the bot itself, since the bot may be doing useful
     non-LLM work (Telegram polling, listening, scheduling)

This module is the engine. The /pause /resume /spend slash
commands consume it. The agent_respond pause check at top of the
loop makes the kill button immediate — no in-flight call gets
charged once the flag is set.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from windyfly.platform import windy_state_dir
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from windyfly.memory.database import Database

logger = logging.getLogger(__name__)


def _pause_flag_path() -> Path:
    """File-based pause flag. Survives bot restart by design — a
    pause should NOT silently un-pause when systemd respawns the
    process. Operator must explicitly /resume."""
    return Path(os.environ.get(
        "WINDY_PAUSE_FLAG",
        str(windy_state_dir() / ".paused"),
    ))


# ── YOLO mode ──────────────────────────────────────────────────────
#
# Sometimes the user wants the bot to cook hard for hours: long
# research run, agentic browse session, big code-gen sprint. The
# default $5/hour auto-pause would clip the work mid-flight. YOLO
# lets the user opt OUT of auto-pause for a bounded window.
#
# Design:
#   - File-based flag at ~/.windy/.yolo with an explicit expires_at.
#   - is_yolo_active() returns True only if the flag exists AND
#     expires_at is still in the future. Past-expiry is treated as
#     not-active (and the file is cleaned up on read).
#   - Hard cap: 7 days max per session. Past 7 days the user must
#     re-enable. Prevents "I forgot YOLO was on for 3 months."
#   - maybe_auto_pause() skips the threshold check when YOLO is
#     active — but the moment YOLO expires, the next heartbeat
#     evaluates the threshold normally.

_YOLO_MAX_HOURS = 7 * 24  # one week hard cap


def _yolo_flag_path() -> Path:
    return Path(os.environ.get(
        "WINDY_YOLO_FLAG",
        str(windy_state_dir() / ".yolo"),
    ))


def yolo_status() -> dict[str, Any]:
    """Read the YOLO flag. Cleans up an expired flag in passing.

    Returns:
        {
          "active":     bool,
          "expires_at": ISO timestamp or None,
          "hours_remaining": float or 0,
          "enabled_at": ISO or None,
          "hours":      int (original duration) or None,
          "actor":      str or None,
        }
    """
    path = _yolo_flag_path()
    if not path.exists():
        return {"active": False, "expires_at": None, "hours_remaining": 0}
    try:
        data = json.loads(path.read_text())
    except Exception:
        # Torn flag — treat as inactive but DON'T delete (might be
        # mid-write); next heartbeat will see it cleaned by next
        # successful write.
        return {"active": False, "expires_at": None, "hours_remaining": 0}

    expires_str = data.get("expires_at")
    if not expires_str:
        return {"active": False, "expires_at": None, "hours_remaining": 0}

    try:
        expires = datetime.fromisoformat(expires_str.replace("Z", "+00:00"))
    except Exception:
        return {"active": False, "expires_at": None, "hours_remaining": 0}

    now = datetime.now(timezone.utc)
    if now >= expires:
        # Expired — clean up so /spend doesn't keep showing a
        # ghost YOLO. Best-effort.
        try:
            path.unlink()
        except Exception:
            pass
        return {"active": False, "expires_at": expires_str, "hours_remaining": 0}

    delta = expires - now
    return {
        "active": True,
        "expires_at": expires_str,
        "hours_remaining": round(delta.total_seconds() / 3600, 2),
        "enabled_at": data.get("enabled_at"),
        "hours": data.get("hours"),
        "actor": data.get("actor"),
    }


def is_yolo_active() -> bool:
    return yolo_status()["active"]


def yolo_enable(hours: int, actor: str = "user") -> dict[str, Any]:
    """Activate YOLO mode for `hours` hours.

    Returns the resulting yolo_status() shape, or {"ok": False,
    "error": ...} on rejection (out of range / write failure).
    """
    if not isinstance(hours, (int, float)) or hours <= 0:
        return {"ok": False, "error": f"hours must be positive (got {hours})"}
    if hours > _YOLO_MAX_HOURS:
        return {
            "ok": False,
            "error": (
                f"hours capped at {_YOLO_MAX_HOURS} ({_YOLO_MAX_HOURS // 24} "
                f"days); requested {hours}"
            ),
        }
    now = datetime.now(timezone.utc)
    expires = now + __import__("datetime").timedelta(hours=hours)
    payload = {
        "enabled_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "hours": hours,
        "actor": actor,
    }
    path = _yolo_flag_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.replace(path)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    logger.warning(
        "YOLO ENABLED: actor=%s for %s hours (expires %s) — auto-pause disabled",
        actor, hours, expires.isoformat(),
    )
    return {"ok": True, **payload, "active": True}


def yolo_disable() -> dict[str, Any]:
    """End YOLO mode immediately. Idempotent."""
    path = _yolo_flag_path()
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        return {"ok": False, "error": str(e)}
    logger.info("YOLO DISABLED (was_active=%s)", existed)
    return {"ok": True, "was_active": existed}


def is_paused() -> bool:
    """Quick check at the top of agent_respond. File-based so the
    check is fast (one stat call, no DB)."""
    return _pause_flag_path().exists()


def pause_reason() -> dict[str, Any]:
    """Read the pause flag's reason payload.

    Flag file format is JSON: {"ts": "...", "reason": "...", "actor": "..."}.
    A torn / hand-edited flag still counts as paused but reason will
    be the literal file contents.
    """
    path = _pause_flag_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"raw": path.read_text()[:500]}


def pause(reason: str = "manual", actor: str = "user") -> dict[str, Any]:
    """Write the pause flag. Atomic (.tmp + rename) so a torn write
    can't leave a half-flag mid-pause."""
    path = _pause_flag_path()
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "actor": actor,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload) + "\n")
        tmp.replace(path)
        logger.warning(
            "PAUSED: actor=%s reason=%s — LLM calls blocked until /resume",
            actor, reason,
        )
        return {"ok": True, **payload}
    except Exception as e:
        logger.error("pause flag write failed: %s", e)
        return {"ok": False, "error": str(e)}


def resume() -> dict[str, Any]:
    """Delete the pause flag. Best-effort — missing flag is a no-op."""
    path = _pause_flag_path()
    existed = path.exists()
    try:
        path.unlink(missing_ok=True)
    except Exception as e:
        logger.warning("resume failed to delete flag: %s", e)
        return {"ok": False, "error": str(e)}
    logger.info("RESUMED: pause flag cleared (existed=%s)", existed)
    return {"ok": True, "was_paused": existed}


# ── Burn rate (rolling windows) ────────────────────────────────────


def get_burn_rate(
    db: "Database",
    window_minutes: int = 60,
) -> dict[str, Any]:
    """Token + cost burn over the last `window_minutes`, broken out
    by provider.

    Provider is inferred from the model prefix (gpt → openai,
    claude → anthropic, grok → xai, gemini → google, deepseek →
    deepseek, mistral → mistral, anything else → other). This is
    the same heuristic the provider chain uses so the breakdown
    matches the operator's mental model.

    Returns:
        {
          "window_minutes": 60,
          "total_calls":     N,
          "total_input_tokens":  N,
          "total_output_tokens": N,
          "total_cost_usd":      0.NN,
          "by_provider": {
            "anthropic": {"calls": ..., "tokens_in": ..., "tokens_out": ..., "cost_usd": ...},
            "openai":    {...},
            ...
          },
          "estimated_hourly_burn_usd": 0.NN,
        }
    """
    cutoff = (
        datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=window_minutes)
    ).strftime("%Y-%m-%d %H:%M:%S")
    try:
        rows = db.fetchall(
            """
            SELECT model, input_tokens, output_tokens, cost_usd
            FROM cost_ledger
            WHERE created_at >= ?
            """,
            (cutoff,),
        )
    except Exception as e:
        logger.warning("burn-rate query failed: %s", e)
        rows = []

    by_provider: dict[str, dict[str, float]] = {}
    total_in = 0
    total_out = 0
    total_cost = 0.0
    for r in rows:
        model = (r.get("model") or "").lower()
        provider = _provider_for_model(model)
        slot = by_provider.setdefault(
            provider,
            {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0},
        )
        slot["calls"] += 1
        slot["tokens_in"] += int(r.get("input_tokens") or 0)
        slot["tokens_out"] += int(r.get("output_tokens") or 0)
        slot["cost_usd"] += float(r.get("cost_usd") or 0.0)
        total_in += int(r.get("input_tokens") or 0)
        total_out += int(r.get("output_tokens") or 0)
        total_cost += float(r.get("cost_usd") or 0.0)

    hourly = total_cost * (60.0 / max(1, window_minutes))

    return {
        "window_minutes": window_minutes,
        "total_calls": len(rows),
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_cost_usd": round(total_cost, 4),
        "by_provider": {
            p: {
                "calls": s["calls"],
                "tokens_in": s["tokens_in"],
                "tokens_out": s["tokens_out"],
                "cost_usd": round(s["cost_usd"], 4),
            }
            for p, s in by_provider.items()
        },
        "estimated_hourly_burn_usd": round(hourly, 4),
    }


def get_spend_summary(db: "Database") -> dict[str, Any]:
    """Multi-window spend snapshot for the /spend command.

    Includes pause + YOLO status so a single call answers "is the
    bot currently spending money, and is auto-pause armed?" without
    multiple round-trips.
    """
    return {
        "paused": is_paused(),
        "pause_info": pause_reason() if is_paused() else None,
        "yolo": yolo_status(),
        "last_5_min":  get_burn_rate(db, window_minutes=5),
        "last_hour":   get_burn_rate(db, window_minutes=60),
        "last_day":    get_burn_rate(db, window_minutes=24 * 60),
    }


def _provider_for_model(model: str) -> str:
    """Same prefix → provider mapping the chain uses."""
    m = model.lower()
    for prefix, provider in (
        ("gpt", "openai"),
        ("o1", "openai"),
        ("o3", "openai"),
        ("claude", "anthropic"),
        ("grok", "xai"),
        ("gemini", "google"),
        ("deepseek", "deepseek"),
        ("mistral", "mistral"),
        ("llama", "ollama"),
        ("kimi", "moonshot"),
    ):
        if m.startswith(prefix):
            return provider
    return "other"


# ── Auto-pause threshold check ─────────────────────────────────────


def maybe_auto_pause(
    db: "Database",
    *,
    threshold_usd_per_hour: float | None = None,
) -> dict[str, Any]:
    """Heartbeat hook — check burn threshold and pause if breached.

    The "8 agents and one is in a zombie loop" scenario solved
    automatically: bots heartbeat every 5 minutes; if the bot's
    own 15-minute burn rate extrapolates to >$5/hr (configurable),
    it pauses ITSELF and the owner gets a DM. Zombie loops
    self-quarantine.

    Returns a structured payload describing the action taken so the
    caller (telegram_bot._heartbeat_loop) can DM the owner with the
    right context.

    Possible action values:
      "noop:already_paused"    — already paused; nothing to do
      "noop:below_threshold"   — burn rate fine; nothing to do
      "paused"                 — threshold breached; we paused
    """
    if is_paused():
        return {"action": "noop:already_paused"}

    if is_yolo_active():
        # YOLO mode — user explicitly opted out of auto-pause for
        # a bounded window. The heartbeat keeps surfacing the
        # status in /spend so they see it; auto-pause stays muzzled
        # until expiry.
        status = yolo_status()
        return {
            "action": "noop:yolo",
            "yolo_hours_remaining": status.get("hours_remaining", 0),
            "yolo_expires_at": status.get("expires_at"),
        }

    breach = check_burn_threshold(
        db, threshold_usd_per_hour=threshold_usd_per_hour,
    )
    if not breach.get("breach"):
        return {
            "action": "noop:below_threshold",
            "current_hourly": breach.get("current_hourly"),
            "threshold": breach.get("threshold"),
        }

    reason = (
        f"auto: burn rate ${breach['current_hourly']:.2f}/hr "
        f"exceeds ${breach['threshold']:.2f}/hr threshold"
    )
    pause(reason=reason, actor="auto")
    return {
        "action": "paused",
        "current_hourly": breach["current_hourly"],
        "threshold": breach["threshold"],
        "by_provider": breach.get("by_provider", {}),
    }


def check_burn_threshold(
    db: "Database",
    threshold_usd_per_hour: float | None = None,
) -> dict[str, Any]:
    """Compare current burn rate to the threshold. Used by the
    heartbeat loop to auto-pause on runaway spend.

    Default threshold from env ``WINDY_BURN_AUTOPAUSE_USD_PER_HOUR``
    (default 5.0). Set to 0 to disable auto-pause.
    """
    if threshold_usd_per_hour is None:
        threshold_usd_per_hour = float(
            os.environ.get("WINDY_BURN_AUTOPAUSE_USD_PER_HOUR", "5.0")
        )
    if threshold_usd_per_hour <= 0:
        return {"breach": False, "reason": "disabled"}

    rate = get_burn_rate(db, window_minutes=15)
    hourly = rate["estimated_hourly_burn_usd"]
    if hourly < threshold_usd_per_hour:
        return {
            "breach": False,
            "current_hourly": hourly,
            "threshold": threshold_usd_per_hour,
        }
    return {
        "breach": True,
        "current_hourly": hourly,
        "threshold": threshold_usd_per_hour,
        "by_provider": rate["by_provider"],
    }
