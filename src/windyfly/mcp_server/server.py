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

from windyfly.agent.capabilities.descriptor import Band
from windyfly.mcp_server.bridge import (
    band_denied_result,
    capability_result_to_mcp,
    registry_to_mcp_tools,
)


def build_registry() -> "CapabilityRegistry":  # noqa: F821
    """Build the Capability Plane this MCP surface exposes.

    SEAM (Fly lane): production parity means reusing boot's
    ``default_capability_registration_sequence`` with a real context
    (db + config + write_queue), so every capability the agent has is
    healable over MCP. That wiring needs the boot context and belongs in a
    focused PR — see docs/handoffs/FLY-LANE in windy-contracts.

    For now this registers the context-free capability modules so the
    entrypoint is runnable and honest about its (partial) surface rather
    than importing a helper that doesn't exist. Adding a module here is
    safe; it never touches the running agent (this entrypoint is opt-in).
    """
    from windyfly.agent.capabilities.registry import CapabilityRegistry
    from windyfly.agent.capabilities.windyword import register_windyword_capabilities

    registry = CapabilityRegistry()
    register_windyword_capabilities(registry, None)  # config-free, proven safe
    return registry


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
