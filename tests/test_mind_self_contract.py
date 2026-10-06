"""Windy Mind's agent-self contract (C16), vendored from windy-contracts (schema/mind/).

If this fails, the vendored file changed: re-vendor from windy-contracts, re-check src/windyfly/agent/mind_self.py
against it, then update the pinned hashes here.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "contract" / "mind"

PINNED = {
    "agent-self.v1.json": "16a36545bb5a74a49fc8341349daafa1591c65b89ab29f1aa38e5d12b5892721",
    "mind-refusal.v1.json": "892e35b410b6728f3dbf91ab7f43cdcad64f49cc513743d314f2224ee9e3c0db",
}


def _sha(name: str) -> str:
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def test_vendored_contracts_are_the_pinned_ones():
    for name, sha in PINNED.items():
        assert _sha(name) == sha, f"{name} changed: re-vendor + re-check mind_self.py"


def test_the_routes_mind_self_uses_are_in_the_contract():
    d = json.loads((ROOT / "agent-self.v1.json").read_text())
    routes = {(e["method"], e["path"]) for e in d["x-endpoints"]}
    assert ("GET", "/v1/agents/me") in routes
    assert ("PUT", "/v1/agents/me/model") in routes
    assert ("DELETE", "/v1/agents/me/model") in routes


def test_the_fields_and_codes_mind_self_reads_exist():
    d = json.loads((ROOT / "agent-self.v1.json").read_text())
    props = d["$defs"]["AgentSelf"]["properties"]
    for field in ("state", "state_reason", "effective", "chain", "burn", "may_pick", "picked_by", "config_version"):
        assert field in props
    assert set(props["may_pick"]["properties"]["mode"]["enum"]) == {"free_and_own", "none", "list"}
    assert set(props["picked_by"]["enum"]) == {"owner", "agent", None}
    raw = (ROOT / "agent-self.v1.json").read_text()
    assert "not_allowed_for_agent" in raw and "switch_rate" in raw
    # no money anywhere in the agent's own view
    assert not [k for k in props["burn"]["properties"] if "cost" in k or "usd" in k]
