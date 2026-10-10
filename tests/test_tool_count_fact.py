"""Journey check A: every start writes the owner-turn tool count as a fact (Windy Mind's cap is 128)."""

from __future__ import annotations

import json

from windyfly.agent.boot import default_capability_registration_sequence
from windyfly.observability import tool_count


class _Tools:
    def __init__(self, n):
        self.n = n

    def get_schemas(self):
        return [{"name": f"t{i}"} for i in range(self.n)]


class _Caps:
    def __init__(self, n):
        self.n = n
        self.bands = []

    def tool_schemas_for_band(self, band):
        self.bands.append(band)
        return [{"name": f"c{i}"} for i in range(self.n)]


def test_counts_what_an_owner_turn_sends():
    from windyfly.agent.capabilities import Band

    caps = _Caps(30)
    assert tool_count.owner_tool_count(_Tools(70), caps) == 100
    assert caps.bands == [Band.OWNER]


def test_writes_the_fact_atomically(tmp_path):
    path = tool_count.write(129, tmp_path)
    assert json.loads(path.read_text(encoding="utf-8"))["owner_tools"] == 129
    assert json.loads(path.read_text(encoding="utf-8"))["mind_max_tools"] == 128
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".owner-tools.")]  # no temp file left


def test_record_never_writes_under_pytest(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path))
    assert tool_count.record(_Tools(1), _Caps(1)) == 2
    assert not (tmp_path / "owner-tools.json").exists()


def test_the_step_is_last_and_optional():
    steps = default_capability_registration_sequence()
    assert steps[-1].name == "observability.tool_count" and steps[-1].optional is True
