"""Drift test: windy-agent only writes message keys the Chat contract marks live.

The vendored contract is a snapshot of sneakyfree/windy-chat
contracts/windy.message-keys.v1.json (see the .SOURCE file). Re-vendor from the
merge commit when Chat changes it; never edit the snapshot by hand.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "contracts" / "windy.message-keys.v1.json").read_text())
KEYS = CONTRACT["keys"]


def _written_keys() -> set[str]:
    found: set[str] = set()
    for p in (ROOT / "src" / "windyfly").rglob("*.py"):
        found |= set(re.findall(r"""["'](uk\.windypro\.[a-z_]+)["']""", p.read_text()))
    return found


def test_contract_shape():
    assert CONTRACT["contract"] == "windy.message-keys.v1"
    assert KEYS["uk.windypro.model"]["status"] == "live"


def test_only_live_keys_are_written():
    written = _written_keys()
    assert "uk.windypro.model" in written
    for k in written:
        assert k in KEYS, f"{k} is not in the contract"
        assert KEYS[k]["status"] == "live", f"{k} is {KEYS[k]['status']}, do not write it"


def test_reserved_keys_not_written():
    written = _written_keys()
    assert "uk.windypro.raw" not in written
    assert "uk.windypro.fallback" not in written
