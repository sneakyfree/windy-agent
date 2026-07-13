# contracts/ — Windy Fly's agent-control manifest

`control.mcp.v1.json` is the **canonical source of truth** for Windy Fly's
agent-control surface, governed by the Agent Control Doctrine (**ADR-060** in
`sneakyfree/windy-contracts`).

**Class A = a NATIVE server, not a woven proxy.** Fly's control surface is its
in-process Capability Plane, projected over MCP by `src/windyfly/mcp_server/`
(`server: native` in `weave.json`). The Loom validates this manifest and
generates the conformance driver, but emits **no** JS packet or Python twin —
a woven proxy would HTTP-hop to an in-process registry, which is backwards.

- `tools/list` = `registry.tool_schemas_for_band(caller_band)`;
  `tools/call` = `registry.invoke` — band-gating + audit come for free.
- The manifest declares the **healing baseline** Fly must expose (see
  `baseline_mapping`), mapped to real capability ids, with gaps marked. The
  LIVE surface is the full band-filtered registry, not this static list.
- Registry↔MCP parity is proven in `tests/test_mcp_control_bridge.py`.
- Run the server (opt-in): `uv sync --extra mcp-server && windy-mcp-server`.

Punch list (native server boot-wiring + the `mcp.*` client for Fly-as-doctor):
`windy-contracts/docs/handoffs/FLY-LANE-2026-07-13.md`.
