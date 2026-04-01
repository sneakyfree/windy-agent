"""CLI channel for Windy Fly.

Interactive terminal interface for development and testing.
Uses Rich for colored output.
"""

from __future__ import annotations

import uuid
from typing import Any

from rich.console import Console
from rich.theme import Theme

from windyfly.agent.loop import agent_respond
from windyfly.memory.database import Database
from windyfly.memory.write_queue import WriteQueue
from windyfly.tools.registry import ToolRegistry
from windyfly.tools.web_search import register_web_search_tool
from windyfly.tools.windy_api import register_windy_tools

theme = Theme({
    "fly": "bold cyan",
    "user_label": "bold green",
    "info": "dim",
})
console = Console(theme=theme)


def run_cli(config: dict[str, Any]) -> None:
    """Run the interactive CLI chat interface.

    Creates a Database, starts the WriteQueue, and enters a read-eval-print
    loop. Type 'quit' or 'exit' to stop.

    Args:
        config: Loaded config dict.
    """
    db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
    db = Database(db_path)
    write_queue = WriteQueue()
    write_queue.start()

    # Initialize tool registry
    tool_registry = ToolRegistry()
    register_windy_tools(tool_registry)
    register_web_search_tool(tool_registry)

    # Register sub-agent tool (G11)
    from windyfly.agent.sub_agents import register_sub_agent_tool
    register_sub_agent_tool(tool_registry, config, db, write_queue)

    session_id = str(uuid.uuid4())

    console.print()
    console.print("🪰 [fly]Windy Fly[/fly] is ready. Type [info]'quit'[/info] to exit.")
    console.print("[info]Session:[/info]", session_id[:8])
    console.print()

    try:
        while True:
            try:
                user_input = console.input("[user_label]You:[/user_label] ")
            except (EOFError, KeyboardInterrupt):
                break

            user_input = user_input.strip()
            if not user_input:
                continue
            if user_input.lower() in ("quit", "exit"):
                break

            # Command detection — /commands work in terminal chat too
            if user_input.startswith("/") or user_input.startswith("!"):
                import asyncio
                from windyfly.commands.registry import registry
                response = asyncio.get_event_loop().run_until_complete(
                    registry.execute(user_input.lstrip("/!"), {"platform": "terminal"})
                )
                console.print(f"[fly]Fly:[/fly] {response}")
                console.print()
                continue

            try:
                response = agent_respond(config, db, write_queue, user_input, session_id, tool_registry)
                console.print(f"[fly]Fly:[/fly] {response}")
                console.print()
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")
                console.print()

    finally:
        console.print("\n[info]Shutting down...[/info]")
        write_queue.stop()
        db.close()
        console.print("[info]Goodbye! 🪰[/info]")
