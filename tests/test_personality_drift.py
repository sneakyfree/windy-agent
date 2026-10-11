"""Personality drift detection (strand C5.3 / C10.3).

Spec: compare current slider values against the values from ~30 days
ago and flag a slider that moved more than 2 points WITHOUT explicit
owner action. Each test below reproduced a real bug before the fix:

- the 24h job snapshotted BEFORE detecting, so the baseline always
  equalled the current value and drift never fired;
- the baseline was the newest soul_history row, not a 30-day-old one;
- the owner's own slider changes were reported as agent drift;
- the result went to a key nothing read (no dashboard surface).
"""

from __future__ import annotations

from windyfly.control_panel import set_slider
from windyfly.dashboard.data import get_dashboard_summary
from windyfly.memory.database import Database
from windyfly.memory.soul import get_soul, upsert_soul
from windyfly.memory.write_queue import WriteQueue
from windyfly.personality.versioning import (
    OWNER_SOURCES,
    detect_drift,
    get_personality_history,
    plan_personality_rollback,
    rollback_personality,
    run_periodic_drift_check,
    snapshot_personality,
)


def _backdate_history(db: Database, days: int) -> None:
    """Pretend every soul_history row so far was written ``days`` ago."""
    db.execute(
        "UPDATE soul_history SET created_at = datetime('now', ?)",
        (f"-{days} days",),
    )
    db.commit()


def _agent_sets(db: Database, slider: str, value: int) -> None:
    """A slider write that did NOT come from the owner."""
    upsert_soul(db, key=f"slider_{slider}", value=str(value), source="agent")


