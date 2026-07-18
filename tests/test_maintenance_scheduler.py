"""In-process maintenance scheduler (Tier 2, 2026-07-18)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from windyfly.agent import maintenance as m


UTC = timezone.utc


class TestDueChecks:
    def test_daily_due_first_run_and_once_per_day(self):
        due = m.daily_due(after_hour=0)
        now = datetime(2026, 7, 18, 3, 0, tzinfo=UTC)
        assert due(None, now) is True                       # never run
        assert due(now, now) is False                       # ran today
        assert due(now - timedelta(days=1), now) is True    # ran yesterday

    def test_daily_after_hour_gate(self):
        due = m.daily_due(after_hour=6)
        assert due(None, datetime(2026, 7, 18, 5, 0, tzinfo=UTC)) is False
        assert due(None, datetime(2026, 7, 18, 6, 0, tzinfo=UTC)) is True

    def test_weekly_due_only_on_weekday(self):
        due = m.weekly_due(weekday=6, after_hour=8)  # Sunday 08:00
        sunday = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)      # a Sunday
        saturday = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
        assert due(None, sunday) is True
        assert due(None, saturday) is False
        assert due(sunday, sunday) is False                   # already today

    def test_interval_due(self):
        due = m.interval_due(3600)
        now = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
        assert due(None, now) is True
        assert due(now - timedelta(minutes=30), now) is False
        assert due(now - timedelta(minutes=61), now) is True


class TestRunDueJobsPersistence:
    def test_runs_due_records_and_dedups(self, tmp_path):
        calls = []
        job = m.MaintenanceJob(
            name="j", run=lambda: calls.append(1),
            due=m.daily_due(),
        )
        now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        ran1 = m.run_due_jobs([job], now=now, state_dir=tmp_path)
        assert ran1 == ["j"] and len(calls) == 1
        # Second run same day → not due (last-run persisted)
        ran2 = m.run_due_jobs([job], now=now, state_dir=tmp_path)
        assert ran2 == [] and len(calls) == 1

    def test_cross_process_dedup_via_shared_file(self, tmp_path):
        # Two "processes" (same state_dir) — only one runs the job.
        calls = []
        mk = lambda: m.MaintenanceJob("j", lambda: calls.append(1), m.daily_due())
        now = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        m.run_due_jobs([mk()], now=now, state_dir=tmp_path)   # process A
        m.run_due_jobs([mk()], now=now, state_dir=tmp_path)   # process B
        assert len(calls) == 1

    def test_broken_job_does_not_block_others(self, tmp_path):
        ok = []
        def boom(): raise RuntimeError("bad job")
        jobs = [
            m.MaintenanceJob("bad", boom, m.daily_due()),
            m.MaintenanceJob("good", lambda: ok.append(1), m.daily_due()),
        ]
        ran = m.run_due_jobs(jobs, now=datetime(2026, 7, 18, tzinfo=UTC),
                             state_dir=tmp_path)
        assert "good" in ran and ok == [1]
        # bad job NOT recorded as run (so it retries next tick)
        assert m._last_run("bad", tmp_path) is None


class TestDefaultJobs:
    def test_journal_job_registered(self):
        jobs = m.default_jobs({"memory": {"db_path": ":memory:"}})
        assert any(j.name == "journal.daily" for j in jobs)


class TestBatteryJobFold:
    def test_battery_registered_when_script_present(self, monkeypatch, tmp_path):
        # Simulate a checkout with the script present.
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "continuity_battery.py").write_text("# stub")
        monkeypatch.setattr(m, "get_project_root", lambda: tmp_path, raising=False)
        import windyfly.platform as plat
        monkeypatch.setattr(plat, "get_project_root", lambda: tmp_path)
        jobs = m.default_jobs({"memory": {"db_path": ":memory:"}})
        names = [j.name for j in jobs]
        assert "continuity.battery.weekly" in names

    def test_battery_absent_when_no_script(self, monkeypatch, tmp_path):
        import windyfly.platform as plat
        monkeypatch.setattr(plat, "get_project_root", lambda: tmp_path)  # no scripts/
        jobs = m.default_jobs({"memory": {"db_path": ":memory:"}})
        assert "continuity.battery.weekly" not in [j.name for j in jobs]

    def test_battery_weekly_cadence_sunday(self):
        due = m.weekly_due(weekday=6, after_hour=8)
        from datetime import datetime, timezone
        sunday9 = datetime(2026, 7, 19, 9, 0, tzinfo=timezone.utc)
        monday9 = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
        assert due(None, sunday9) is True
        assert due(None, monday9) is False
