"""Core commands — work in both HiFly and Windy Fly."""

import os
import sys
import time
import logging
import platform
import subprocess
from pathlib import Path
from datetime import datetime, timezone

from windyfly.commands.registry import Command, registry
from windyfly.platform import get_project_root

logger = logging.getLogger(__name__)
PROJECT_ROOT = get_project_root()

_db = None
_config = None
_channel_manager = None
_init_time: float = 0.0


def init_core(db=None, config=None, channel_manager=None):
    global _db, _config, _channel_manager, _init_time
    _db = db
    _config = config
    _channel_manager = channel_manager
    _init_time = time.time()
    _register_all()


def wire_runtime(db=None, channel_manager=None) -> None:
    """Inject runtime objects after init_core ran.

    init_all_commands is called early in main() before the channel
    branch creates the Database / ChannelManager. Channels that need
    /pulse to see live state should call wire_runtime once they've
    finished setting things up.
    """
    global _db, _channel_manager
    if db is not None:
        _db = db
    if channel_manager is not None:
        _channel_manager = channel_manager


def _r(name, desc, cat, handler, aliases=None, dangerous=False, usage=""):
    registry.register(Command(
        name=name, description=desc, category=cat,
        handler=handler, aliases=aliases or [], dangerous=dangerous, usage=usage,
    ))


