"""Format the Sunday-evening cumulative recap.

Different angle from the morning brief:
  - Morning brief: how organs FEEL right now + recommendations
  - Evening recap: cumulative trend over the week + what we did

Reads recent scorecards + the bot's episode/agent_actions DB to
build a backward-looking summary. Like a personal assistant doing
year-in-review, but weekly.

Outputs Markdown. Empty string if there's literally nothing to
report (e.g. no scorecards yet).
"""

from __future__ import annotations

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

_AGENT_SRC = os.environ.get("_AGENT_SRC")
if _AGENT_SRC and os.path.isdir(_AGENT_SRC):
    sys.path.insert(0, _AGENT_SRC)

from windyfly.agent.capabilities.health import _load_snapshots


def _bot_db_path() -> str | None:
    """Where the live bot's DB lives. Read-only access — we never
    write to it from the recap."""
    candidates = [
        os.environ.get("WINDY_BOT_DB"),
        "/home/grantwhitmer/.local/share/windyfly/agent/data/windy-0.db",
    ]
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def _query_one(con: sqlite3.Connection, sql: str, *args) -> int:
    try:
        cur = con.execute(sql, args)
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def _bot_stats() -> dict:
    """Read bot's DB for the last 7 days of activity. Returns
    safe-zero dict if DB is unavailable so the recap never crashes."""
    db_path = _bot_db_path()
    if not db_path:
        return {"ok": False, "reason": "bot DB not found"}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
    except Exception as e:
        return {"ok": False, "reason": f"connect failed: {e}"}

    seven_days_ago = (
        datetime.now(timezone.utc) - timedelta(days=7)
    ).isoformat()

    try:
        episodes_total = _query_one(
            con,
            "SELECT COUNT(*) FROM episodes WHERE created_at >= ?",
            seven_days_ago,
        )
        user_turns = _query_one(
            con,
            "SELECT COUNT(*) FROM episodes "
            "WHERE created_at >= ? AND role = 'user'",
            seven_days_ago,
        )
        sessions_active = _query_one(
            con,
            "SELECT COUNT(DISTINCT session_id) FROM episodes "
            "WHERE created_at >= ?",
            seven_days_ago,
        )
        # agent_actions may not exist on all instances; guard
        try:
            tools_used = _query_one(
                con,
                "SELECT COUNT(*) FROM agent_actions WHERE created_at >= ?",
                seven_days_ago,
            )
        except Exception:
            tools_used = 0
        nodes_total = _query_one(con, "SELECT COUNT(*) FROM nodes")
    finally:
        con.close()

    return {
        "ok": True,
        "user_turns_7d": user_turns,
        "episodes_7d": episodes_total,
        "sessions_active_7d": sessions_active,
        "tools_used_7d": tools_used,
        "nodes_total": nodes_total,
    }


def _trend_summary(snapshots: list[dict], days: int = 28) -> str:
    """Verdict-count rollup over the last N days of scorecards."""
    if not snapshots:
        return ""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=days)
    ).isoformat()
    recent = [s for s in snapshots if (s.get("ts") or "") >= cutoff]
    if not recent:
        recent = snapshots

    totals = {"green": 0, "yellow": 0, "red": 0}
    for s in recent:
        counts = s.get("verdict_counts") or {}
        for k in totals:
            totals[k] += counts.get(k, 0)
    total = sum(totals.values())
    if total == 0:
        return ""
    pct = {k: round(v / total * 100, 1) for k, v in totals.items()}
    return (
        f"Over the last {days} days across {len(recent)} checkups: "
        f"🟢 {pct['green']:.0f}% green, "
        f"🟡 {pct['yellow']:.0f}% yellow, "
        f"🔴 {pct['red']:.0f}% red."
    )


def main() -> int:
    snapshots = _load_snapshots(limit=50)
    bot = _bot_stats()

    # If no data at all, stay silent.
    has_anything = bool(snapshots) or bot.get("ok")
    if not has_anything:
        return 0

    lines: list[str] = ["🌙 *Sunday Evening — Week in Review*"]
    lines.append("")

    if bot.get("ok"):
        lines.append("*This week with you:*")
        lines.append(
            f"💬 {bot['user_turns_7d']} message{'s' if bot['user_turns_7d'] != 1 else ''}"
            f" you sent me"
        )
        if bot["sessions_active_7d"] > 0:
            lines.append(
                f"🧵 {bot['sessions_active_7d']} conversation thread"
                f"{'s' if bot['sessions_active_7d'] != 1 else ''}"
            )
        if bot["tools_used_7d"] > 0:
            lines.append(f"🛠 {bot['tools_used_7d']} tool calls I made for you")
        if bot["nodes_total"] > 0:
            lines.append(
                f"🧠 {bot['nodes_total']} facts I remember about you so far"
            )
        lines.append("")

    trend = _trend_summary(snapshots)
    if trend:
        lines.append("*How I have been holding up:*")
        lines.append(trend)
        lines.append("")

    lines.append("Thanks for spending the week with me. See you tomorrow.")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
