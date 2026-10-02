"""contract/persona-schema.json (windy.persona.v2): shape pinned so Chat, Mobile and Hub can vendor it."""

from __future__ import annotations

import json
from pathlib import Path

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "contract" / "persona-schema.json").read_text())


def test_contract_and_ten_unique_bipolar_sliders():
    assert SCHEMA["contract"] == "windy.persona.v2"
    s = SCHEMA["sliders"]
    assert s["range"] == {"min": -10, "max": 10, "step": 1} and s["default"] == 0
    ids = [i["id"] for i in s["items"]]
    assert len(ids) == 10 == len(set(ids))
    assert set(ids) == {"humor", "warmth", "sarcasm", "directness", "brevity",
                        "formality", "pushback", "curiosity", "proactivity", "creativity"}
    assert all(i["label"] and i["low"] and i["high"] for i in s["items"])


def test_pure_and_memory_dial():
    assert SCHEMA["pure"]["type"] == "boolean" and SCHEMA["pure"]["default"] is False
    m = SCHEMA["memory"]
    assert (m["min"], m["max"]) == (0, 3) and [lv["value"] for lv in m["levels"]] == [0, 1, 2, 3]


def test_atomic_write_with_if_match():
    assert "If-Match" in SCHEMA["api"]["write"] and "PUT /api/v2/agent/panel/:agent/state" in SCHEMA["api"]["write"]
