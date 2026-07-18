"""Unified command registry — one set of commands, every platform.

Terminal: `windy doctor`
Telegram: `/doctor`
Discord: `/doctor`
Slack: `/doctor`
Matrix: `!doctor`
WhatsApp: `/doctor`

All call the same function. The output is plain text that the
channel adapter sends back to the user.

### Channel policy

`CHANNEL_POLICY` maps platform → allowed category prefixes. Local
platforms (`terminal`, `cli`) get everything. Remote platforms get a
restricted set — no developer commands (`12_developer`), no cloud
ops (`10_cloud`), no factory-reset etc. Even when a remote caller
knows a valid command name, the registry refuses if the category
isn't on the platform's allowlist.

### Trust gating

Commands in `12_developer` AND commands marked `dangerous=True` are
additionally gated by the Eternitas trust check. The action name
used for the gate comes from `_TRUST_ACTION_MAP` — defaulting to
`run_command` for anything in the developer category. Gate failure
denies with a clear message rather than raising.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)


# Platforms that run locally with the owner physically at the keyboard.
# Everything else is a remote adapter (messaging bots, email, SMS).
_LOCAL_PLATFORMS = frozenset({"terminal", "cli"})

# Categories remote callers may invoke. Anything that mutates runtime
# state, executes code, or touches the cloud is off-limits remotely.
# Individual `dangerous=True` commands inside an allowed category still
# require the confirmation token AND pass the trust gate.
_REMOTE_ALLOWED_CATEGORIES = frozenset({
    "01_process",      # version, ping, uptime, status (kill is dangerous→gated)
    "02_diagnostics",
    "03_chat",
    "04_model",        # read-only model/provider info only
    "05_personality",
    "06_memory",
    "08_budget",
    "09_identity",     # whoami, channels, passport view
    "13_help",
    # Added 2026-05-20 per Grant's "stress test until solid +
    # bot must do things for grandma out of the box" mandate.
    # The original goal explicitly listed sending email + SMS
    # as core capabilities — pre-PR, /send-mail and /sms were
    # silently telegram-blocked at this allow-list, so the bot
    # could NOT perform its core advertised function from the
    # chat surface. Mutating commands in these categories
    # carry ``dangerous=True`` (require ``--confirm``) so an
    # adversarial telegram message can't fire a real send
    # without an explicit confirmation token. Read-only views
    # (/inbox, /read-mail, /mail-stats, /sms-history,
    # /voicemail) stay one-tap — they don't mutate state.
    "14_email",        # /inbox, /read-mail (R/O) + /send-mail, /reply-mail (gated)
    "15_phone",        # /sms-history, /voicemail (R/O) + /sms (gated)
})

# Channel → allowed category set. Unlisted platforms get the remote
# default, which is the safe fallback.
CHANNEL_POLICY: dict[str, frozenset[str]] = {
    "terminal": frozenset(),  # empty set = allow-all (enforced below)
    "cli": frozenset(),
}

# Map a command name → the trust gate action we should require before
# execution. When a command isn't in this table but is `dangerous=True`
# or in `12_developer`, we default to `run_command`.
_TRUST_ACTION_MAP: dict[str, str] = {
    "run": "run_command",
    "exec": "run_command",
    "sh": "run_command",
    "repl": "run_command",
    "git": "commit_push",
    "web": "run_command",
    "fetch": "run_command",
    "curl": "run_command",
    "factory-reset": "run_command",
    "kill": "run_command",
    "shutdown": "run_command",
    "deploy": "run_command",
}


@dataclass
class Command:
    name: str
    description: str
    category: str
    handler: Callable[..., Awaitable[str] | str]
    aliases: list[str] = field(default_factory=list)
    ecosystem_only: bool = False  # True = Windy Fly exclusive, not in HiFly
    dangerous: bool = False
    usage: str = ""  # e.g. "model set <name>"


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}
        self._aliases: dict[str, str] = {}

    def register(self, cmd: Command) -> None:
        self._commands[cmd.name] = cmd
        for alias in cmd.aliases:
            self._aliases[alias] = cmd.name

    def get(self, name: str) -> Command | None:
        name = name.lstrip("/!").split()[0]
        if name in self._commands:
            return self._commands[name]
        canonical = self._aliases.get(name)
        return self._commands.get(canonical) if canonical else None

    def all(self) -> list[Command]:
        return sorted(self._commands.values(), key=lambda c: (c.category, c.name))

    def by_category(self) -> dict[str, list[Command]]:
        cats: dict[str, list[Command]] = {}
        for cmd in self._commands.values():
            cats.setdefault(cmd.category, []).append(cmd)
        return {k: sorted(v, key=lambda c: c.name) for k, v in sorted(cats.items())}

    async def execute(self, raw_input: str, context: dict | None = None) -> str:
        parts = raw_input.lstrip("/!").split()
        if not parts:
            return "Type /help for available commands."

        name = parts[0]
        args_list = parts[1:]
        cmd = self.get(name)

        if not cmd:
            return f"Unknown command: {name}. Type /help for available commands."

        ctx = context or {}
        platform = str(ctx.get("platform", "unknown"))

        # Usage telemetry (name only, never args — privacy hard line).
        # Fire-and-forget; feeds the data-driven command-surface prune.
        try:
            from windyfly.observability.admin_telemetry import (
                emit_command_invoked,
            )
            emit_command_invoked(ctx.get("write_queue"), cmd.name, platform)
        except Exception:  # noqa: BLE001 — telemetry never blocks a command
            pass

        # Channel policy — remote channels cannot invoke developer,
        # cloud, or maintenance commands. Local platforms (terminal /
        # cli) bypass the category check entirely.
        if not _platform_may_invoke(platform, cmd.category):
            logger.warning(
                "Channel policy denied: platform=%s cmd=%s category=%s",
                platform, cmd.name, cmd.category,
            )
            return (
                f"Command /{cmd.name} is not allowed from {platform}. "
                f"Run it from the owner's terminal."
            )

        # Trust gate — sensitive commands go through Eternitas before
        # dispatch. We fail closed on TrustDenied; fail open on any
        # other exception so a broken trust service doesn't nuke the
        # CLI for the owner.
        if _needs_trust_gate(cmd):
            action = _trust_action_for(cmd.name)
            try:
                from windyfly.trust.gate import TrustDenied, require_trust_sync

                require_trust_sync(action)
            except TrustDenied as denied:
                logger.warning("Trust gate denied /%s: %s", cmd.name, denied)
                return f"Trust gate denied /{cmd.name}: {denied.reason}"
            except Exception as exc:
                logger.warning("Trust gate check failed for /%s (fail-open): %s", cmd.name, exc)

        # Dangerous command gating — require explicit confirmation
        if cmd.dangerous:
            confirmed = (
                "--confirm" in args_list
                or "CONFIRM" in args_list
                or "yes" in args_list
            )
            if not confirmed:
                return (
                    f"⚠ /{cmd.name} is a dangerous command that may cause data loss.\n"
                    f"To confirm, type: /{cmd.name} --confirm"
                )
            # Remove confirmation tokens from args
            args_list = [a for a in args_list if a not in ("--confirm", "CONFIRM", "yes")]

        try:
            ctx["_args"] = args_list
            ctx["_raw"] = " ".join(args_list)
            result = cmd.handler(ctx)
            if hasattr(result, "__await__"):
                result = await result
            return str(result)
        except Exception as exc:
            logger.error("Command /%s failed: %s", name, exc)
            return f"Command failed: {exc}"

    # Category display: maps internal category code → (sort_order,
    # emoji, grandma-readable label). The internal codes (01_process,
    # 02_diagnostics, etc.) become invisible to the user — she sees
    # the emoji column running down the left edge, same as the
    # Telegram slash-bar layout (PR #139).
    #
    # Sort order is urgency-weighted: rescue at the top so a
    # squinting-grandma's eye lands on /reset and /resurrect first.
    # Categories not in this map fall back to a generic emoji and
    # render at the bottom — keeps the output complete even if
    # someone adds a new category without updating this table.
    _CATEGORY_DISPLAY: tuple[tuple[str, int, str, str], ...] = (
        # Top — panic-grandma's eye lands here first.
        ("01_process",     1,  "🆘", "If something's wrong"),
        ("13_help",        2,  "❓", "Help"),
        # Most-used everyday commands next.
        ("03_chat",        3,  "💬", "Chatting with me"),
        ("08_budget",      4,  "💰", "Money"),
        ("06_memory",      5,  "🧠", "What I remember"),
        ("05_personality", 6,  "🎭", "My personality"),
        ("02_diagnostics", 7,  "ℹ️",  "Status"),
        ("04_model",       8,  "🤖", "My brain"),
        # Tools grandma uses every day (weather, reminders, todo,
        # calendar) — surface BEFORE the power-user skill-authoring
        # commands. Both share the 🧰 toolbox metaphor but one is for
        # USING tools, the other is for AUTHORING new ones.
        ("08a_tools",      9,  "🧰", "Everyday tools"),
        ("14_email",       10, "📧", "Email"),
        ("15_phone",       11, "☎️",  "Phone"),
        ("16_social",      12, "👥", "Social"),
        ("17_voice",       13, "🎙", "Voice"),
        ("07_skills",      14, "🛠",  "Skills (power-user)"),
        # Identity + permissions.
        ("09_identity",    15, "🪪", "Who I am"),
        ("18_permissions", 16, "🔐", "Permissions"),
        # Operator-tier — grandma rarely lands here. Two cloud
        # buckets exist for historical reasons; surface the more
        # user-facing one (10_cloud: connect, deploy basics) before
        # the deeper-ops one (19_cloud: instance management).
        ("10_cloud",       17, "☁️",  "Cloud"),
        ("19_cloud",       18, "🌩",  "Cloud (advanced)"),
        ("10_config",      19, "⚙️",  "Settings"),
        ("11_maintenance", 20, "🔧", "Maintenance"),
        ("12_developer",   21, "💻", "Developer"),
    )

    def format_help(self, platform: str = "terminal") -> str:
        """Categorized, emoji-coded /help output.

        Layout matches the Telegram slash-bar menu (PR #139): emoji
        prefix per section, urgency-weighted ordering. Grandma's eye
        scans the column of emojis instead of reading every line.

        Telegram receives this with parse_mode='Markdown'; the bold
        section headers + italic recovery hint render cleanly. In a
        terminal they appear as literal asterisks — readable but
        not styled.
        """
        prefix = {
            "telegram": "/",
            "discord": "/",
            "slack": "/",
            "matrix": "!",
            "terminal": "windy ",
        }.get(platform, "/")

        # Build a quick lookup for category metadata + an ordering
        # key that puts unknown categories last (sorted by name).
        meta = {code: (rank, emoji, label)
                for code, rank, emoji, label in self._CATEGORY_DISPLAY}
        max_rank = len(self._CATEGORY_DISPLAY)

        def cat_sort_key(category: str) -> tuple[int, str]:
            if category in meta:
                return (meta[category][0], category)
            return (max_rank + 1, category)

        # Header — short greeting, not a wall of text. Grandma reads
        # the first three lines; we want them to set context fast.
        lines = [
            "🪰 *Windy Fly — what I can do*",
            "",
            "_Tap any command, or just type it. If I stop responding,"
            " try /resurrect — I'll switch to a free local brain so"
            " we can keep talking._",
            "",
        ]

        eco_marker_used = False
        for category in sorted(self.by_category().keys(), key=cat_sort_key):
            cmds = self.by_category()[category]
            if category in meta:
                _, emoji, label = meta[category]
            else:
                # Unknown category — derive a friendly label from the
                # internal code. Better than dropping the commands.
                emoji = "•"
                label = category.split("_", 1)[-1].replace("_", " ").title()

            eco_cmds = [c for c in cmds if c.ecosystem_only]
            core_cmds = [c for c in cmds if not c.ecosystem_only]

            if core_cmds:
                lines.append(f"{emoji} *{label}*")
                for cmd in core_cmds:
                    lines.append(f"  {prefix}{cmd.name} — {cmd.description}")
                lines.append("")

            if eco_cmds:
                lines.append(f"{emoji} *{label}* (Windy Fly exclusive)")
                for cmd in eco_cmds:
                    lines.append(f"  {prefix}{cmd.name} — {cmd.description} ⚡")
                lines.append("")
                eco_marker_used = True

        if eco_marker_used:
            lines.append("⚡ = Windy Fly exclusive (not available in HiFly forks)")
        lines.append(f"_Type {prefix}help <command> for details on any command._")
        return "\n".join(lines)

    def count(self) -> tuple[int, int]:
        core = sum(1 for c in self._commands.values() if not c.ecosystem_only)
        eco = sum(1 for c in self._commands.values() if c.ecosystem_only)
        return core, eco


# Global singleton
registry = CommandRegistry()


def is_command(text: str) -> bool:
    return bool(text) and text[0] in ("/", "!") and len(text) > 1 and text[1:2].isalpha()


def parse_command(text: str) -> str:
    return text.lstrip("/!").strip()


def _platform_may_invoke(platform: str, category: str) -> bool:
    """True when a caller on `platform` may invoke a command of `category`.

    Local platforms bypass; remote platforms check the default remote
    allow-list. Per-platform overrides live in CHANNEL_POLICY.
    """
    if platform in _LOCAL_PLATFORMS:
        return True
    override = CHANNEL_POLICY.get(platform)
    if override:
        return category in override
    return category in _REMOTE_ALLOWED_CATEGORIES


def _needs_trust_gate(cmd: Command) -> bool:
    """Developer-category and dangerous commands pass through the gate."""
    return cmd.category == "12_developer" or cmd.dangerous


def _trust_action_for(cmd_name: str) -> str:
    """Map a command name to the Eternitas gate action."""
    return _TRUST_ACTION_MAP.get(cmd_name, "run_command")
