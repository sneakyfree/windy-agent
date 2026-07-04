"""Channel-agnostic rescue-command EXECUTION layer.

``channels/slash_commands.py`` is the recognition layer; until Sprint 2
its only consumer was telegram_bot, so the grandma rescue kit —
``/pause``, ``/resume``, ``/resurrect``, ``/normal``, ``/lifeboat``,
``/spend``, ``/auto-resurrect``, and the ``/reset`` panic button — did
not exist on Discord, Slack, Matrix, Signal, Teams, IRC, or WhatsApp
(2026-07-04 audit). A wedged agent on those channels had no escape
hatch at all.

This module executes the rescue commands with the SAME side-effect
primitives telegram uses (spend_monitor, resurrect) and plain-text
acks that render acceptably on every platform. ``handle_incoming``
consults it BEFORE the command registry, so rescue works even where
richer per-channel handlers don't exist. Telegram keeps its own
bespoke handlers (richer formatting, chat-id-aware greetings) — they
short-circuit before this layer ever runs.

Restart semantics: the panic path schedules a SIGTERM to our own
process after the ack is returned; under the standard systemd/launchd
deployment (Restart=always) that is a clean self-heal, mirroring
telegram's ``_trigger_self_restart``. Tests neuter ``schedule_restart``
via an autouse fixture (same pattern as ``kill_by_name``).
"""

from __future__ import annotations

import logging
import os
import signal
import threading

from windyfly.channels import slash_commands as sc

logger = logging.getLogger(__name__)

_RESTART_DELAY_S = 2.0


def schedule_restart(reason: str) -> None:
    """SIGTERM ourselves shortly — after the ack has been sent."""
    logger.warning("rescue: scheduling self-restart (%s)", reason)

    def _die() -> None:
        os.kill(os.getpid(), signal.SIGTERM)

    t = threading.Timer(_RESTART_DELAY_S, _die)
    t.daemon = True
    t.start()


def _spend_reply() -> str:
    from windyfly.agent.spend_monitor import get_spend_summary
    from windyfly.commands import core as _core

    db = getattr(_core, "_db", None)
    if db is None:
        return (
            "💳 I can't reach the cost ledger from this channel right "
            "now — try /status for overall health."
        )
    s = get_spend_summary(db)
    day = s.get("last_day") or {}
    hour = s.get("last_hour") or {}
    lines = ["💳 Spending"]
    if s.get("paused"):
        lines.append("⏸ Paused — not spending anything right now.")
    lines.append(
        f"Last 24h: ${day.get('total_cost_usd', 0.0):.2f} "
        f"({day.get('total_calls', 0)} calls)"
    )
    lines.append(f"Last hour: ${hour.get('total_cost_usd', 0.0):.2f}")
    lines.append("Say /pause to stop all spending instantly.")
    return "\n".join(lines)


def _resurrect_reply(actor: str) -> str:
    from windyfly.agent.resurrect import resurrect, resurrection_state

    existing = resurrection_state()
    if existing.get("active"):
        model = existing.get("model") or "(unknown)"
        return (
            f"🛟 Lifeboat is already on — running on local model "
            f"{model}. Memory intact. Say /normal to switch back."
        )
    prev_model = os.environ.get("DEFAULT_MODEL") or "your usual model"
    result = resurrect(actor=actor, previous_model=str(prev_model))
    if result.get("ok"):
        return (
            f"🛟 Lifeboat mode activated. I switched to a free local "
            f"model ({result.get('model')}). Memory and personality "
            f"intact — quality will be a bit lower until your usual "
            f"model works again. Say /normal to switch back."
        )
    if result.get("reason") == "ollama_not_running":
        return (
            "🆘 I tried to switch to a free local model but Ollama "
            "isn't running on my host. Run this once there, then say "
            f"/resurrect again:\n{result.get('install_hint', '')}"
        )
    if result.get("reason") == "no_models_installed":
        return (
            "🆘 Ollama is installed but has no models. Pull one and "
            f"try again:\n{result.get('install_hint', '')}"
        )
    return (
        f"⚠ Couldn't enter lifeboat mode: "
        f"{result.get('error') or result.get('reason') or 'unknown'}."
    )


