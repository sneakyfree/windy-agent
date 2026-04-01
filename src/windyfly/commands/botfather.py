"""Generate BotFather /setcommands payload from the unified registry.

Usage:
    uv run python -m windyfly.commands.botfather

Then paste the output into @BotFather → /setcommands.
Telegram limits to 100 commands of max 32 chars each.
"""

from __future__ import annotations

import sys


def generate_botfather_commands() -> str:
    """Return the formatted command list for BotFather /setcommands."""
    from windyfly.commands.setup import init_all_commands
    from windyfly.commands.registry import registry

    init_all_commands()

    lines = []
    for cmd in registry.all():
        # BotFather: command names must be 1-32 chars, lowercase, no spaces
        name = cmd.name.replace("-", "")[:32]
        desc = cmd.description[:256]
        eco = " [ecosystem]" if cmd.ecosystem_only else ""
        lines.append(f"{name} - {desc}{eco}")

    # Telegram allows max 100 commands
    return "\n".join(lines[:100])


def main() -> None:
    output = generate_botfather_commands()
    print("=" * 60)
    print("Paste the following into @BotFather → /setcommands:")
    print("=" * 60)
    print(output)
    print("=" * 60)
    print(f"\nTotal: {len(output.splitlines())} commands")
    print("\nSteps:")
    print("  1. Open Telegram → @BotFather")
    print("  2. Send /setcommands")
    print("  3. Select your bot")
    print("  4. Paste the block above")


if __name__ == "__main__":
    main()
