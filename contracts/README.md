# contracts/ — Windy Fly's agent-control manifest

> **`control.mcp.v1.json` is RETIRED (2026-11, lean Windy Fly step R1; Boss RULING, BOARD 2026-11-11T05:06:52Z).** The
> native MCP control server it describes (`src/windyfly/mcp_server/`, the `windy-mcp-server` command) is removed: it was
> never wired into the agent's start and had no caller. The file stays as a record, marked `"status": "retired"`; nothing
> serves it.

What it described, for the record: a native MCP server that projected the agent's in-process capability registry
(`tools/list`, `tools/call`, band-gated and audited), governed by the Agent Control Doctrine (ADR-060 in
`sneakyfree/windy-contracts`). Its history is in git (`src/windyfly/mcp_server/`, `tests/test_mcp_control_bridge.py`).