def _normal_reply() -> str:
    from windyfly.agent.resurrect import normalize

    result = normalize()
    if not result.get("ok"):
        return (
            f"⚠ Couldn't clear the lifeboat flag: "
            f"{result.get('error', 'unknown')}. Try /reset."
        )
    if result.get("was_resurrected"):
        normal_model = os.environ.get("DEFAULT_MODEL") or "your usual brain"
        return (
            f"✨ Back to normal — using {normal_model} again. "
            f"If it ever fails, say /resurrect."
        )
    return "I wasn't in lifeboat mode — already on my usual brain."


def _auto_resurrect_reply(arg: str | None) -> str:
    from windyfly.agent.resurrect import (
        is_auto_resurrect_disabled, set_auto_resurrect,
    )

    if arg == "invalid":
        return "Usage: /auto-resurrect on|off|status"
    if arg in ("on", "off"):
        set_auto_resurrect(arg == "on", actor="rescue-layer")
        return (
            "🛟 Auto-resurrect is ON — if my paid brain dies I'll "
            "switch to a local model automatically."
            if arg == "on"
            else "🛟 Auto-resurrect is OFF — I'll wait for you to say "
            "/resurrect instead of switching automatically."
        )
    enabled = not is_auto_resurrect_disabled()
    return (
        f"🛟 Auto-resurrect is {'ON' if enabled else 'OFF'}. "
        "Change with /auto-resurrect on|off."
    )


def _panic_reply(platform: str, channel_id: str | None) -> str:
    # Same recovery semantics as telegram's panic handler: clear the
    # lifeboat flag (a /reset that leaves lifeboat on strands every
    # reply on a timing-out local model — 2026-05-10 incident), leave
    # pause/yolo/guest flags alone (explicit user state), greet after
    # the restart, then SIGTERM under the supervisor.
    try:
        from windyfly.agent.resurrect import normalize
        normalize()
    except Exception as e:
        logger.warning("rescue panic: failed to clear resurrect flag: %s", e)
    try:
        from windyfly.observability.restart_greeting import set_pending_greeting
        if channel_id:
            set_pending_greeting(
                chat_id=str(channel_id),
                platform=platform,
                reason="panic_reset",
            )
    except Exception as e:
        logger.debug("rescue panic: pending greeting not set: %s", e)
    schedule_restart(f"panic via {platform}")
    return (
        "🆘 Full reset, coming right up. I'll restart with a clean "
        "slate — give me about 30 seconds, then say hi."
    )


def try_rescue(
    text: str | None,
    *,
    platform: str = "unknown",
    channel_id: str | None = None,
    actor: str = "channel-user",
) -> str | None:
    """Execute a rescue command if ``text`` is one; else return None.

    Returns the plain-text ack to send back to the user. Pure-sync and
    fast (file flags + one DB read for /spend).
    """
    if not text:
        return None

    if sc.is_pause_message(text):
        from windyfly.agent.spend_monitor import pause as pause_spending
        result = pause_spending(
            reason=f"user requested via /pause ({platform})", actor=actor,
        )
        if result.get("ok"):
            return (
                "⏸ Paused. I won't make any LLM calls until you say "
                "/resume. I'll still answer /resume, /reset, and /spend."
            )
        return "⚠ Couldn't write the pause flag — try /reset instead."

    if sc.is_resume_message(text):
        from windyfly.agent.spend_monitor import resume as resume_spending
        result = resume_spending()
        if result.get("ok"):
            return (
                "▶️ Awake — I'm thinking again. What can I help with?"
                if result.get("was_paused")
                else "I wasn't paused — just say what you need."
            )
        return "⚠ Couldn't clear the pause flag — try /reset."

    if sc.is_spend_message(text):
        try:
            return _spend_reply()
        except Exception as e:
            logger.warning("rescue /spend failed: %s", e)
            return "⚠ Couldn't read the cost ledger just now."

    if sc.is_lifeboat_status_message(text):
        from windyfly.agent.resurrect import format_lifeboat_status
        return format_lifeboat_status()

    if sc.is_resurrect_message(text):
        return _resurrect_reply(actor)

    if sc.is_normal_message(text):
        return _normal_reply()

    is_ar, ar_arg = sc.parse_auto_resurrect_command(text)
    if is_ar:
        return _auto_resurrect_reply(ar_arg)

    if sc.is_panic_message(text):
        return _panic_reply(platform, channel_id)

    return None