def _register_all():
    # ═══════════════════════════════════════════════════════════════
    # PROCESS & LIFECYCLE (1-10)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_go(ctx):
        return "Run 'windy go' from terminal to hatch. Cannot hatch from inside a chat session."
    _r("go", "One-command quickstart — hatch a new agent", "01_process", cmd_go)

    async def cmd_start(ctx):
        pid_file = Path("data/windyfly.pid")
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text().strip().split("\n")[0].split("=")[1])
                os.kill(pid, 0)
                return f"Windy Fly is already running (PID {pid}). Use /restart or /stop first."
            except (OSError, ValueError):
                pid_file.unlink(missing_ok=True)
        return "Run 'windy start' from terminal to start the agent stack."
    _r("start", "Start the agent (brain + gateway)", "01_process", cmd_start)

    async def cmd_stop(ctx):
        # Under systemd, pkill triggers Restart=on-failure and the
        # agent revives in RestartSec=10. systemctl stop is the only
        # path that actually stays stopped.
        from windyfly.platform import find_systemd_unit_for_pattern, systemctl_stop
        info = find_systemd_unit_for_pattern("windyfly.main")
        if info is not None:
            ok, msg = systemctl_stop(info)
            scope_flag = "--user " if info.scope == "user" else ""
            if ok:
                return f"Stopped via `systemctl {scope_flag}stop {info.unit}`."
            # Fall through to legacy paths if systemctl failed for any reason.
            logger.warning(
                "systemctl stop %s failed (%s) — falling back to pid/pkill",
                info.unit, msg,
            )

        pid_file = Path("data/windyfly.pid")
        if pid_file.exists():
            try:
                content = pid_file.read_text()
                for line in content.strip().split("\n"):
                    if "=" in line:
                        pid = int(line.split("=")[1])
                        try:
                            os.kill(pid, 15)
                        except OSError:
                            pass
                pid_file.unlink(missing_ok=True)
                return "Windy Fly stopped."
            except Exception as e:
                return f"Error stopping: {e}"
        os.system("pkill -f 'windyfly' 2>/dev/null")
        return "Stop signal sent. No PID file found — used fallback kill."
    _r("stop", "Stop all Windy Fly processes", "01_process", cmd_stop)

    async def cmd_restart(ctx):
        await cmd_stop(ctx)
        return "Stopped. Run 'windy start' from terminal to restart."
    _r("restart", "Stop + start in one shot", "01_process", cmd_restart)

    async def cmd_kill(ctx):
        # Same systemd-aware logic as cmd_stop — `windy kill` was
        # originally pkill -9, which triggers RestartSec=10 and
        # revives the agent. systemctl stop sends SIGTERM and
        # escalates to SIGKILL after TimeoutStopSec, *and* marks the
        # unit inactive so no restart fires.
        from windyfly.platform import find_systemd_unit_for_pattern, systemctl_stop
        info = find_systemd_unit_for_pattern("windyfly.main")
        if info is not None:
            ok, msg = systemctl_stop(info, timeout=10)
            if ok:
                return (
                    f"Killed via `systemctl{' --user' if info.scope == 'user' else ''} "
                    f"stop {info.unit}`. To prevent auto-restart at boot, also run: "
                    f"`systemctl{' --user' if info.scope == 'user' else ''} disable {info.unit}`."
                )
            logger.warning(
                "systemctl stop %s failed (%s) — falling back to pkill -9",
                info.unit, msg,
            )

        os.system("pkill -9 -f 'windyfly' 2>/dev/null")
        Path("data/windyfly.pid").unlink(missing_ok=True)
        Path("data/windyfly.lock").unlink(missing_ok=True)
        return "All Windy Fly processes force-killed. Lock files removed."
    _r("kill", "Force-kill everything (emergency)", "01_process", cmd_kill, dangerous=True)

    async def cmd_ps(ctx):
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        lines = [line for line in result.stdout.split("\n") if "windyfly" in line.lower() and "grep" not in line]
        if not lines:
            return "No Windy Fly processes running."
        return "Running processes:\n" + "\n".join(lines)
    _r("ps", "Show running Windy Fly processes", "01_process", cmd_ps, aliases=["processes"])

    async def cmd_update(ctx):
        from windyfly.update import check_for_update, apply_update
        info = check_for_update(force=True)
        if info is None:
            return "Already on latest version."
        success, message = apply_update()
        return message
    _r("update", "Update to latest version from PyPI", "01_process", cmd_update, aliases=["upgrade"])

    async def cmd_version(ctx):
        from windyfly import __version__
        ver = __version__
        model = os.environ.get("DEFAULT_MODEL", "not set")
        budget = os.environ.get("DAILY_BUDGET_USD", "5.00")
        return (f"🪰 Windy Fly v{ver}\n"
                f"Python {platform.python_version()} | {platform.system()} {platform.machine()}\n"
                f"Model: {model} | Budget: ${budget}/day")
    _r("version", "Show version, Python, OS, architecture", "01_process", cmd_version, aliases=["ver", "v"])

    async def cmd_uptime(ctx):
        pid_file = Path("data/windyfly.pid")
        if pid_file.exists():
            try:
                for line in pid_file.read_text().strip().split("\n"):
                    if line.startswith("started="):
                        start = datetime.fromisoformat(line.split("=", 1)[1])
                        delta = datetime.now(timezone.utc) - start
                        hours = int(delta.total_seconds() // 3600)
                        mins = int((delta.total_seconds() % 3600) // 60)
                        return f"Uptime: {hours}h {mins}m"
            except Exception as e:
                logger.debug("Uptime parse failed: %s", e)
        return "Agent is not running (no PID file)."
    _r("uptime", "Show how long the agent has been running", "01_process", cmd_uptime)

    async def cmd_shutdown(ctx):
        return "SHUTDOWN"
    _r("shutdown", "Graceful shutdown with state save", "01_process", cmd_shutdown)

    # ═══════════════════════════════════════════════════════════════
    # DIAGNOSTICS (11-18)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_doctor(ctx):
        lines = ["🩺 Windy Fly Doctor\n"]
        lines.append("Environment:")
        lines.append(f"  {'✓' if sys.version_info >= (3, 12) else '✗'} Python {platform.python_version()}")
        lines.append(f"  {'✓' if subprocess.run(['which', 'uv'], capture_output=True).returncode == 0 else '✗'} uv")
        lines.append(f"  {'✓' if subprocess.run(['which', 'bun'], capture_output=True).returncode == 0 else '○'} Bun (optional — for gateway)")
        lines.append(f"  {'✓' if subprocess.run(['which', 'node'], capture_output=True).returncode == 0 else '○'} Node.js (fallback for gateway)")
        lines.append("\nConfiguration:")
        lines.append(f"  {'✓' if Path('windyfly.toml').exists() else '✗'} windyfly.toml")
        lines.append(f"  {'✓' if Path('.env').exists() else '✗'} .env")
        lines.append(f"  {'✓' if Path('SOUL.md').exists() else '✗'} SOUL.md")
        api_key = bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
        lines.append(f"  {'✓' if api_key else '✗'} LLM API key ({'set' if api_key else 'MISSING — agent cannot respond'})")
        lines.append(f"  ✓ DEFAULT_MODEL = {os.environ.get('DEFAULT_MODEL', 'not set')}")
        db_path = os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")
        lines.append("\nDatabase:")
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / 1024 / 1024
            lines.append(f"  ✓ {db_path} ({size_mb:.1f} MB)")
            if _db:
                try:
                    from windyfly.memory.nodes import count_nodes
                    from windyfly.memory.episodes import count_episodes
                    nodes = count_nodes(_db)
                    episodes = count_episodes(_db)
                    lines.append(f"  ✓ {nodes} nodes, {episodes} episodes")
                except Exception:
                    lines.append("  ? Could not query database")
        else:
            lines.append(f"  ✗ {db_path} — NOT FOUND")
        lines.append("\nAudio:")
        sound_file = PROJECT_ROOT / "data" / "sounds" / "its-alive.wav"
        lines.append(f"  {'✓' if sound_file.exists() else '✗'} {sound_file}")
        lines.append("\nEcosystem:")
        import httpx
        for name, url in [
            ("Windy Pro", os.environ.get("WINDY_API_URL", "")),
            ("Eternitas", os.environ.get("ETERNITAS_API_URL", "")),
            ("Windy Mail", os.environ.get("WINDYMAIL_API_URL", "")),
        ]:
            if url:
                try:
                    r = httpx.get(f"{url}/health", timeout=5)
                    lines.append(f"  ✓ {name} ({url}) — {r.status_code}")
                except Exception:
                    lines.append(f"  ✗ {name} ({url}) — unreachable")
            else:
                lines.append(f"  ○ {name} — not configured")
        lines.append("\nChannels:")
        for ch_name, env_var in [("Matrix", "MATRIX_BOT_TOKEN"), ("Telegram", "TELEGRAM_BOT_TOKEN"),
                                  ("Discord", "DISCORD_BOT_TOKEN"), ("Slack", "SLACK_BOT_TOKEN")]:
            lines.append(f"  {'✓' if os.environ.get(env_var) else '○'} {ch_name}")
        lines.append("\nIdentity:")
        lines.append(f"  {'✓' if os.environ.get('ETERNITAS_PASSPORT') else '✗'} Eternitas passport")
        lines.append(f"  {'✓' if os.environ.get('WINDYMAIL_EMAIL') else '✗'} Windy Mail")
        lines.append(f"  {'✓' if os.environ.get('TWILIO_PHONE_NUMBER') else '✗'} Phone number")
        lines.append("\nProcess:")
        pid_file = Path("data/windyfly.pid")
        if pid_file.exists():
            lines.append("  ✓ Windy Fly is running (PID file exists)")
        else:
            lines.append("  ○ Windy Fly is NOT running")
        return "\n".join(lines)

    _r("doctor", "Full health check — env, config, DB, ecosystem, channels",
       "02_diagnostics", cmd_doctor, aliases=["health", "check", "diag"])

    async def cmd_status(ctx):
        """Grandma-friendly /status — emoji headers, plain-English
        memory state, OAuth fingerprint, per-model context cap.

        Designed so a non-technical operator can read it top-to-bottom
        and know everything that matters about their agent:
        health, memory, brain, billing, uptime, what it remembers
        about you, what tools it can use.

        See PR #194 + #195 for the full design rationale; PR #194
        was a developer-flavored first cut, this revision (PR #195)
        leans hard on plain-English labels per Grant's 2026-05-19
        feedback: "this is a great opportunity for a grandma or
        a normie to know everything that's going on with their
        agent."
        """
        from windyfly.agent.models import (
            get_anthropic_auth_path, get_context_cap,
        )

        # ── Model + degradation status ────────────────────────────
        # PR #198 — read the per-channel preference (set via /model)
        # FIRST. Pre-fix /status was reading os.environ DEFAULT_MODEL
        # directly, which lied to the user after a /model switch:
        # they'd see "Brain: claude-sonnet-4-6" even though the agent
        # loop was about to dispatch the next reply to claude-opus-4-7.
        # _resolve_active_model walks the same precedence the loop
        # uses (per-channel pref > env > config > default).
        platform_early = (ctx or {}).get("platform")
        channel_id_early = (ctx or {}).get("channel_id")
        configured_model = _resolve_active_model(
            platform_early, channel_id_early,
        )
        live_model = configured_model
        degraded_reason = None
        try:
            from windyfly.agent.resurrect import (
                is_resurrected, resurrection_state,
            )
            if is_resurrected():
                state = resurrection_state() or {}
                live_model = state.get("model") or "unknown"
                actor = state.get("actor") or "auto"
                degraded_reason = (
                    f"on {live_model} fallback ({actor})"
                )
        except Exception:
            pass

        # Health: visual signal at the top of the report.
        if degraded_reason:
            health_line = (
                f"🟡 Health: degraded — {degraded_reason}"
            )
        else:
            health_line = "🟢 Health: all good"

        # ── Memory (context-window) ──────────────────────────────
        # Plain-English descriptors so a normie understands at a
        # glance. The raw K/K numbers stay for the operator who
        # wants precision.
        # PR #198 — effective cap = per-channel pinned (/memory) cap
        # if set, else model native cap. Pre-fix /status used native
        # only, so /memory 1M on Sonnet still showed "200K" — exactly
        # the misleading screenshot Grant pushed back on.
        memory_line = None
        platform = (ctx or {}).get("platform")
        channel_id = (ctx or {}).get("channel_id")
        pinned_cap = None
        if platform and channel_id:
            try:
                from windyfly.agent.session_reset import (
                    next_session_id, get_memory_cap,
                )
                from windyfly.agent.loop import _session_tokens
                pinned_cap = get_memory_cap(platform, channel_id)
                sid = next_session_id(platform, channel_id)
                used = _session_tokens.get(sid, 0)
                native_cap = get_context_cap(live_model)
                max_ctx = (
                    pinned_cap if pinned_cap is not None else native_cap
                )
                pct_rem = (
                    max(0.0, 100.0 - (used / max_ctx) * 100)
                    if max_ctx else 0.0
                )
                # Plain-English descriptor band
                if pct_rem >= 90:
                    phrase, dot = "feels fresh", "🟢"
                elif pct_rem >= 70:
                    phrase, dot = "mostly free", "🟢"
                elif pct_rem >= 30:
                    phrase, dot = "moderate", "🟡"
                elif pct_rem >= 10:
                    phrase, dot = "getting full", "🟡"
                else:
                    phrase, dot = "nearly full — type /new", "🔴"
                # K vs M cap rendering
                if max_ctx >= 1_000_000:
                    cap_str = f"{max_ctx // 1_000_000}M"
                else:
                    cap_str = f"{max_ctx // 1000}K"
                memory_line = (
                    f"🧠 Memory: {phrase} — {pct_rem:.0f}% free "
                    f"({used/1000:.1f}K of {cap_str} used) {dot}"
                )
            except Exception:
                pass

        # ── Brain (model + effective context cap) ─────────────────
        # PR #198 — show the cap actually in effect for this channel
        # (pinned via /memory, else model native), not the raw model
        # native. Keeps Brain and Memory lines numerically aligned.
        native_cap = get_context_cap(live_model)
        effective_cap = (
            pinned_cap if pinned_cap is not None else native_cap
        )
        if effective_cap >= 1_000_000:
            cap_human = f"{effective_cap // 1_000_000}M context"
        else:
            cap_human = f"{effective_cap // 1000}K context"
        # Indicate when the cap is extended past native (so the user
        # knows the 1M-beta tier is active rather than thinking the
        # model itself is bigger now).
        if pinned_cap is not None and pinned_cap > native_cap:
            cap_human += " — extended tier"
        brain_line = f"🤖 Brain: {live_model} ({cap_human})"
        if degraded_reason:
            brain_line += f" — was {configured_model}"

        # ── Plan (auth + token fingerprint) ───────────────────────
        auth = get_anthropic_auth_path()
        plan_line = (
            f"💳 Plan: {auth['label_short']} — {auth['fingerprint']}"
        )

        # ── Uptime ────────────────────────────────────────────────
        uptime_line = None
        if _init_time:
            up = int(time.time() - _init_time)
            h, rem = divmod(up, 3600)
            m, s = divmod(rem, 60)
            uptime_line = (
                f"⏱️ Up: {h}h {m}m" if h else f"⏱️ Up: {m}m {s}s"
            )

        # ── Today's cost vs budget ────────────────────────────────
        # Honest framing on Max plan: the cost ledger logs estimated
        # cost on every call (input+output tokens × API03 per-token
        # rates) regardless of auth path. On OAuth Max that estimate
        # ISN'T actually billed — Anthropic bills flat $200/mo. So
        # /status used to show "$0.29 of $5.00 budget (Max = flat
        # rate)" which is cognitive friction: a non-zero number next
        # to "flat rate" reads as real spend.
        #
        # Surfaced 2026-05-19 by Grant after PR #195 went live —
        # /status said Max plan but also showed $0.29 today, which
        # made him wonder if a cost-leak slipped through PR #189.
        # It didn't (the ledger just estimates everything); fix is
        # to split the line into "$0 actually billed" + "~$X token
        # estimate" so the headline number is truthful AND the
        # capacity-planning estimate stays visible as a parenthetical
        # — and so a REAL cost leak would jump out (wildly higher
        # estimate would stand out against $0 billed).
        today_line = None
        try:
            from windyfly.memory.cost_ledger import get_daily_spend
            if _db:
                spend = get_daily_spend(_db)
                if auth["kind"] in ("oauth_manager", "oauth_api_key"):
                    today_line = (
                        f"💰 Today: $0 billed · ~${spend:.2f} token "
                        f"estimate (Max plan)"
                    )
                else:
                    budget = float(
                        ((_config or {}).get("costs") or {})
                        .get("daily_budget_usd", 5.0)
                    )
                    pct_used = (
                        (spend / budget) * 100 if budget else 0.0
                    )
                    today_line = (
                        f"💰 Today: ${spend:.2f} of ${budget:.2f} "
                        f"budget ({pct_used:.0f}% used)"
                    )
        except Exception:
            pass

        # ── Session info (rolling counter from PR #193) ───────────
        session_line = None
        if platform and channel_id:
            try:
                from windyfly.agent.session_reset import (
                    next_session_id, get_reset_count,
                )
                sid = next_session_id(platform, channel_id)
                resets = get_reset_count(platform, channel_id)
                plural = "s" if resets != 1 else ""
                session_line = (
                    f"✨ Session: {sid} ({resets} fresh start{plural})"
                )
            except Exception:
                pass

        # ── What I remember about you (node count) ────────────────
        facts_line = None
        try:
            if _db:
                row = _db.fetchone("SELECT COUNT(*) AS c FROM nodes")
                n = (row or {}).get("c", 0)
                facts_line = (
                    f"📚 Memory of you: {n} fact{'s' if n != 1 else ''}"
                )
        except Exception:
            pass

        # ── Tools I can use (capability registry count) ───────────
        tools_line = None
        try:
            from windyfly.agent.capabilities import capability_registry
            n = capability_registry.count()
            tools_line = (
                f"🛠️ Tools available: {n}"
            )
        except Exception:
            pass

        # ── Persistence / identity ────────────────────────────────
        db_path = os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")
        size = (
            f"{os.path.getsize(db_path)/1024/1024:.1f}MB"
            if os.path.exists(db_path) else "no db"
        )
        passport = os.environ.get("ETERNITAS_PASSPORT", "none")
        email = os.environ.get("WINDYMAIL_EMAIL", "none")

        # ── Compose ───────────────────────────────────────────────
        lines = ["🪰 Windy Fly status", "", health_line]
        if memory_line:
            lines.append(memory_line)
        lines.append(brain_line)
        lines.append(plan_line)
        if uptime_line:
            lines.append(uptime_line)
        if today_line:
            lines.append(today_line)
        if session_line:
            lines.append(session_line)
        if facts_line:
            lines.append(facts_line)
        if tools_line:
            lines.append(tools_line)
        lines.extend(["", f"📂 Memory file: {size}",
                      f"🪪 Passport: {passport}",
                      f"📧 Email: {email}"])
        return "\n".join(lines)
    _r("status", "Quick status summary", "02_diagnostics", cmd_status, aliases=["info"])

    async def cmd_pulse(ctx):
        """Live runtime diagnostics — what /doctor checks at config time,
        /pulse checks at runtime. Use this when something feels off and
        you want to know whether the agent is actually working right
        now."""
        lines = ["🪰 Pulse\n"]

        # Uptime
        if _init_time:
            up = int(time.time() - _init_time)
            h, rem = divmod(up, 3600)
            m, s = divmod(rem, 60)
            lines.append(f"Uptime: {h}h {m}m {s}s")
        else:
            lines.append("Uptime: unknown (init time not recorded)")

        # DB writability — actually try a write so we catch lock /
        # WAL / disk-full issues that env-var checks miss.
        db_path = (_config or {}).get("memory", {}).get(
            "db_path",
            os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db"),
        )
        if os.path.exists(db_path):
            size_mb = os.path.getsize(db_path) / 1024 / 1024
            db_line = f"DB: {db_path} ({size_mb:.2f}MB)"
            if _db is not None:
                try:
                    t0 = time.time()
                    _db.execute(
                        "CREATE TABLE IF NOT EXISTS _pulse_probe (ts REAL)"
                    )
                    _db.execute(
                        "INSERT INTO _pulse_probe (ts) VALUES (?)", (time.time(),),
                    )
                    _db.execute("DELETE FROM _pulse_probe")
                    elapsed_ms = (time.time() - t0) * 1000
                    db_line += f" — writable in {elapsed_ms:.1f}ms"
                except Exception as e:
                    db_line += f" — WRITE FAILED: {e}"
            lines.append(db_line)
        else:
            lines.append(f"DB: {db_path} — MISSING")

        # Memory contents
        if _db is not None:
            try:
                eps = _db.fetchone("SELECT COUNT(*) AS n FROM episodes")
                nodes = _db.fetchone("SELECT COUNT(*) AS n FROM nodes")
                intents = _db.fetchone(
                    "SELECT COUNT(*) AS n FROM intents WHERE status = 'active'"
                )
                lines.append(
                    f"Memory: {eps['n']} episodes, {nodes['n']} nodes, "
                    f"{intents['n']} active intents"
                )
            except Exception as e:
                lines.append(f"Memory: query failed — {e}")

        # Last LLM call (from cost ledger)
        if _db is not None:
            try:
                last = _db.fetchone(
                    "SELECT model, cost_usd, input_tokens, output_tokens, created_at "
                    "FROM cost_ledger ORDER BY created_at DESC LIMIT 1"
                )
                if last:
                    age_row = _db.fetchone(
                        "SELECT CAST((julianday('now') - julianday(?)) * 86400 AS INTEGER) AS age",
                        (last["created_at"],),
                    )
                    age_s = age_row["age"] if age_row else -1
                    total_tokens = (last["input_tokens"] or 0) + (last["output_tokens"] or 0)
                    lines.append(
                        f"Last LLM: {last['model']} {age_s}s ago "
                        f"(${last['cost_usd']:.4f}, {total_tokens} tokens)"
                    )
                else:
                    lines.append("Last LLM: no calls yet this session")
            except Exception as e:
                lines.append(f"Last LLM: query failed — {e}")

        # Daily / monthly cost vs. budget
        if _db is not None:
            try:
                from windyfly.memory.cost_ledger import (
                    get_daily_spend, get_monthly_spend,
                )
                daily = get_daily_spend(_db)
                monthly = get_monthly_spend(_db)
                daily_budget = (_config or {}).get("costs", {}).get(
                    "daily_budget_usd", 5.0,
                )
                monthly_budget = (_config or {}).get("costs", {}).get(
                    "monthly_budget_usd", 50.0,
                )
                pct_d = (daily / daily_budget * 100) if daily_budget else 0
                pct_m = (monthly / monthly_budget * 100) if monthly_budget else 0
                lines.append(
                    f"Cost today:  ${daily:.4f} / ${daily_budget:.2f} ({pct_d:.0f}%)"
                )
                lines.append(
                    f"Cost month:  ${monthly:.4f} / ${monthly_budget:.2f} ({pct_m:.0f}%)"
                )
            except Exception as e:
                lines.append(f"Cost: query failed — {e}")

        # Channel connectivity
        if _channel_manager is not None:
            try:
                status = _channel_manager.status()
                if status:
                    parts = [
                        f"{name}={'up' if up else 'DOWN'}"
                        for name, up in status.items()
                    ]
                    lines.append(f"Channels: {', '.join(parts)}")
            except Exception as e:
                lines.append(f"Channels: query failed — {e}")

        # Provider cooldowns (circuit breaker state from agent.models)
        try:
            from windyfly.agent.models import _provider_cooldowns
            now = time.time()
            cooled = [
                (k, max(0, int(until - now)))
                for k, (until, _) in _provider_cooldowns.items()
                if until > now
            ]
            if cooled:
                parts = [f"{k}({s}s)" for k, s in cooled]
                lines.append(f"Provider cooldowns: {', '.join(parts)}")
            else:
                lines.append("Provider cooldowns: none")
        except Exception:
            # Failover module may not be on this branch yet — silent skip
            pass

        # Capability invocations in the last hour (Wave 2 #53 ledger).
        # When debugging "is the bot actually using the new tools?",
        # this answers the question instantly.
        if _db is not None:
            try:
                rows = _db.fetchall(
                    "SELECT capability_id, COUNT(*) AS n, "
                    "       SUM(CASE WHEN success=1 THEN 1 ELSE 0 END) AS ok "
                    "FROM agent_actions "
                    "WHERE started_at > datetime('now', '-1 hour') "
                    "GROUP BY capability_id ORDER BY n DESC LIMIT 10"
                )
                if rows:
                    parts = [
                        f"{r['capability_id']}({r['ok']}/{r['n']})"
                        for r in rows
                    ]
                    lines.append(f"Caps last hour: {', '.join(parts)}")
                else:
                    lines.append("Caps last hour: none invoked")
            except Exception as e:
                lines.append(f"Caps last hour: query failed — {e}")

        return "\n".join(lines)

    _r("pulse", "Live runtime diagnostics — DB, memory, last LLM, cost, channels",
       "02_diagnostics", cmd_pulse, aliases=["live"])

    async def cmd_caps(ctx):
        """List capabilities the agent can call.

        Filters by band — defaults to OWNER (every capability), but a
        future passport-aware ctx will pass the session's actual band so
        grandma's /caps shows only what grandma can use.
        """
        from windyfly.agent.capabilities import (
            Band,
            capability_registry,
        )

        # Resolve band from ctx if a future channel passes it; otherwise
        # default to OWNER so /caps shows the complete inventory.
        band_name = (ctx or {}).get("band", "OWNER")
        try:
            band = Band[band_name.upper()] if isinstance(band_name, str) else band_name
        except (KeyError, AttributeError):
            band = Band.OWNER

        caps = sorted(
            capability_registry.list_for_band(band),
            key=lambda c: (int(c.tier), c.id),
        )
        if not caps:
            return (
                f"No capabilities registered (band={band.name}).\n"
                "Capabilities register through capability_registry; the "
                "matrix channel installs them at startup. Check that the "
                "channel branch finished startup."
            )

        lines = [f"🪰 Capabilities ({len(caps)} for band {band.name})\n"]
        current_tier = None
        for c in caps:
            if c.tier != current_tier:
                lines.append(f"\n— Tier {int(c.tier)} ({c.tier.name}) —")
                current_tier = c.tier
            audit = "audited" if c.audit_required else "no-audit"
            req_band = c.band_required.name if c.band_required else "?"
            # Trim description to one line
            desc = (c.description or "").split("\n")[0][:80]
            lines.append(f"  {c.id}  [{req_band}, {audit}]\n    {desc}")
        return "\n".join(lines)

    # No aliases — "capabilities" is already taken by the legacy
    # cmd_capabilities below (returns HELP_TEXT), and "tools" risks
    # collision with future tool commands. /caps stays unique.
    _r("caps", "List capabilities the agent can call (Capability Plane)",
       "02_diagnostics", cmd_caps)

    async def cmd_seed_from_memory(ctx):
        """Import Grant's user-memory files into the bot's nodes table.

        One-shot operator command. Reads every ``.md`` file in
        ``~/.claude/projects/-Users-thewindstorm/memory/`` (configurable
        via ``WINDYFLY_USER_MEMORY_DIR``) and upserts each as a node with
        type ``memory.<frontmatter_type>``. Idempotent — re-running
        upserts existing nodes.

        Without this, a fresh windy-0 instance has zero domain context
        and collaborators confabulate (the AWS Polly TTS hallucination
        Grant caught when he said "Polly and rate sheets").
        """
        if _db is None:
            return (
                "Database not wired into the command registry — restart "
                "the bot or call wire_runtime(db=...) before /seed."
            )

        # Allow ``/seed dry-run`` to preview without writing
        args = (ctx or {}).get("_args", []) or []
        dry_run = any(a.lower() in ("dry", "dryrun", "dry-run", "preview") for a in args)

        from windyfly.memory.seed_from_user_memory import seed_from_user_memory
        result = seed_from_user_memory(_db, dry_run=dry_run)

        if "error" in result:
            return f"🪰 Seed failed\n{result['error']}\nmemory_dir={result['memory_dir']}"

        lines = [
            f"🪰 Seed {'(dry-run preview)' if dry_run else 'complete'}",
            f"  imported:  {result['imported']}",
            f"  skipped:   {result['skipped']}",
            f"  errors:    {result['errors']}",
            f"  memory_dir: {result['memory_dir']}",
        ]
        if result.get("imported_nodes"):
            lines.append("\nNodes:")
            for n in result["imported_nodes"][:20]:
                lines.append(f"  • {n}")
            extra = len(result["imported_nodes"]) - 20
            if extra > 0:
                lines.append(f"  ... and {extra} more")
        if result.get("error_details"):
            lines.append("\nErrors:")
            for e in result["error_details"][:5]:
                lines.append(f"  • {e['file']}: {e['error']}")
        return "\n".join(lines)

    _r("seed-from-memory",
       "Import Grant's user-memory files as nodes (one-shot)",
       "02_diagnostics", cmd_seed_from_memory,
       aliases=["seed", "seed-memory"])

    async def cmd_debug(ctx):
        lines = ["=== WINDY FLY DEBUG INFO ==="]
        lines.append(f"Python: {sys.version}")
        lines.append(f"Platform: {platform.platform()}")
        lines.append(f"Architecture: {platform.machine()}")
        lines.append(f"CWD: {os.getcwd()}")
        try:
            result = subprocess.run([sys.executable, "-m", "pip", "list", "--format=columns"],
                                    capture_output=True, text=True)
            pkg_count = len(result.stdout.strip().split("\n")) - 2
            lines.append(f"Installed packages: {pkg_count}")
        except Exception as e:
            logger.debug("Package count failed: %s", e)
        lines.append("\nEnvironment (redacted):")
        for key in sorted(os.environ):
            if any(k in key.upper() for k in ["WINDY", "MATRIX", "ETERNITAS",
                                                "OPENAI", "ANTHROPIC", "TELEGRAM", "DISCORD", "SLACK", "TWILIO"]):
                val = os.environ[key]
                redacted = val[:4] + "***" if len(val) > 8 else "***"
                lines.append(f"  {key}={redacted}")
        return "\n".join(lines)
    _r("debug", "Verbose diagnostic info for bug reports", "02_diagnostics", cmd_debug)

    async def cmd_logs(ctx):
        log_file = Path("data/windyfly.log")
        n = 20
        if ctx.get("_args"):
            try:
                n = int(ctx["_args"][0])
            except ValueError:
                pass
        if log_file.exists():
            lines = log_file.read_text().strip().split("\n")
            return "\n".join(lines[-n:])
        return "No log file found at data/windyfly.log"
    _r("logs", "Tail agent logs (last N lines)", "02_diagnostics", cmd_logs, usage="logs [N]")

    async def cmd_ping(ctx):
        start = time.monotonic()
        elapsed = (time.monotonic() - start) * 1000
        return f"🏓 Pong! ({elapsed:.1f}ms command processing)"
    _r("ping", "Check if the agent is responsive", "02_diagnostics", cmd_ping, aliases=["pong"])

    async def cmd_benchmark(ctx):
        if not _db:
            return "Database not available for benchmark."
        start = time.monotonic()
        try:
            from windyfly.agent.loop import agent_respond
            response = await agent_respond("What is 2+2? Reply with just the number.", "benchmark-session", _db)
            elapsed = time.monotonic() - start
            return f"Benchmark: {elapsed:.2f}s\nResponse: {response[:100]}"
        except Exception as e:
            return f"Benchmark failed: {e}"
    _r("benchmark", "Speed test — time a simple prompt", "02_diagnostics", cmd_benchmark, aliases=["bench"])

    async def cmd_errors(ctx):
        log_file = Path("data/windyfly.log")
        if log_file.exists():
            lines = log_file.read_text().strip().split("\n")
            errors = [line for line in lines if "ERROR" in line or "CRITICAL" in line]
            if errors:
                return "Recent errors:\n" + "\n".join(errors[-10:])
            return "No errors found in logs."
        return "No log file found."
    _r("errors", "Show last 10 errors from logs", "02_diagnostics", cmd_errors)

    async def cmd_audit(ctx):
        lines = ["🔍 Audit Report\n"]
        data_dir = Path("data")
        if data_dir.exists():
            files = list(data_dir.rglob("*"))
            total_size = sum(f.stat().st_size for f in files if f.is_file()) / 1024 / 1024
            lines.append(f"Data directory: {len(files)} files, {total_size:.1f} MB")
        pid_file = Path("data/windyfly.pid")
        if pid_file.exists():
            lines.append(f"PID file exists: {pid_file.read_text().strip()[:50]}")
        recovery = Path("data/provision_recovery.json")
        if recovery.exists():
            lines.append("⚠ Provisioning recovery file exists — run /hatch to retry")
        return "\n".join(lines)
    _r("audit", "Full audit — stale files, orphaned data", "02_diagnostics", cmd_audit)

    # ═══════════════════════════════════════════════════════════════
    # CHAT & CONVERSATION (19-30)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_chat(ctx):
        return "Run 'windy chat' from terminal to start interactive chat mode."
    _r("chat", "Start CLI chat mode", "03_chat", cmd_chat)

    async def cmd_new(ctx):
        """Roll the session counter so the bot's prompt starts fresh
        on the next turn — clears _session_tokens for the old id and
        bumps to a new id so get_recent_episodes() no longer pulls
        prior turns. Long-term memory (nodes, turnover letter,
        identity facts) survives because it isn't session-scoped.

        Pre-fix this returned the literal 'NEW_SESSION' string which
        nothing in the channel layer read — /new was a stub that
        only posted the sentinel back to the user (see 2026-05-19
        Telegram screenshot)."""
        platform = (ctx or {}).get("platform")
        channel_id = (ctx or {}).get("channel_id")
        if not platform or not channel_id:
            return (
                "🪰 /new received but I couldn't identify your "
                "chat session — the channel layer didn't pass "
                "channel_id into the command context. /new is a "
                "no-op until that's fixed."
            )
        from windyfly.agent.session_reset import reset_session
        reset_session(platform, channel_id)
        return (
            "🪰 Fresh start. Working memory cleared — I've got a "
            "clean slate to think on. Long-term memory (who you "
            "are, what we've been working on) is still intact. "
            "What's on your mind?"
        )
    _r("new", "Start a new conversation (clear context, keep memory)", "03_chat", cmd_new, aliases=["fresh"])

    def _resolve_active_model(
        platform: str | None, channel_id: str | None,
    ) -> str:
        """Resolution order for "which model is active for this turn":
          1. Per-channel ``/model`` preference (if set)
          2. ``DEFAULT_MODEL`` env var
          3. ``[agent].default_model`` from config
          4. Hardcoded fallback ``claude-sonnet-4-6``
        Mirrors the read path used by the agent loop in PR #197 so
        ``/model`` and ``/memory`` agree with what actually fires."""
        if platform and channel_id:
            try:
                from windyfly.agent.session_reset import get_model
                m = get_model(platform, channel_id)
                if m:
                    return m
            except Exception:
                pass
        env = os.environ.get("DEFAULT_MODEL")
        if env:
            return env
        cfg = (_config or {}).get("agent", {}).get("default_model")
        if cfg:
            return cfg
        return "claude-sonnet-4-6"

    # ── /model ─ pick which brain powers replies on this channel ──
    async def cmd_model(ctx):
        """Show current model + list catalog, or switch with an arg.

        Per-channel preference: takes effect on the NEXT reply; does
        NOT force a fresh session (use /new explicitly for a clean
        break). Per the 2026-05-19 design discussion with Grant.
        """
        from windyfly.agent.models_catalog import (
            list_models, resolve, format_cap, family_emoji,
        )
        platform = (ctx or {}).get("platform")
        channel_id = (ctx or {}).get("channel_id")
        raw = (ctx or {}).get("_raw", "") or ""
        arg = raw.strip()

        # ── No arg: render the picker UI ─────────────────────────
        if not arg:
            current_model = _resolve_active_model(platform, channel_id)
            current_info = resolve(current_model)
            lines = ["🤖 Current model: " + (
                f"{current_info.id} — {current_info.description}"
                if current_info else current_model
            )]
            lines.append("")
            lines.append("Available models:")
            for m in list_models():
                ext = (
                    f" (or {format_cap(m.extended_cap)} extended)"
                    if m.extended_cap else ""
                )
                marker = " ← current" if (
                    current_info and m.id == current_info.id
                ) else ""
                lines.append(
                    f"  {family_emoji(m.family)} `{m.aliases[0]}` "
                    f"({m.id}) — {m.description}; "
                    f"{format_cap(m.native_cap)} memory{ext}{marker}"
                )
            lines.append("")
            lines.append(
                "Switch with: /model opus  (or sonnet / haiku / "
                "smartest / balanced / fastest)"
            )
            return "\n".join(lines)

        # ── With arg: resolve + persist ──────────────────────────
        target = resolve(arg)
        if target is None:
            return (
                f"🤔 I don't know the model '{arg}'. Try one of: "
                "opus, sonnet, haiku (or smartest, balanced, fastest)."
            )
        if not platform or not channel_id:
            return (
                "🪰 /model received but I couldn't identify your "
                "chat session — channel context missing. The change "
                "wasn't saved."
            )
        from windyfly.agent.session_reset import (
            set_model, get_memory_cap,
        )
        set_model(platform, channel_id, target.id)

        # If the user has a memory_cap pinned that this new model
        # can't deliver, surface it now rather than at the next /status.
        pinned_cap = get_memory_cap(platform, channel_id)
        suffix = ""
        if pinned_cap is not None:
            from windyfly.agent.models_catalog import supports_cap
            ok, beta = supports_cap(target.id, pinned_cap)
            if not ok:
                from windyfly.agent.models_catalog import format_cap as _fc
                suffix = (
                    f"\n\n⚠️ Your memory is set to "
                    f"{_fc(pinned_cap)}, but {target.id} tops out at "
                    f"{_fc(target.native_cap)}"
                    f"{' (or ' + _fc(target.extended_cap) + ' extended)' if target.extended_cap else ''}"
                    ". I'll clamp to what this model can do. Type "
                    "`/memory default` to use its native size."
                )
        return (
            f"🤖 Switched to {target.id} — {target.description}. "
            f"Effective on your next message; current conversation "
            f"carries over. Use /new for a clean break.{suffix}"
        )

    _r("model", "Show or switch the model powering replies",
       "03_chat", cmd_model, aliases=["brain"])

    # PR #198 — direct shortcuts so users can type `/opus` instead of
    # `/model opus`. Grandma will guess the shorter form first; the
    # current "Unknown command" rejection is friction.
    def _make_model_alias_handler(alias: str):
        async def _handler(ctx):
            ctx2 = dict(ctx or {})
            ctx2["_raw"] = alias
            ctx2["_args"] = [alias]
            return await cmd_model(ctx2)
        return _handler

    for _alias in ("opus", "sonnet", "haiku",
                   "smartest", "balanced", "fastest"):
        _r(_alias, f"Switch model to {_alias} (shortcut for /model {_alias})",
           "03_chat", _make_model_alias_handler(_alias))

    # ── /memory ─ pick the context-window cap for this channel ───
    async def cmd_memory(ctx):
        """Show or set the context-window cap on this channel.

        Conflict resolution (single matrix, four-way):
          - cap ≤ model native    → just set
          - cap == model native   → just set
          - cap ≤ model extended  → set + auto-attach 1M beta header
                                    on future Anthropic calls
          - cap >  model extended → refuse + suggest /model switch
        """
        from windyfly.agent.models_catalog import (
            resolve, supports_cap, parse_cap, format_cap,
        )
        platform = (ctx or {}).get("platform")
        channel_id = (ctx or {}).get("channel_id")
        raw = (ctx or {}).get("_raw", "") or ""
        arg = raw.strip()
        args_list = (ctx or {}).get("_args", []) or []

        # ── Developer subcommands (search/nodes/export/clear/stats) ─
        # Routed to the dispatcher merged from the old /memory; if it
        # returns "" the input wasn't a recognized subcommand and we
        # fall through to the new cap-setting behavior.
        if args_list:
            sub = await cmd_memory_subcommand(args_list)
            if sub:
                return sub

        # ── No arg: show current ─────────────────────────────────
        if not arg:
            current_model = _resolve_active_model(platform, channel_id)
            info = resolve(current_model)
            if platform and channel_id:
                from windyfly.agent.session_reset import get_memory_cap
                pinned = get_memory_cap(platform, channel_id)
            else:
                pinned = None
            native = info.native_cap if info else 200_000
            ext = info.extended_cap if info else None
            effective = pinned if pinned is not None else native
            origin = "your pick" if pinned is not None else "model default"
            lines = [
                f"🧠 Current memory cap: {format_cap(effective)} "
                f"({origin})",
                "",
                f"Model: {current_model} — native {format_cap(native)}"
                + (f", extended up to {format_cap(ext)}" if ext else ""),
                "",
                "Set with: `/memory 1M` (or `/memory 500K`, `/memory "
                "default`, `/memory 200K`).",
            ]
            return "\n".join(lines)

        # ── With arg: parse + validate + persist ─────────────────
        if not platform or not channel_id:
            return (
                "🪰 /memory received but I couldn't identify your "
                "chat session — channel context missing."
            )
        from windyfly.agent.session_reset import (
            set_memory_cap, get_memory_cap,
        )
        if arg.lower() in ("default", "reset", "clear"):
            set_memory_cap(platform, channel_id, None)
            return (
                "🧠 Memory cap cleared — using your model's native "
                "default. Effective on your next message."
            )
        cap = parse_cap(arg)
        if cap is None:
            return (
                f"🤔 I couldn't parse '{arg}' as a memory size. "
                "Try `/memory 1M`, `/memory 500K`, or `/memory "
                "default`."
            )
        current_model = _resolve_active_model(platform, channel_id)
        ok, beta = supports_cap(current_model, cap)
        if not ok:
            info = resolve(current_model)
            top = (info.extended_cap or info.native_cap) if info else 200_000
            return (
                f"🚫 {current_model} can't deliver {format_cap(cap)} "
                f"memory — its max is {format_cap(top)}. Switch to a "
                "bigger model first with `/model opus` (1M native), "
                "then set the cap."
            )
        set_memory_cap(platform, channel_id, cap)
        note = ""
        if beta:
            note = (
                " — extended-context tier engaged (same Max-plan "
                "flat rate)"
            )
        return (
            f"🧠 Memory cap set to {format_cap(cap)}{note}. "
            "Effective on your next message."
        )

    _r("memory", "Show or set this channel's context-window cap",
       "03_chat", cmd_memory)

    async def cmd_reset_chat(ctx):
        return "RESET_SESSION"
    _r("reset", "Reset conversation context completely", "03_chat", cmd_reset_chat)

    async def cmd_undo(ctx):
        return "UNDO_LAST"
    _r("undo", "Undo the last exchange", "03_chat", cmd_undo)

    async def cmd_retry(ctx):
        return "RETRY_LAST"
    _r("retry", "Regenerate the last response", "03_chat", cmd_retry, aliases=["regenerate", "regen"])

    async def cmd_continue(ctx):
        return "CONTINUE_GENERATING"
    _r("continue", "Continue generating if response was truncated", "03_chat", cmd_continue, aliases=["more"])

    async def cmd_copy(ctx):
        return "COPY_LAST"
    _r("copy", "Copy the last response to clipboard", "03_chat", cmd_copy)

    async def cmd_save_chat(ctx):
        filename = ctx.get("_raw", "conversation.md")
        return f"SAVE_CONVERSATION:{filename}"
    _r("save", "Save current conversation to file", "03_chat", cmd_save_chat, usage="save [filename]")

    async def cmd_load_chat(ctx):
        filename = ctx.get("_raw", "")
        if not filename:
            return "Usage: /load <filename>"
        return f"LOAD_CONVERSATION:{filename}"
    _r("load", "Load a conversation from file", "03_chat", cmd_load_chat, usage="load <filename>")

    async def cmd_history(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.episodes import get_recent_episodes
            episodes = get_recent_episodes(_db, limit=10)
            if not episodes:
                return "No conversation history."
            lines = ["Recent conversation:\n"]
            for ep in episodes:
                role = ep.get("role", "?")
                content = ep.get("content", "")[:80]
                lines.append(f"  [{role}] {content}...")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
    _r("history", "Show conversation history (last 10 messages)", "03_chat", cmd_history)

    async def cmd_summarize(ctx):
        return "SUMMARIZE_CONVERSATION"
    _r("summarize", "Summarize the current conversation", "03_chat", cmd_summarize, aliases=["summary"])

    async def cmd_share(ctx):
        return "SHARE_CONVERSATION"
    _r("share", "Export conversation as shareable markdown", "03_chat", cmd_share)

    # ═══════════════════════════════════════════════════════════════
    # MODEL & AI (31-40)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_models(ctx):
        providers = [
            ("OpenAI", "gpt-4o, gpt-4o-mini, o1, o3-mini"),
            ("Anthropic", "claude-sonnet-4-6, claude-opus-4-6, claude-haiku-4-5"),
            ("Google", "gemini-2.0-flash, gemini-1.5-pro (free tier)"),
            ("Groq", "llama-3.1-70b, mixtral-8x7b (free tier)"),
            ("DeepSeek", "deepseek-chat, deepseek-reasoner (very cheap)"),
            ("Mistral", "mistral-large, mistral-small"),
            ("Ollama", "any local model (free, fully offline)"),
            ("OpenRouter", "hundreds of models via openrouter.ai"),
            ("Together.ai", "open models with free credits"),
        ]
        lines = ["Available LLM Providers:\n"]
        for name, models in providers:
            lines.append(f"  {name}: {models}")
        lines.append("\nChange: /model set <model-name>")
        return "\n".join(lines)

    # The /model command is registered earlier in this file via PR
    # #197 — it's per-channel-preference-aware (uses
    # session_reset.set_model) instead of mutating the process-wide
    # DEFAULT_MODEL env var. The old env-mutating /model was deleted
    # here: it affected every channel and disagreed with /status,
    # which read from per-channel preference after PR #197. Single
    # registration site.
    _r("models", "List all available models and providers", "04_model", cmd_models, aliases=["providers"])

    async def cmd_provider(ctx):
        model = os.environ.get("DEFAULT_MODEL", "")
        if "gpt" in model or "o1" in model or "o3" in model:
            return f"Provider: OpenAI (model: {model})"
        elif "claude" in model:
            return f"Provider: Anthropic (model: {model})"
        elif "gemini" in model:
            return f"Provider: Google (model: {model})"
        elif "llama" in model or "mixtral" in model:
            return f"Provider: Groq/Ollama (model: {model})"
        return f"Provider: Unknown (model: {model})"
    _r("provider", "Show current LLM provider", "04_model", cmd_provider)

    async def cmd_temperature(ctx):
        args = ctx.get("_args", [])
        if args:
            try:
                temp = float(args[0])
                os.environ["LLM_TEMPERATURE"] = str(temp)
                return f"Temperature set to {temp}"
            except ValueError:
                return "Usage: /temperature <0.0-1.0>"
        temp = os.environ.get("LLM_TEMPERATURE", "0.7")
        return f"Temperature: {temp}"
    _r("temperature", "Show or set temperature (0.0-1.0)", "04_model", cmd_temperature, aliases=["temp"])

    async def cmd_tokens(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.cost_tracker import get_daily_cost
            cost = get_daily_cost(_db)
            return f"Session tokens: (tracked via cost ledger)\nToday's cost: ${cost:.4f}"
        except Exception:
            return "Token tracking not available."
    _r("tokens", "Show token usage for current session", "04_model", cmd_tokens)

    async def cmd_context(ctx):
        max_ctx = int(os.environ.get("MAX_CONTEXT_TOKENS", "8000"))
        return f"Context window: {max_ctx} tokens max\n(Actual usage varies per conversation)"
    _r("context", "Show context window usage", "04_model", cmd_context, aliases=["ctx"])

    async def cmd_fast(ctx):
        os.environ["DEFAULT_MODEL"] = "gpt-4o-mini"
        return "Switched to fast mode: gpt-4o-mini (cheapest, fastest)"
    _r("fast", "Switch to fastest/cheapest model", "04_model", cmd_fast, aliases=["cheap", "mini"])

    # ═══════════════════════════════════════════════════════════════
    # PERSONALITY & SOUL (41-50)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_soul(ctx):
        args = ctx.get("_args", [])
        if args and args[0] == "edit":
            return "Run 'windy soul edit' from terminal to open SOUL.md in your editor."
        soul_path = Path("SOUL.md")
        if soul_path.exists():
            content = soul_path.read_text()[:500]
            return f"Current personality:\n{content}{'...' if len(soul_path.read_text()) > 500 else ''}"
        return "No SOUL.md found. Create one to define your agent's personality."
    _r("soul", "Show current personality / SOUL.md", "05_personality", cmd_soul,
       aliases=["personality", "persona"])

    async def cmd_sliders(ctx):
        if not _db:
            return "Database not available."
        try:
            # ``get_sliders`` is the actual function name; the
            # original code imported ``get_all_sliders`` which has
            # never existed → /sliders raised ImportError on every
            # invocation since this file shipped. Surfaced
            # 2026-05-20 by Grant asking "is there a slash command
            # for the sliders?"
            from windyfly.control_panel import get_sliders
            sliders = get_sliders(_db)
            lines = ["*Personality Sliders:*\n"]
            for name, value in sorted(sliders.items()):
                val = int(float(value))
                bar = "█" * val + "░" * (10 - val)
                lines.append(f"  `{name:22s}` [{bar}] {val}/10")
            lines.append("\n_Tune one: /slider <name> <value>_")
            lines.append("_Apply preset: /preset <name>  ·  list: /presets_")
            return "\n".join(lines)
        except Exception as e:
            return f"Error loading sliders: {e}"
    _r("sliders", "Show all personality sliders", "05_personality", cmd_sliders)

    async def cmd_slider_set(ctx):
        args = ctx.get("_args", [])
        # Tolerate both `/slider humor 8` and the docstring's
        # original `/slider set humor 8` syntax.
        if args and args[0] == "set":
            args = args[1:]
        if len(args) < 2:
            return "Usage: `/slider <name> <value>` (e.g. `/slider humor 8`)"
        name, value = args[0], args[1]
        if not _db:
            return "Database not available."
        try:
            # FIX: signature is set_slider(db, slider_name, value,
            # user_id="default"). Pre-fix code passed (_db, None,
            # name, value) which mapped to slider_name=None →
            # ValueError. Has never worked since launch.
            from windyfly.control_panel import set_slider
            set_slider(_db, name, int(float(value)))
            return f"✅ Slider `{name}` set to `{value}`"
        except Exception as e:
            return f"Error: {e}"
    _r("slider", "Set a personality slider", "05_personality", cmd_slider_set,
       usage="slider <name> <value>")

    async def cmd_preset(ctx):
        args = ctx.get("_args", [])
        if not args:
            return ("Available presets: buddy, engineer, coder, friend, writer, researcher, powerhouse, silent\n"
                    "Usage: `/preset <name>`")
        if not _db:
            return "Database not available."
        try:
            # FIX: signature is apply_preset(db, preset_name,
            # user_id="default"). Pre-fix code passed (_db, None,
            # args[0]) → preset_name=None → "Unknown preset 'None'"
            # on every invocation. Has never worked since launch.
            from windyfly.control_panel import apply_preset
            result = apply_preset(_db, args[0])
            return f"✅ Preset `{args[0]}` applied — `/sliders` to see the new values" if result else f"Unknown preset: {args[0]}"
        except Exception as e:
            return f"Error: {e}"
    _r("preset", "Switch personality preset", "05_personality", cmd_preset, usage="preset <name>")

    async def cmd_presets(ctx):
        presets = {
            "buddy": "Friendly companion (warmth 9, humor 7, proactivity 7)",
            "engineer": "Technical precision (reasoning 8, humor 2, personality 3)",
            "coder": "Programming expert (reasoning 9, personality 1)",
            "friend": "Best friend (warmth 10, humor 8, personality 10)",
            "writer": "Wordsmith (creativity 8, verbosity 9)",
            "researcher": "Scholar (reasoning 10, memory 9, personality 2)",
            "powerhouse": "Go-getter (proactivity 10, autonomy 8)",
            "silent": "Minimal (verbosity 2, humor 0, personality 3)",
        }
        lines = ["Available Presets:\n"]
        for name, desc in presets.items():
            lines.append(f"  {name:14s} {desc}")
        lines.append("\nSwitch: /preset <name>")
        return "\n".join(lines)
    _r("presets", "List all available personality presets", "05_personality", cmd_presets)

    async def cmd_mood(ctx):
        return "Current emotional context: neutral\n(Emotion detection runs on incoming messages)"
    _r("mood", "Show detected emotional context", "05_personality", cmd_mood, aliases=["emotion", "vibe"])

    async def cmd_mode(ctx):
        args = ctx.get("_args", [])
        if args:
            mode = args[0]
            if mode in ("companion", "focused", "neutral"):
                return f"Mode switched to: {mode}"
            return "Available modes: companion, focused, neutral"
        return "Current mode: companion\nAvailable: companion, focused, neutral\nSwitch: /mode <name>"
    _r("mode", "Show or switch mode (companion/focused/neutral)", "05_personality", cmd_mode)

    # ═══════════════════════════════════════════════════════════════
    # MEMORY & KNOWLEDGE (51-62)
    # ═══════════════════════════════════════════════════════════════

    # /memory subcommands (search / nodes / export / clear / stats)
    # — these are the developer-flavored memory-DB operations from
    # before PR #197. PR #197 made bare `/memory` and `/memory <size>`
    # control the context-window cap (the grandma use case). To keep
    # both, the new /memory command (registered earlier) routes
    # ``search`` / ``nodes`` / ``export`` / ``clear`` / ``stats``
    # subcommands here, so URL stays the same and discoverability is
    # unified under one name. See cmd_memory_subcommand below.
    async def cmd_memory_subcommand(args: list[str]) -> str:
        """Dispatch the old developer-flavored /memory subcommands."""
        if args and args[0] == "search" and len(args) > 1:
            query = " ".join(args[1:])
            if not _db:
                return "Database not available."
            try:
                from windyfly.memory.episodes import search_episodes
                results = search_episodes(_db, query, limit=5)
                if not results:
                    return f"No results for '{query}'"
                lines = [f"Search results for '{query}':\n"]
                for r in results:
                    lines.append(f"  [{r.get('role','?')}] {r.get('content','')[:80]}...")
                return "\n".join(lines)
            except Exception as e:
                return f"Search error: {e}"
        if args and args[0] == "nodes":
            if not _db:
                return "Database not available."
            try:
                from windyfly.memory.nodes import get_all_nodes
                node_type = args[1] if len(args) > 1 else None
                nodes = get_all_nodes(_db, node_type=node_type, limit=20)
                if not nodes:
                    return "No nodes found."
                lines = [f"Knowledge nodes{f' (type: {node_type})' if node_type else ''}:\n"]
                for n in nodes:
                    lines.append(f"  [{n.get('type','?')}] {n.get('name','?')} — {n.get('metadata',{})}")
                return "\n".join(lines[:25])
            except Exception as e:
                return f"Error: {e}"
        if args and args[0] == "export":
            return "MEMORY_EXPORT"
        if args and args[0] == "clear":
            return "Run 'windy memory clear --confirm' from terminal. This is irreversible."
        if args and args[0] == "stats":
            if not _db:
                return "Database not available."
            try:
                from windyfly.memory.nodes import count_nodes
                from windyfly.memory.episodes import count_episodes
                node_count = count_nodes(_db)
                episode_count = count_episodes(_db)
                db_path = os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")
                size = os.path.getsize(db_path) / 1024 / 1024 if os.path.exists(db_path) else 0
                return f"Memory: {node_count} nodes, {episode_count} episodes | DB: {size:.1f} MB"
            except Exception as e:
                return f"Error: {e}"
        return ""  # no matching subcommand

    async def cmd_intents(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.intents import get_active_intents
            intents = get_active_intents(_db)
            if not intents:
                return "No active intents/goals."
            lines = ["Active intents:\n"]
            for i in intents:
                lines.append(f"  • {i.get('description','?')} (priority: {i.get('priority','?')})")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
    _r("intents", "List active intents/goals", "06_memory", cmd_intents, aliases=["goals"])

    async def cmd_facts(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.nodes import get_all_nodes
            nodes = get_all_nodes(_db, node_type="fact", limit=20)
            nodes += get_all_nodes(_db, node_type="preference", limit=20)
            if not nodes:
                return "No facts or preferences stored."
            lines = ["Known facts:\n"]
            for n in nodes:
                lines.append(f"  • {n.get('name','?')}: {n.get('metadata',{})}")
            return "\n".join(lines[:30])
        except Exception as e:
            return f"Error: {e}"
    _r("facts", "List known facts about the user", "06_memory", cmd_facts)

    async def cmd_forget(ctx):
        return ("To forget something specific, tell me in chat: 'forget that I like pizza'\n"
                "To clear all memory: /memory clear (terminal only, irreversible)")
    _r("forget", "Remove specific knowledge", "06_memory", cmd_forget, dangerous=True)

    async def cmd_remember(ctx):
        fact = ctx.get("_raw", "")
        if not fact:
            return "Usage: /remember <fact> (e.g. /remember I'm allergic to shellfish)"
        return f"REMEMBER:{fact}"
    _r("remember", "Manually add a fact to memory", "06_memory", cmd_remember, usage="remember <fact>")

    async def cmd_conflicts(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.conflict_detector import get_unresolved_conflicts
            conflicts = get_unresolved_conflicts(_db)
            if not conflicts:
                return "No unresolved memory conflicts."
            lines = ["Unresolved conflicts:\n"]
            for c in conflicts:
                lines.append(f"  ⚠ {c.get('old_value','?')} vs {c.get('new_value','?')}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
    _r("conflicts", "Show detected memory conflicts", "06_memory", cmd_conflicts)

    async def cmd_failures(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.failures import get_unresolved_failures
            failures = get_unresolved_failures(_db)
            if not failures:
                return "No unresolved failures. (Never Wrong Twice log is clean)"
            lines = ["Never Wrong Twice — unresolved failures:\n"]
            for f in failures:
                lines.append(f"  ✗ [{f.get('fault_type','?')}] {f.get('description','')[:80]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
    _r("failures", "Show 'Never Wrong Twice' failure log", "06_memory", cmd_failures)

    async def cmd_decay(ctx):
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.decay import run_decay
            run_decay(_db)
            return "Cognitive decay cycle completed. Stale memories pruned."
        except Exception as e:
            return f"Error: {e}"
    _r("decay", "Run cognitive decay now (prune stale memories)", "06_memory", cmd_decay)

    # ═══════════════════════════════════════════════════════════════
    # SKILLS (63-70)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_skills(ctx):
        args = ctx.get("_args", [])
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.skills import get_promoted_skills, get_all_skills
            if args and args[0] == "all":
                skills = get_all_skills(_db)
            else:
                skills = get_promoted_skills(_db)
            if not skills:
                return "No skills found."
            label = "All" if args and args[0] == "all" else "Promoted"
            lines = [f"{label} skills:\n"]
            for s in skills:
                status = "✓" if s.get("promoted") else "○"
                lines.append(f"  {status} {s.get('name','?')} v{s.get('version',1)} — {s.get('description','')[:60]}")
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {e}"
    _r("skills", "List skills (promoted by default, 'skills all' for all)", "07_skills", cmd_skills,
        usage="skills [all]")

    async def cmd_skills_run(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /skills run <name>"
        return f"SKILL_RUN:{args[0]}"
    _r("skills-run", "Execute a skill manually", "07_skills", cmd_skills_run, usage="skills-run <name>")

    async def cmd_skills_eval(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /skills eval <name>"
        return f"SKILL_EVAL:{args[0]}"
    _r("skills-eval", "Run evaluation gates on a skill", "07_skills", cmd_skills_eval, usage="skills-eval <name>")

    async def cmd_skills_promote(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /skills promote <name>"
        return f"SKILL_PROMOTE:{args[0]}"
    _r("skills-promote", "Promote a skill after passing gates", "07_skills", cmd_skills_promote,
       usage="skills-promote <name>")

    async def cmd_skills_rollback(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /skills rollback <name>"
        return f"SKILL_ROLLBACK:{args[0]}"
    _r("skills-rollback", "Rollback to previous skill version", "07_skills", cmd_skills_rollback,
       usage="skills-rollback <name>")

    async def cmd_skills_test(ctx):
        return "SKILL_REGRESSION_TEST"
    _r("skills-test", "Run regression tests on all promoted skills", "07_skills", cmd_skills_test)

    async def cmd_skills_create(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /skills create <name>"
        return f"SKILL_CREATE:{args[0]}"
    _r("skills-create", "Create a new skill", "07_skills", cmd_skills_create, usage="skills-create <name>")

    # Register remaining categories via the second half
    _register_budget_through_help()


def _register_budget_through_help():
    """Register commands 71-108: Budget, Identity, Config, Maintenance, Developer."""

    # ═══════════════════════════════════════════════════════════════
    # BUDGET & COSTS (71-76)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_budget(ctx):
        args = ctx.get("_args", [])
        if not _db:
            return "Database not available."
        try:
            from windyfly.memory.cost_tracker import get_daily_cost
            limit = float(os.environ.get("DAILY_BUDGET_USD", "5.00"))
            if args and args[0] == "set" and len(args) > 1:
                new_limit = float(args[1])
                os.environ["DAILY_BUDGET_USD"] = str(new_limit)
                return f"Daily budget set to ${new_limit:.2f}"
            if args and args[0] == "month":
                daily = get_daily_cost(_db)
                return f"This month's estimated spend: ${daily * 30:.2f} (based on today: ${daily:.4f})"
            if args and args[0] == "breakdown":
                return "Cost breakdown by model:\n(Available in the gateway dashboard at localhost:3000)"
            cost = get_daily_cost(_db)
            pct = (cost / limit * 100) if limit > 0 else 0
            bar = "█" * int(pct / 10) + "░" * (10 - int(pct / 10))
            return f"Budget: ${cost:.4f} / ${limit:.2f} [{bar}] {pct:.1f}%"
        except Exception as e:
            return f"Error: {e}"

    _r("budget", "Show today's spend vs daily limit", "08_budget", cmd_budget,
        aliases=["cost", "spend", "usage"], usage="budget [month | set <amount> | breakdown]")

    # ═══════════════════════════════════════════════════════════════
    # EVERYDAY TOOLS (87-96)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_remind(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /remind <time> <message>\nExample: /remind in 20 minutes take medicine"
        # Parse: first arg is time-like, rest is message
        from windyfly.tools.reminders import set_reminder
        from windyfly.memory.database import Database
        _db = Database(os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db"))
        # Find the time boundary — look for common time starters
        text = " ".join(args)
        for sep in ["to ", "that "]:
            idx = text.find(sep)
            if idx > 0:
                time_str = text[:idx].strip()
                message = text[idx + len(sep):].strip()
                result = set_reminder(_db, message, time_str)
                _db.close()
                return result.get("message", str(result))
        # Fallback: first 3 words are time, rest is message
        time_str = " ".join(args[:3])
        message = " ".join(args[3:]) or "reminder"
        result = set_reminder(_db, message, time_str)
        _db.close()
        return result.get("message", str(result))
    _r("remind", "Set a reminder", "08a_tools", cmd_remind,
       aliases=["reminder", "timer"], usage="remind <time> <message>")

    async def cmd_reminders(ctx):
        from windyfly.tools.reminders import list_reminders
        from windyfly.memory.database import Database
        _db = Database(os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db"))
        result = list_reminders(_db)
        _db.close()
        return result.get("message", "No reminders.")
    _r("reminders", "List upcoming reminders", "08a_tools", cmd_reminders)

    async def cmd_todo(ctx):
        args = ctx.get("_args", [])
        from windyfly.tools.todos import add_todo, list_todos, complete_todo, delete_todo
        from windyfly.memory.database import Database
        _db = Database(os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db"))
        action = args[0] if args else "list"
        rest = " ".join(args[1:])
        if action == "add" and rest:
            result = add_todo(_db, rest)
        elif action in ("done", "complete") and rest:
            result = complete_todo(_db, rest)
        elif action == "delete" and rest:
            result = delete_todo(_db, rest)
        else:
            result = list_todos(_db)
        _db.close()
        return result.get("message", str(result))
    _r("todo", "Manage to-do list", "08a_tools", cmd_todo,
       aliases=["todos", "task", "tasks"], usage="todo [add|done|delete] <text>")

    async def cmd_weather(ctx):
        args = ctx.get("_args", [])
        location = " ".join(args) if args else "New York"
        from windyfly.tools.weather import get_weather
        result = get_weather(location)
        return result.get("summary", result.get("error", str(result)))
    _r("weather", "Get current weather", "08a_tools", cmd_weather, usage="weather <location>")

    async def cmd_news(ctx):
        args = ctx.get("_args", [])
        topic = " ".join(args) if args else None
        from windyfly.tools.news import get_news
        result = get_news(topic)
        return result.get("message", str(result))
    _r("news", "Get latest headlines", "08a_tools", cmd_news,
       aliases=["headlines"], usage="news [topic]")

    async def cmd_calendar(ctx):
        args = ctx.get("_args", [])
        from windyfly.tools.calendar import get_today_events, get_upcoming_events
        action = args[0] if args else "today"
        if action == "week":
            result = get_upcoming_events(7)
        else:
            result = get_today_events()
        return result.get("message", str(result))
    _r("calendar", "Show calendar events", "08a_tools", cmd_calendar,
       aliases=["cal", "schedule", "events"], usage="calendar [today|week]")

    async def cmd_capabilities(ctx):
        from windyfly.agent.capabilities import HELP_TEXT
        return HELP_TEXT
    _r("capabilities", "What can your agent do?", "08a_tools", cmd_capabilities,
       aliases=["whatcanyoudo", "abilities"])

    # ═══════════════════════════════════════════════════════════════
    # IDENTITY & ECOSYSTEM (77-86)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_ecosystem(ctx):
        try:
            from windyfly.ecosystem_health import check_ecosystem_health
            return await check_ecosystem_health()
        except Exception as e:
            return f"Error: {e}"
    _r("ecosystem", "Show all Windy product connections + health", "09_identity", cmd_ecosystem, aliases=["eco"])

    async def cmd_passport(ctx):
        passport = os.environ.get("ETERNITAS_PASSPORT", "")
        if passport:
            return f"🪪 Eternitas Passport: {passport}\nStatus: active\nTrust: 70/100"
        return "🪪 No Eternitas passport. Run /go to register."
    _r("passport", "Show Eternitas passport status", "09_identity", cmd_passport, aliases=["id", "identity"])

    async def cmd_mail_status(ctx):
        email = os.environ.get("WINDYMAIL_EMAIL", "")
        if email:
            return f"📧 Windy Mail: {email}\nStatus: active"
        return "📧 No mailbox. Run /go to provision."
    _r("mail", "Show Windy Mail inbox status", "09_identity", cmd_mail_status, aliases=["email"])

    async def cmd_phone_status(ctx):
        phone = os.environ.get("TWILIO_PHONE_NUMBER", "")
        if phone:
            return f"📱 Phone: {phone}"
        return "📱 No phone number. Configure Twilio credentials."
    _r("phone", "Show phone number", "09_identity", cmd_phone_status)

    async def cmd_cert(ctx):
        import glob
        certs = glob.glob("data/birth_certificate_*.pdf")
        if certs:
            latest = sorted(certs)[-1]
            return f"📜 Birth Certificate: {latest}\n(Open with: open {latest})"
        return "📜 No birth certificate. Run /go to generate."
    _r("cert", "Show or open birth certificate", "09_identity", cmd_cert, aliases=["certificate"])

    async def cmd_channels(ctx):
        channels = {
            "CLI": ("Always on", True),
            "Matrix": ("Windy Chat", bool(os.environ.get("MATRIX_BOT_TOKEN") or os.environ.get("MATRIX_BOT_PASSWORD"))),
            "Telegram": ("@BotFather", bool(os.environ.get("TELEGRAM_BOT_TOKEN"))),
            "Discord": ("Bot token", bool(os.environ.get("DISCORD_BOT_TOKEN"))),
            "Slack": ("Bot + App", bool(os.environ.get("SLACK_BOT_TOKEN"))),
            "WhatsApp": ("Twilio", bool(os.environ.get("TWILIO_WHATSAPP_NUMBER"))),
            "Signal": ("signal-cli", bool(os.environ.get("SIGNAL_PHONE_NUMBER"))),
            "Teams": ("Bot Framework", bool(os.environ.get("TEAMS_APP_ID"))),
            "IRC": ("Open", bool(os.environ.get("IRC_SERVER"))),
        }
        lines = ["Messaging Channels:\n"]
        for name, (detail, active) in channels.items():
            lines.append(f"  {'✅' if active else '○':3s} {name:12s} ({detail})")
        lines.append("\nAdd a channel: /channel-add <platform>")
        return "\n".join(lines)
    _r("channels", "Show configured messaging channels", "09_identity", cmd_channels)

    async def cmd_channel_add(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /channel-add <platform>\nPlatforms: telegram, discord, slack, whatsapp, signal, teams, irc"
        guides = {
            "telegram": "1. Open Telegram, message @BotFather\n2. Send /newbot, follow prompts\n3. Copy the token\n4. Set: TELEGRAM_BOT_TOKEN=<token> in .env\n5. Restart: /restart",
            "discord": "1. Go to discord.com/developers\n2. Create application → Bot → Token\n3. Set: DISCORD_BOT_TOKEN=<token> in .env\n4. Invite bot to server with Message Content intent\n5. Restart: /restart",
            "slack": "1. Go to api.slack.com/apps\n2. Create app → Enable Socket Mode\n3. Set: SLACK_BOT_TOKEN and SLACK_APP_TOKEN in .env\n4. Restart: /restart",
            "whatsapp": "1. Go to twilio.com → Messaging → WhatsApp Sandbox\n2. Set: TWILIO_WHATSAPP_NUMBER in .env\n3. Configure webhook URL\n4. Restart: /restart",
            "signal": "1. Run: docker run -p 8080:8080 bbernhard/signal-cli-rest-api\n2. Register your number with Signal\n3. Set: SIGNAL_PHONE_NUMBER in .env\n4. Restart: /restart",
            "teams": "1. Go to dev.teams.microsoft.com\n2. Create bot → Get App ID and Password\n3. Set: TEAMS_APP_ID and TEAMS_APP_PASSWORD in .env\n4. Restart: /restart",
            "irc": "1. Set IRC_SERVER (e.g. irc.libera.chat)\n2. Set IRC_CHANNEL (e.g. #windyfly)\n3. Set IRC_NICKNAME\n4. Restart: /restart",
        }
        platform_name = args[0].lower()
        return guides.get(platform_name, f"Unknown platform: {platform_name}")
    _r("channel-add", "Guide for adding a new messaging channel", "09_identity", cmd_channel_add,
       usage="channel-add <platform>")

    async def cmd_whoami(ctx):
        name = os.environ.get("WINDYFLY_AGENT_NAME", "windyfly")
        passport = os.environ.get("ETERNITAS_PASSPORT", "none")
        email = os.environ.get("WINDYMAIL_EMAIL", "none")
        phone = os.environ.get("TWILIO_PHONE_NUMBER", "none")
        owner = os.environ.get("WINDY_OWNER_NAME", "unknown")
        model = os.environ.get("DEFAULT_MODEL", "not set")
        return (f"🪰 I am {name}\nPassport: {passport}\nEmail: {email}\n"
                f"Phone: {phone}\nOwner: {owner}\nBrain: {model}")
    _r("whoami", "Show full agent identity", "09_identity", cmd_whoami)

    async def cmd_owner(ctx):
        name = os.environ.get("WINDY_OWNER_NAME", "unknown")
        owner_id = os.environ.get("WINDY_OWNER_ID", "unknown")
        return f"Owner: {name} (ID: {owner_id})"
    _r("owner", "Show owner info", "09_identity", cmd_owner)

    async def cmd_hatch(ctx):
        return "Run 'windy go' from terminal to re-run the hatch ceremony and re-provision ecosystem services."
    _r("hatch", "Re-run the hatch ceremony", "09_identity", cmd_hatch, aliases=["provision", "rehatch"])

    # ═══════════════════════════════════════════════════════════════
    # VPS & CLOUD (95-104)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_deploy(ctx):
        args = ctx.get("_args", [])
        if not args or "--vps" not in args:
            return "Usage: /deploy --vps [--region us-east-1] [--type t3.small]"
        from windyfly.vps_deploy import deploy_vps, format_vps_status
        region = "us-east-1"
        itype = "t3.small"
        for i, a in enumerate(args):
            if a == "--region" and i + 1 < len(args):
                region = args[i + 1]
            if a == "--type" and i + 1 < len(args):
                itype = args[i + 1]
        instance = await deploy_vps(region=region, instance_type=itype)
        return format_vps_status(instance)
    _r("deploy", "Deploy agent to a cloud VPS", "10_cloud", cmd_deploy,
       usage="deploy --vps [--region us-east-1] [--type t3.small]")

    async def cmd_vps(ctx):
        args = ctx.get("_args", [])
        action = args[0] if args else "status"
        from windyfly.vps_deploy import get_vps_status, stop_vps, destroy_vps, format_vps_status
        if action == "stop":
            return format_vps_status(await stop_vps())
        if action in ("destroy", "terminate"):
            return format_vps_status(await destroy_vps())
        return format_vps_status(await get_vps_status())
    _r("vps", "VPS status, stop, or destroy", "10_cloud", cmd_vps,
       aliases=["server", "instance"], usage="vps [status | stop | destroy]")

    async def cmd_backup(ctx):
        args = ctx.get("_args", [])
        action = args[0] if args else "status"
        from windyfly.cloud_backup import backup_to_cloud, restore_from_cloud, list_backups, get_backup_state
        if action == "now":
            result = await backup_to_cloud()
            if result.get("success"):
                return f"\u2705 Backup complete: {result.get('backup_id', 'ok')} ({result.get('size_bytes', 0)} bytes)"
            return f"\u274c Backup failed: {result.get('error', 'unknown')}"
        if action == "restore":
            backup_id = args[1] if len(args) > 1 else "latest"
            result = await restore_from_cloud(backup_id)
            if result.get("success"):
                return f"\u2705 Restored from {result.get('backup_id', 'latest')} ({result.get('size_bytes', 0)} bytes)"
            return f"\u274c Restore failed: {result.get('error', 'unknown')}"
        if action == "list":
            result = await list_backups()
            if not result.get("backups"):
                return "No backups found." + (f" ({result.get('error', '')})" if result.get("error") else "")
            lines = ["\U0001f4be Cloud Backups:\n"]
            for b in result["backups"]:
                lines.append(f"  {b.get('backup_id', '?'):20s}  {b.get('timestamp', '?'):25s}  {b.get('size_bytes', 0)} bytes")
            return "\n".join(lines)
        # Default: show status
        state = get_backup_state()
        last = state.get("last_backup", "never")
        return f"\U0001f4be Backup status: last backup at {last}\nUsage: /backup [now | list | restore [id]]"
    _r("backup", "Cloud backup: run, list, or restore", "10_cloud", cmd_backup,
       usage="backup [now | list | restore [backup_id] | status]")

    # ═══════════════════════════════════════════════════════════════
    # CONFIGURATION (87-94)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_config(ctx):
        args = ctx.get("_args", [])
        if args and args[0] == "set" and len(args) > 2:
            key, value = args[1], " ".join(args[2:])
            env_path = Path(".env")
            lines = env_path.read_text().split("\n") if env_path.exists() else []
            found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{key}="):
                    lines[i] = f"{key}={value}"
                    found = True
                    break
            if not found:
                lines.append(f"{key}={value}")
            env_path.write_text("\n".join(lines))
            os.environ[key] = value
            return f"Config set: {key}={value}"
        if args and args[0] == "path":
            paths = [
                ("Config", "windyfly.toml"),
                ("Environment", ".env"),
                ("Personality", "SOUL.md"),
                ("Database", os.environ.get("WINDYFLY_DB_PATH", "data/windyfly.db")),
                ("Audio", "data/sounds/its-alive.wav"),
                ("Logs", "data/windyfly.log"),
            ]
            lines = ["File locations:\n"]
            for label, p in paths:
                exists = "✓" if Path(p).exists() else "✗"
                lines.append(f"  {exists} {label:14s} {p}")
            return "\n".join(lines)
        if args and args[0] == "reset":
            return "Run 'windy config reset' from terminal to re-run the setup wizard."
        lines = ["Configuration (secrets redacted):\n"]
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text().strip().split("\n"):
                if "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    if any(s in key.upper() for s in ["KEY", "SECRET", "TOKEN", "PASSWORD"]):
                        val = val[:4] + "***" if len(val) > 4 else "***"
                    lines.append(f"  {key}={val}")
        return "\n".join(lines)

    _r("config", "View or edit configuration", "10_config", cmd_config,
        aliases=["settings"], usage="config [set <key> <value> | path | reset]")

    async def cmd_language(ctx):
        args = ctx.get("_args", [])
        if args:
            os.environ["PREFERRED_LANGUAGE"] = args[0]
            return f"Language set to: {args[0]}"
        lang = os.environ.get("PREFERRED_LANGUAGE", "en")
        return f"Language: {lang}\nChange: /language <code> (e.g. es, fr, de, ja)"
    _r("language", "Show or set preferred language", "10_config", cmd_language, aliases=["lang"])

    async def cmd_timezone(ctx):
        args = ctx.get("_args", [])
        if args:
            os.environ["TIMEZONE"] = args[0]
            return f"Timezone set to: {args[0]}"
        tz = os.environ.get("TIMEZONE", "UTC")
        return f"Timezone: {tz}\nChange: /timezone <tz> (e.g. US/Eastern, Europe/London)"
    _r("timezone", "Show or set timezone", "10_config", cmd_timezone, aliases=["tz"])

    async def cmd_theme(ctx):
        args = ctx.get("_args", [])
        if args:
            os.environ["OUTPUT_THEME"] = args[0]
            return f"Theme set to: {args[0]}"
        theme = os.environ.get("OUTPUT_THEME", "dark")
        return f"Theme: {theme}\nAvailable: dark, light, minimal"
    _r("theme", "Show or set output theme", "10_config", cmd_theme)

    # ═══════════════════════════════════════════════════════════════
    # MAINTENANCE & BACKUP (95-100)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_export(ctx):
        import tarfile
        from datetime import datetime
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"windyfly-backup-{timestamp}.tar.gz"
        files_to_backup = []
        for f in ["data/windyfly.db", ".env", "windyfly.toml", "SOUL.md"]:
            if Path(f).exists():
                files_to_backup.append(f)
        for f in Path("data/sounds").glob("*"):
            files_to_backup.append(str(f))
        for f in Path("data").glob("birth_certificate_*.pdf"):
            files_to_backup.append(str(f))
        try:
            with tarfile.open(filename, "w:gz") as tar:
                for f in files_to_backup:
                    tar.add(f)
            return f"Backup saved: {filename} ({len(files_to_backup)} files)"
        except Exception as e:
            return f"Backup failed: {e}"
    _r("export", "Backup everything (db, config, soul, audio) to tar.gz", "11_maintenance", cmd_export,
       aliases=["backup"])

    async def cmd_import(ctx):
        args = ctx.get("_args", [])
        if not args:
            return "Usage: /import <backup-file.tar.gz>"
        return f"Run from terminal: tar xzf {args[0]} (will overwrite current data)"
    _r("import", "Restore from backup", "11_maintenance", cmd_import, aliases=["restore"], usage="import <file>")

    async def cmd_reset_agent(ctx):
        args = ctx.get("_args", [])
        if not args:
            return ("Usage: /reset soft (clear memory, keep config) or /reset hard (delete everything)\n"
                    "⚠ This is irreversible!")
        if args[0] == "soft":
            return "RESET_SOFT"
        if args[0] == "hard":
            return "⚠ To factory reset, run from terminal: windy reset --hard\nYou must type RESET to confirm."
        return "Usage: /reset soft or /reset hard"
    _r("factory-reset", "Factory reset (soft or hard)", "11_maintenance", cmd_reset_agent,
       dangerous=True, usage="factory-reset <soft|hard>")

    async def cmd_clean(ctx):
        cleaned = []
        for f in [Path("data/windyfly.pid"), Path("data/windyfly.lock")]:
            if f.exists():
                f.unlink()
                cleaned.append(str(f))
        return f"Cleaned: {', '.join(cleaned)}" if cleaned else "Nothing to clean."
    _r("clean", "Remove temp files, stale locks", "11_maintenance", cmd_clean)

    async def cmd_migrate(ctx):
        if not _db:
            return "Database not available."
        try:
            _db.run_migrations()
            return "Database migrations completed."
        except Exception as e:
            return f"Migration error: {e}"
    _r("migrate", "Run database migrations after update", "11_maintenance", cmd_migrate)

    # ═══════════════════════════════════════════════════════════════
    # DEVELOPER & ADVANCED (101-108)
    # ═══════════════════════════════════════════════════════════════

    async def cmd_repl(ctx):
        return "Run 'windy repl' from terminal to drop into Python REPL with agent context."
    _r("repl", "Developer Python REPL with agent context", "12_developer", cmd_repl, dangerous=True)

    async def cmd_test(ctx):
        return "TEST_SELF"
    _r("test", "Run self-test (send '2+2' and verify)", "12_developer", cmd_test, aliases=["selftest"])

    async def cmd_run(ctx):
        cmd = ctx.get("_raw", "")
        if not cmd:
            return "Usage: /run <shell command>"
        try:
            # NO shell=True — user input is tokenised with shlex so a
            # trailing `; rm -rf ~` can't reach the interpreter even if
            # the trust gate and channel policy are somehow bypassed.
            import shlex
            argv = shlex.split(cmd)
            if not argv:
                return "Usage: /run <shell command>"
            result = subprocess.run(argv, capture_output=True, text=True, timeout=30)
            output = result.stdout or result.stderr
            return output[:2000] if output else "(no output)"
        except FileNotFoundError:
            return f"Command not found: {cmd.split()[0]}"
        except ValueError as e:
            return f"Could not parse command: {e}"
        except subprocess.TimeoutExpired:
            return "Command timed out (30s limit)"
        except Exception as e:
            return f"Error: {e}"
    _r("run", "Execute a shell command", "12_developer", cmd_run,
       aliases=["exec", "sh"], dangerous=True, usage="run <command>")

    async def cmd_web(ctx):
        url = ctx.get("_raw", "")
        if not url:
            return "Usage: /web <url>"
        from windyfly.safe_fetch import SSRFBlocked, safe_fetch
        try:
            result = safe_fetch(url, timeout=10, max_bytes=2000, allow_one_redirect=True)
            return (
                f"Fetched {result.url} ({result.status_code}, {result.length} chars):\n"
                f"{result.text}"
            )
        except SSRFBlocked as blocked:
            return f"Refused: {blocked}"
        except Exception as e:
            return f"Error fetching {url}: {e}"
    _r("web", "Fetch a URL and show content", "12_developer", cmd_web,
       aliases=["fetch", "curl"], dangerous=True, usage="web <url>")

    async def cmd_diff(ctx):
        try:
            result = subprocess.run(["git", "diff", "--stat"], capture_output=True, text=True)
            return result.stdout or "No changes."
        except Exception:
            return "Git not available."
    _r("diff", "Show recent git changes", "12_developer", cmd_diff)

    async def cmd_git(ctx):
        cmd = ctx.get("_raw", "")
        if not cmd:
            return "Usage: /git <command> (e.g. /git status, /git log --oneline -5)"
        try:
            # NO shell=True — splits /git log;rm -rf ~ into literal argv
            # rather than piping to the shell.
            import shlex
            argv = ["git", *shlex.split(cmd)]
            result = subprocess.run(argv, capture_output=True, text=True, timeout=15)
            return result.stdout[:2000] or result.stderr[:500] or "(no output)"
        except ValueError as e:
            return f"Could not parse command: {e}"
        except Exception as e:
            return f"Error: {e}"
    _r("git", "Run a git command", "12_developer", cmd_git,
       dangerous=True, usage="git <command>")

    async def cmd_help(ctx):
        args = ctx.get("_args", [])
        plat = ctx.get("platform", "terminal")
        if args:
            cmd = registry.get(args[0])
            if cmd:
                eco = " ⚡ (Windy Fly exclusive)" if cmd.ecosystem_only else ""
                usage_str = f"\nUsage: {cmd.usage}" if cmd.usage else ""
                aliases = f"\nAliases: {', '.join(cmd.aliases)}" if cmd.aliases else ""
                return f"/{cmd.name} — {cmd.description}{eco}{usage_str}{aliases}\nCategory: {cmd.category.split('_', 1)[-1]}"
            return f"Unknown command: {args[0]}"
        return registry.format_help(plat)
    _r("help", "Show all commands or help for a specific command", "13_help", cmd_help,
       aliases=["commands", "?"], usage="help [command]")
