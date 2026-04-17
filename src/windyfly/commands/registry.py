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

    def format_help(self, platform: str = "terminal") -> str:
        prefix = {
            "telegram": "/",
            "discord": "/",
            "slack": "/",
            "matrix": "!",
            "terminal": "windy ",
        }.get(platform, "/")
        lines = ["🪰 Windy Fly Commands\n"]
        for category, cmds in self.by_category().items():
            label = category.upper().replace("_", " ")
            eco_cmds = [c for c in cmds if c.ecosystem_only]
            core_cmds = [c for c in cmds if not c.ecosystem_only]

            if core_cmds:
                lines.append(f"▸ {label}")
                for cmd in core_cmds:
                    lines.append(f"  {prefix}{cmd.name:20s} {cmd.description}")
                lines.append("")

            if eco_cmds:
                lines.append(f"▸ {label} (Ecosystem)")
                for cmd in eco_cmds:
                    lines.append(f"  {prefix}{cmd.name:20s} {cmd.description} ⚡")
                lines.append("")

        lines.append("⚡ = Windy Fly exclusive (not available in HiFly forks)")
        lines.append(f"\nType '{prefix}help <command>' for details on any command.")
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
