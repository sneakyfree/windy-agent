"""Drift test for the vendored Windy Mind helper (MD14).

Mind owns clients/python/mind_client.py and contracts/route-table.v1.json
(see src/windyfly/_vendor/mind_client.SOURCE). Re-vendor from Mind's commit
when it changes; never edit the snapshots by hand.
"""
import json
from pathlib import Path

from windyfly._vendor import mind_client

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / "contracts" / "route-table.v1.json").read_text())


def test_required_keys_match_contract():
    assert tuple(mind_client.REQUIRED_KEYS) == tuple(CONTRACT["required"])


def test_schema_version_is_known():
    assert mind_client.SCHEMA_VERSION == 1


def test_helper_has_the_documented_surface():
    assert hasattr(mind_client, "MindClient")
    assert hasattr(mind_client, "MindResult")
    assert issubclass(mind_client.MindUnavailableError, mind_client.MindError)
    assert issubclass(mind_client.MindRefusedError, mind_client.MindError)