class TestDetectDrift:
    def test_agent_change_against_30_day_baseline_is_drift(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)  # owner
        snapshot_personality(db, changed_by="periodic")
        _backdate_history(db, 40)
        _agent_sets(db, "humor", 9)
        # A day-old snapshot already captured the 9 — pre-fix the
        # detector compared against THIS row and saw no change.
        snapshot_personality(db, changed_by="periodic")
        db.execute(
            "UPDATE soul_history SET created_at = datetime('now', '-1 days') "
            "WHERE new_value = '9'"
        )
        db.commit()

        drift = detect_drift(db)
        assert drift is not None
        assert drift["drifted_sliders"] == [
            {"name": "humor", "old": 5, "new": 9, "delta": 4,
             "source": "agent", "since": drift["baseline_at"]},
        ]
        assert drift["drift_source"] == "agent"
        assert drift["baseline_at"] <= db.fetchone(
            "SELECT datetime('now', '-30 days') AS c")["c"]
        db.close()

    def test_owner_change_is_not_drift(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        snapshot_personality(db, changed_by="periodic")
        _backdate_history(db, 40)
        set_slider(db, "humor", 9)  # owner moved it: control_panel
        assert detect_drift(db) is None
        db.close()

    def test_small_agent_change_is_not_drift(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _backdate_history(db, 40)
        _agent_sets(db, "humor", 7)  # moved exactly 2 — not > 2
        assert detect_drift(db) is None
        db.close()

    def test_falls_back_to_oldest_snapshot_when_none_is_30_days_old(self):
        db = Database(":memory:")
        set_slider(db, "creativity", 2)
        snapshot_personality(db)
        _backdate_history(db, 3)
        _agent_sets(db, "creativity", 8)
        snapshot_personality(db)  # newest row now says 8
        drift = detect_drift(db)
        assert drift is not None
        assert drift["drifted_sliders"][0]["old"] == 2
        assert drift["drifted_sliders"][0]["new"] == 8
        db.close()

    def test_owner_sources_cover_what_owner_surfaces_write(self):
        # set_slider / apply_preset (dashboard PUT, UDS sliders.set,
        # /slider + /preset commands) write "control_panel"; rollback
        # writes "rollback".
        assert {"control_panel", "rollback"} <= set(OWNER_SOURCES)
        db = Database(":memory:")
        set_slider(db, "humor", 3)
        assert get_soul(db, "slider_humor")["source"] in OWNER_SOURCES
        db.close()


class TestPeriodicCheck:
    def test_scheduler_detects_before_snapshotting(self):
        db = Database(":memory:")
        wq = WriteQueue()
        wq.start()
        try:
            set_slider(db, "humor", 5)
            snapshot_personality(db, changed_by="periodic")
            _backdate_history(db, 2)
            _agent_sets(db, "humor", 9)

            result = run_periodic_drift_check(db, wq)
            assert result["drift_detected"] is True
            assert result["drift_report"]["drifted_sliders"][0]["name"] == "humor"
            # The snapshot still happens — after detection.
            latest = get_personality_history(db, limit=1, sliders_only=True)[0]
            assert latest["new_value"] == "9"
            # And the next day's run still sees it (baseline is old, not
            # the snapshot it just took).
            assert run_periodic_drift_check(db, wq)["drift_detected"] is True
        finally:
            wq.stop()
            db.close()

    def test_dashboard_summary_includes_drift(self):
        db = Database(":memory:")
        assert get_dashboard_summary(db)["drift"]["detected"] is False

        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _backdate_history(db, 40)
        _agent_sets(db, "humor", 9)
        run_periodic_drift_check(db)

        drift = get_dashboard_summary(db)["drift"]
        assert drift["detected"] is True
        assert drift["drifted_sliders"][0]["name"] == "humor"
        assert drift["drifted_sliders"][0]["old"] == 5
        assert drift["drifted_sliders"][0]["new"] == 9
        assert drift["drift_source"] == "agent"
        assert drift["baseline_at"]
        db.close()

    def test_alert_clears_when_drift_resolves(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _backdate_history(db, 40)
        _agent_sets(db, "humor", 9)
        run_periodic_drift_check(db)
        assert get_dashboard_summary(db)["drift"]["detected"] is True
        set_slider(db, "humor", 9)  # owner accepts the new value
        run_periodic_drift_check(db)
        assert get_dashboard_summary(db)["drift"]["detected"] is False
        db.close()


class TestRollback:
    def test_rollback_restores_value_and_clears_drift(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _backdate_history(db, 40)
        when = db.fetchone("SELECT created_at FROM soul_history")["created_at"]
        _agent_sets(db, "humor", 9)
        run_periodic_drift_check(db)
        assert get_dashboard_summary(db)["drift"]["detected"] is True

        restored = rollback_personality(db, when)
        assert restored == 1
        row = get_soul(db, "slider_humor")
        assert row["value"] == "5"
        assert row["source"] == "rollback"
        assert get_dashboard_summary(db)["drift"]["detected"] is False
        assert detect_drift(db) is None
        db.close()

    def test_rollback_only_touches_personality_sliders(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        upsert_soul(db, key="email_address", value="a@example.test")
        snapshot_personality(db)
        upsert_soul(db, key="email_address", value="b@example.test")
        rollback_personality(db, "2099-01-01")
        assert get_soul(db, "email_address")["value"] == "b@example.test"
        db.close()

    def test_rollback_after_rollback_restores_point_in_time_value(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _agent_sets(db, "humor", 9)
        rollback_personality(db, "2099-01-01")  # -> 5 (history: 9 -> 5)
        _agent_sets(db, "humor", 1)
        rollback_personality(db, "2099-01-01")  # latest point says 5, not 9
        assert get_soul(db, "slider_humor")["value"] == "5"
        db.close()


class TestRollbackShowsExactlyWhatItRestores:
    """Hub, 2b: the owner sees the exact slider changes before a restore, and the
    restore applies exactly that list (no silent personality overwrite)."""

    def _drifted(self) -> tuple[Database, str]:
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        set_slider(db, "formality", 4)
        snapshot_personality(db)
        _backdate_history(db, 40)
        when = db.fetchone("SELECT MAX(created_at) AS t FROM soul_history")["t"]
        _agent_sets(db, "humor", 9)
        return db, when

    def test_plan_lists_only_what_changes_and_writes_nothing(self):
        db, when = self._drifted()
        versions = db.fetchone("SELECT SUM(version) AS v FROM soul")["v"]
        plan = plan_personality_rollback(db, when)
        assert [(c["slider"], c["current"], c["restore"]) for c in plan] == [("humor", "9", "5")]
        assert db.fetchone("SELECT SUM(version) AS v FROM soul")["v"] == versions
        assert get_soul(db, "slider_humor")["value"] == "9"
        db.close()

    def test_rollback_applies_exactly_the_plan(self):
        db, when = self._drifted()
        plan = plan_personality_rollback(db, when)
        assert rollback_personality(db, when) == len(plan) == 1
        assert get_soul(db, "slider_humor")["value"] == "5"
        assert get_soul(db, "slider_formality")["value"] == "4"
        assert plan_personality_rollback(db, when) == []
        db.close()

    def test_bridge_dry_run_returns_changes_and_changes_nothing(self):
        from tests.test_gap_closure import _make_bridge, _run

        bridge, db, _ = _make_bridge()
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _backdate_history(db, 40)
        when = db.fetchone("SELECT MAX(created_at) AS t FROM soul_history")["t"]
        _agent_sets(db, "humor", 9)

        dry = _run(bridge._dispatch("personality.rollback", {"snapshot_date": when, "dry_run": True}))
        assert dry == {"dry_run": True, "changes": [{"slider": "humor", "current": "9", "restore": "5"}]}
        assert get_soul(db, "slider_humor")["value"] == "9"

        done = _run(bridge._dispatch("personality.rollback", {"snapshot_date": when}))
        assert done["restored_count"] == 1
        assert done["changes"] == dry["changes"]
        assert get_soul(db, "slider_humor")["value"] == "5"
        db.close()



class TestRollbackRefusesAChangedPlan:
    """Hub (#509 note): the restore applies only the list the owner confirmed."""

    def _setup(self):
        from tests.test_gap_closure import _make_bridge, _run

        bridge, db, _ = _make_bridge()
        set_slider(db, "humor", 5)
        snapshot_personality(db)
        _backdate_history(db, 40)
        when = db.fetchone("SELECT MAX(created_at) AS t FROM soul_history")["t"]
        _agent_sets(db, "humor", 9)
        dry = _run(bridge._dispatch("personality.rollback", {"snapshot_date": when, "dry_run": True}))
        return bridge, db, when, dry, _run

    def test_confirmed_list_is_applied(self):
        bridge, db, when, dry, _run = self._setup()
        done = _run(bridge._dispatch("personality.rollback",
                                     {"snapshot_date": when, "expected": dry["changes"]}))
        assert "error" not in done and done["restored_count"] == 1
        assert get_soul(db, "slider_humor")["value"] == "5"
        db.close()

    def test_slider_moved_after_preview_restores_nothing(self):
        bridge, db, when, dry, _run = self._setup()
        _agent_sets(db, "humor", 7)  # moved after the owner looked
        done = _run(bridge._dispatch("personality.rollback",
                                     {"snapshot_date": when, "expected": dry["changes"]}))
        assert done["error"] == "changed" and done["restored_count"] == 0
        assert done["changes"] == [{"slider": "humor", "current": "7", "restore": "5"}]
        assert get_soul(db, "slider_humor")["value"] == "7"
        db.close()

class TestHistory:
    def test_history_names_the_slider_and_can_filter(self):
        db = Database(":memory:")
        set_slider(db, "humor", 5)
        upsert_soul(db, key="last_checkin_date", value="2026-10-10")
        snapshot_personality(db)
        rows = get_personality_history(db, limit=10, sliders_only=True)
        assert [r["key"] for r in rows] == ["slider_humor"]
        assert len(get_personality_history(db, limit=10)) == 2
        db.close()
