"""Pure registry<->MCP mapping. No MCP SDK import — this is the tested core.

The whole point of Class A: the MCP surface is a thin projection of the
Capability Plane. `tools/list` is the registry's band-filtered schemas
reshaped to MCP's `{name, description, inputSchema}`; a tool result is the
capability's return value wrapped in an MCP content block, mirroring the
`{ok: false}` envelope as `isError`. Nothing is re-implemented, so nothing
can drift from what the agent's own LLM sees.
"""

from __future__ import annotations

import json
from typing import Any

from windyfly.agent.capabilities.descriptor import Band
from windyfly.agent.capabilities.registry import CapabilityRegistry


def registry_to_mcp_tools(registry: CapabilityRegistry, band: Band) -> list[dict[str, Any]]:
    """The band-filtered registry as MCP tool definitions.

    Band filtering is the load-bearing property (ADR-060 §3.5): a caller
    below a capability's band_required never even SEES it in tools/list.
    We reshape the registry's OpenAI-style schema — we do not re-derive it,
    so the MCP tool list is identical to the agent's own tool list at the
    same band.
    """
    tools: list[dict[str, Any]] = []
    for schema in registry.tool_schemas_for_band(band):
        fn = schema["function"]
        tools.append(
            {
                "name": fn["name"],
                "description": fn["description"],
                "inputSchema": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return tools


def capability_result_to_mcp(result: Any) -> dict[str, Any]:
    """Wrap a capability's return value as an MCP tool result.

    A capability that returns ``{"ok": False, ...}`` surfaces as ``isError``
    so the calling agent sees the failure structurally (the same contract
    Word/Mind packets use). Everything else is a normal content block.
    """
    text = result if isinstance(result, str) else json.dumps(result, indent=2, default=str)
    is_error = isinstance(result, dict) and result.get("ok") is False
    block: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if is_error:
        block["isError"] = True
    return block


def band_denied_result(capability_id: str, message: str) -> dict[str, Any]:
    """MCP result for a band/permission denial — structured, never silent."""
    return {
        "isError": True,
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {"ok": False, "error": "denied", "capability": capability_id, "detail": message}
                ),
            }
        ],
    }
