"""The MCP server entrypoint — thin wrapper over bridge.py.

Needs the optional `mcp` dependency (``uv sync --extra mcp-server``). NOT
imported at agent boot; run explicitly:

    uv run python -m windyfly.mcp_server.server            # stdio (local agent)

Band resolution: local stdio callers are the OWNER co-tenant (the person at
the machine). A future remote/streamable-HTTP entrypoint will map the
caller's EPT to a band via the same identity path the channels use, so a
remote agent gets exactly the tools its passport earns — the moat, over MCP.
"""

from __future__ import annotations

import asyncio
import os

from windyfly.agent.capabilities.descriptor import Band
from windyfly.mcp_server.bridge import (
    band_denied_result,
    capability_result_to_mcp,
    registry_to_mcp_tools,
)


def build_registry() -> "CapabilityRegistry":  # noqa: F821
    """Build the FULL Capability Plane this MCP surface exposes.

    Runs boot's ``default_capability_registration_sequence`` against a real
    context (db + write_queue + config) — the SAME registration the running
    agent performs — so every capability Fly has is healable over MCP, not
    just the context-free subset. Uses the agent's own db_path so it reads
    the same data.

    Falls back to the context-free subset if the substrate can't be opened
    (e.g. a read-only or partial environment), so the entrypoint is always
    runnable rather than dead. Set WINDY_MCP_SERVER_MINIMAL=1 to force the
    minimal surface deliberately.
    """
    from windyfly.agent.capabilities.registry import CapabilityRegistry
    from windyfly.agent.capabilities.windyword import register_windyword_capabilities

    def _minimal() -> "CapabilityRegistry":
        registry = CapabilityRegistry()
        register_windyword_capabilities(registry, None)
        return registry

    if os.environ.get("WINDY_MCP_SERVER_MINIMAL") == "1":
        return _minimal()

    try:
        from windyfly.agent.boot import (
            BootContext,
            BootSequence,
            default_capability_registration_sequence,
        )
        from windyfly.agent.capabilities import capability_registry
        from windyfly.config import load_config
        from windyfly.memory.database import Database
        from windyfly.memory.write_queue import WriteQueue
        from windyfly.tools.registry import ToolRegistry

        config = load_config()
        db_path = config.get("memory", {}).get("db_path", "data/windyfly.db")
        db = Database(db_path)
        write_queue = WriteQueue()
        write_queue.start()
        BootSequence(default_capability_registration_sequence()).run(
            BootContext(
                config=config,
                db=db,
                write_queue=write_queue,
                tool_registry=ToolRegistry(),
                capability_registry=capability_registry,
            )
        )
        return capability_registry
    except Exception as e:  # substrate unavailable — stay runnable, honestly partial
        import logging
        logging.getLogger(__name__).warning(
            "mcp_server: full registry unavailable (%s) — serving the minimal surface", e
        )
        return _minimal()


async def serve_stdio(band: Band = Band.OWNER) -> None:
    # Imported lazily so the package is importable (and testable) without the
    # optional MCP SDK present.
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    import mcp.types as types

    registry = build_registry()
    server = Server("windy-fly-control")

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return [types.Tool(**t) for t in registry_to_mcp_tools(registry, band)]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        try:
            result = await registry.invoke(name, arguments or {}, band)
            block = capability_result_to_mcp(result)
        except Exception as e:  # band denial or capability failure — never crash
            block = band_denied_result(name, str(e))
        return [types.TextContent(type="text", text=block["content"][0]["text"])]

    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def main() -> None:
    asyncio.run(serve_stdio())


if __name__ == "__main__":
    main()
