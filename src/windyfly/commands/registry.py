"""Unified command registry — one set of commands, every platform.

Terminal: `windy doctor`
Telegram: `/doctor`
Discord: `/doctor`
Slack: `/doctor`
Matrix: `!doctor`
WhatsApp: `/doctor`

All call the same function. The output is plain text that the
channel adapter sends back to the user.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Any

logger = logging.getLogger(__name__)


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

        # Dangerous command gating — require explicit confirmation
        if cmd.dangerous:
            confirmed = (
                "--confirm" in args_list
                or "CONFIRM" in args_list
                or "yes" in args_list
            )
            platform = (context or {}).get("platform", "terminal")
            if not confirmed:
                return (
                    f"⚠ /{cmd.name} is a dangerous command that may cause data loss.\n"
                    f"To confirm, type: /{cmd.name} --confirm"
                )
            # Remove confirmation tokens from args
            args_list = [a for a in args_list if a not in ("--confirm", "CONFIRM", "yes")]

        try:
            ctx = context or {}
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
