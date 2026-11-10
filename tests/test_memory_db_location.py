"""One place for the memory database, whatever folder a command runs in (Boss + Hub 11-03, the #696 lab C).

`windy home` imported a handed-over agent's memory into <folder X>/data/windyfly.db while the agent, started in
folder Y, read <Y>/data/windyfly.db: empty, so it greeted its owner as a stranger and never answered the question."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from windyfly.memory import db_location as dl
from windyfly.memory.database import Database


@pytest.fixture(autouse=True)
def _places(tmp_path, monkeypatch):
    monkeypatch.setenv("WINDY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("WINDYFLY_HOME", str(tmp_path / "install"))
    monkeypatch.delenv("WINDYFLY_DB_PATH", raising=False)
    (tmp_path / "install").mkdir()
    dl._reset_for_tests()
    yield
    dl._reset_for_tests()


def _folder(tmp_path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    return d


def _make_db(path: Path, episodes: int = 0, handover: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    from windyfly.memory.episodes import save_episode

    db = Database(str(path))
    for i in range(episodes):
        save_episode(db, "user", f"m{i}", session_id="s")
    if handover:
        from windyfly.bring_home import write_turnover

        write_turnover("I'm moving to your computer.", "ET26-TEST-AAAA", db)
    db.close()


def _state_db(tmp_path) -> Path:
    return (tmp_path / "state" / "data" / "windyfly.db").resolve()


def test_an_absolute_path_is_used_as_it_is(tmp_path):
    assert dl.resolve_db_path(str(tmp_path / "x.db")) == str(tmp_path / "x.db")


def test_a_fresh_agent_keeps_its_memory_in_the_state_folder_from_any_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(_folder(tmp_path, "a"))
    first = dl.resolve_db_path()
    monkeypatch.chdir(_folder(tmp_path, "b"))
    assert dl.resolve_db_path() == first == str(_state_db(tmp_path))


def test_an_agent_alive_today_keeps_its_database_in_its_working_folder(tmp_path, monkeypatch):
    home = _folder(tmp_path, "agent")
    _make_db(home / "data" / "windyfly.db", episodes=3)
    monkeypatch.chdir(home)
    assert dl.resolve_db_path() == str((home / "data" / "windyfly.db").resolve())


def test_the_install_folder_is_an_old_place_too(tmp_path, monkeypatch):
    _make_db(tmp_path / "install" / "data" / "windyfly.db", episodes=2)
    monkeypatch.chdir(_folder(tmp_path, "somewhere-else"))
    assert dl.resolve_db_path() == str((tmp_path / "install" / "data" / "windyfly.db").resolve())


def test_zero_case_an_old_rich_database_wins_over_an_empty_new_one(tmp_path, monkeypatch):
    """Boss 12:23Z: both exist, neither holds a handover record -> the OLD one, memory visible."""
    home = _folder(tmp_path, "zero")
    _make_db(home / "data" / "windyfly.db", episodes=19)
    _make_db(_state_db(tmp_path))
    monkeypatch.chdir(home)
    with patch.object(dl, "_ambiguous_event") as event:
        picked = dl.resolve_db_path()
    assert picked == str((home / "data" / "windyfly.db").resolve())
    db = Database(picked)
    assert db.fetchone("SELECT COUNT(*) AS c FROM episodes")["c"] == 19
    db.close()
    event.assert_called_once_with("working_folder")


@pytest.mark.parametrize("old_h, new_h, want", [(False, True, "new"), (True, False, "old"), (True, True, "old")])
def test_both_exist_the_handover_database_wins_else_the_old_one(tmp_path, monkeypatch, old_h, new_h, want):
    home = _folder(tmp_path, "agent")
    old, new = home / "data" / "windyfly.db", _state_db(tmp_path)
    _make_db(old, episodes=1, handover=old_h)
    _make_db(new, episodes=1, handover=new_h)
    monkeypatch.chdir(home)
    with patch.object(dl, "_ambiguous_event") as event:
        picked = dl.resolve_db_path()
    assert picked == str((old if want == "old" else new).resolve())
    assert event.called == (old_h == new_h)          # only the undecidable case is reported


def test_the_event_names_the_install_folder_when_that_old_place_won(tmp_path, monkeypatch):
    _make_db(tmp_path / "install" / "data" / "windyfly.db", episodes=2)
    _make_db(_state_db(tmp_path), episodes=1)
    monkeypatch.chdir(_folder(tmp_path, "elsewhere"))
    with patch.object(dl, "_ambiguous_event") as event:
        dl.resolve_db_path()
    event.assert_called_once_with("install_folder")


def test_nothing_is_merged_moved_or_deleted(tmp_path, monkeypatch):
    home = _folder(tmp_path, "agent")
    old, new = home / "data" / "windyfly.db", _state_db(tmp_path)
    _make_db(old, episodes=4)
    _make_db(new, episodes=1)
    before = (old.read_bytes(), new.read_bytes())
    monkeypatch.chdir(home)
    dl.resolve_db_path()
    assert (old.read_bytes(), new.read_bytes()) == before


def _capture_events(monkeypatch) -> list[dict]:
    from windyfly.observability import admin_telemetry as at

    sent: list[dict] = []
    monkeypatch.setattr(at, "enabled", lambda: True)
    monkeypatch.setattr(at, "_own_passport", lambda: "ET26-TEST-AAAA")
    monkeypatch.setattr(at, "send_event", lambda q, ev: sent.append(ev))
    return sent


def test_the_ambiguity_event_names_which_place_won_never_a_path(monkeypatch):
    sent = _capture_events(monkeypatch)
    monkeypatch.setattr(dl, "EMIT_AMBIGUOUS_EVENT", True)
    for picked in ("working_folder", "install_folder", "state_folder"):   # Admin #869's exact set
        dl._ambiguous_event(picked)
    assert [e["event_type"] for e in sent] == ["agent.db_path_ambiguous"] * 3
    assert [e["metadata"] for e in sent] == [{"picked": p} for p in ("working_folder", "install_folder",
                                                                     "state_folder")]   # closed keys: picked only


def test_the_ambiguity_event_is_on_since_admin_said_live(monkeypatch):
    # Admin "declared and live" 2026-11-04 21:16Z. Still inert without the ingest (admin_telemetry.enabled()).
    sent = _capture_events(monkeypatch)
    assert dl.EMIT_AMBIGUOUS_EVENT is True
    dl._ambiguous_event("working_folder")
    assert [e["metadata"] for e in sent] == [{"picked": "working_folder"}]
    from windyfly.observability import admin_telemetry as at

    monkeypatch.setattr(at, "enabled", lambda: False)
    dl._ambiguous_event("state_folder")
    assert len(sent) == 1  # no ingest configured = nothing sent


def test_status_from_another_folder_creates_no_database(tmp_path, monkeypatch):
    from windyfly.cli_status import print_status

    monkeypatch.chdir(_folder(tmp_path, "elsewhere"))
    print_status({})
    assert not _state_db(tmp_path).exists()
    assert not (tmp_path / "elsewhere" / "data" / "windyfly.db").exists()
    assert dl.existing_db_path() is None


# ── the 11-03 lab, end to end: `windy home` in folder X, the agent started in folder Y ───────────────────────────────

def _brought_home(tmp_path, monkeypatch) -> None:
    from windyfly import bring_home, hub_hatch
    from windyfly.memory.episodes import save_episode

    monkeypatch.chdir(_folder(tmp_path, "X"))
    db = bring_home._open_db()
    save_episode(db, "user", "Remember the code word: kite", session_id="handover:ho_1")
    bring_home.write_turnover("I'm moving to your computer.", "ET26-TEST-AAAA", db)
    assert Path(bring_home._abs_db_path(db)) == _state_db(tmp_path)   # what `windy home` prints
    db.close()
    hub_hatch._write_private_json(hub_hatch.cloud_agent_path(),
                                  {"passport_number": "ET26-TEST-AAAA", "where": "home"})


@pytest.mark.virgin_db_welcome
@patch("windyfly.agent.loop.is_online", return_value=True)
@patch("windyfly.agent.loop.call_llm")
def test_home_in_one_folder_and_started_in_another_uses_the_handed_over_memory(mock_llm, _online, tmp_path,
                                                                                monkeypatch):
    from windyfly.agent.loop import agent_respond
    from windyfly.memory.write_queue import WriteQueue

    _brought_home(tmp_path, monkeypatch)
    monkeypatch.chdir(_folder(tmp_path, "Y"))
    db = Database(dl.resolve_db_path())
    assert db.fetchone("SELECT COUNT(*) AS c FROM episodes WHERE session_id = 'handover:ho_1'")["c"] == 1
    mock_llm.return_value = {"content": "It was kite.", "model": "m", "input_tokens": 1, "output_tokens": 1,
                             "tool_calls": None}
    wq = WriteQueue()
    wq.start()
    try:
        reply = agent_respond({"agent": {"default_model": "m"}, "memory": {}, "personality": {}}, db, wq,
                              "Wake test: what was the code word?", "matrix:!dm:v0")
    finally:
        wq.stop()
    assert reply == "It was kite."          # answered, and no "I just hatched" tour
    assert mock_llm.call_count == 1
    db.close()


@pytest.mark.virgin_db_welcome
def test_a_brought_home_agent_is_never_welcomed_even_on_an_empty_database(tmp_path, monkeypatch):
    from windyfly import hub_hatch
    from windyfly.agent.welcome import is_first_contact

    db = Database(str(tmp_path / "empty.db"))
    assert is_first_contact(db)
    hub_hatch._write_private_json(hub_hatch.cloud_agent_path(), {"passport_number": "ET26-TEST-AAAA", "where": "home"})
    assert not is_first_contact(db)
    db.close()


def test_install_memory_reports_the_absolute_memory_file(tmp_path, monkeypatch):
    from windyfly import bring_home

    monkeypatch.chdir(_folder(tmp_path, "X"))
    out = bring_home.install_memory({"turnover_letter": "hello"}, "ET26-TEST-AAAA", "ho_1", "")
    assert out["db_path"] == str(_state_db(tmp_path)) and Path(out["db_path"]).is_absolute()


def test_no_reader_resolves_the_memory_file_on_its_own():
    """Grep-level guard: a call site that falls back to a bare relative default re-creates the 11-03 bug."""
    import re

    src = Path(__file__).resolve().parents[1] / "src" / "windyfly"
    bad = re.compile(r"""(get\(\s*["']db_path["']\s*,\s*["']data/windyfly\.db["']|"""
                     r"""environ\.get\(\s*["']WINDYFLY_DB_PATH["']\s*,\s*["']data/windyfly\.db["']|"""
                     r"""PROJECT_ROOT\s*/\s*["']data["']\s*/\s*["']windyfly\.db["'])""")
    hits = [f"{p.relative_to(src)}:{i}" for p in src.rglob("*.py")
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if bad.search(line)]
    assert hits == [], hits


def test_a_deleted_working_folder_never_crashes_the_lookup(tmp_path, monkeypatch):
    gone = _folder(tmp_path, "gone")
    monkeypatch.chdir(gone)
    gone.rmdir()
    assert dl.resolve_db_path() == str(_state_db(tmp_path))


def test_the_event_rides_the_same_once_key_as_the_warning(tmp_path, monkeypatch, caplog):
    """Hub on #715: resolving the same ambiguous pair again (every open of the DB) sends no second event."""
    import logging

    home = _folder(tmp_path, "zero")
    _make_db(home / "data" / "windyfly.db", episodes=19)
    _make_db(_state_db(tmp_path))
    monkeypatch.chdir(home)
    with patch.object(dl, "_ambiguous_event") as event, caplog.at_level(logging.WARNING, logger=dl.__name__):
        for _ in range(5):
            dl.resolve_db_path()
    event.assert_called_once_with("working_folder")
    assert sum("AND in the state folder" in r.getMessage() for r in caplog.records) == 1
