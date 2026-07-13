"""Windy Fly's agent-control MCP surface (ADR-060 Class A).

Fly is both patient and doctor. This package is the PATIENT half: it exposes
Fly's own Capability Plane as an MCP server so an OUTSIDE agent can enumerate
Fly's knobs, read its health, and drive it back to green — the doctrine's
"Fix this for me" promise applied to the agent itself.

Class A does NOT get a Loom-woven JS proxy (that would HTTP-hop to an
in-process Python registry — backwards). Instead Fly wraps its EXISTING
registry in a native MCP server: `tools/list` is the registry's band-filtered
schemas, `tools/call` is `registry.invoke_sync` — so band-gating, audit, and
discovery-filtering come for free. The doctrine calls this "a serialization
shim" (§2), and that is exactly what `bridge.py` is.

`bridge.py` is pure (no MCP SDK) and is what the tests exercise against the
real registry. `server.py` is the thin stdio/HTTP wrapper that needs the
optional `mcp` dependency (install extra `mcp-server`); it is NOT wired into
the agent's boot — a separate entrypoint, zero impact on the running agent.
"""

from windyfly.mcp_server.bridge import (  # noqa: F401
    capability_result_to_mcp,
    registry_to_mcp_tools,
)
