

# === Shutdown turnovers (2026-07-18) ===


class TestShutdownTurnovers:
    def _db_with_sessions(self):
        from windyfly.memory.database import Database
        from windyfly.memory.episodes import save_episode
        db = Database(":memory:")
        for sid in ("telegram:111:v1", "telegram:222:v1", "matrix:!room:v1"):
            save_episode(db, "user", f"hello from {sid}", session_id=sid)
            save_episode(db, "assistant", "hi!", session_id=sid)
        return db

    def test_writes_letters_for_platform_sessions_only(self):
        from windyfly.agent.turnover import write_shutdown_turnovers
        db = self._db_with_sessions()
        n = write_shutdown_turnovers(db, "telegram")
        assert n == 2
        rows = db.fetchall(
            "SELECT name FROM nodes WHERE type='turnover_letter' ORDER BY name"
        )
        names = [r["name"] for r in rows]
        assert names == ["turnover:telegram:111", "turnover:telegram:222"]
        db.close()

    def test_never_raises_on_broken_db(self):
        from windyfly.agent.turnover import write_shutdown_turnovers

        class Broken:
            def fetchall(self, *a, **k):
                raise RuntimeError("db is toast")

        assert write_shutdown_turnovers(Broken(), "telegram") == 0

    def test_bounded_by_max_sessions(self):
        from windyfly.memory.database import Database
        from windyfly.memory.episodes import save_episode
        from windyfly.agent.turnover import write_shutdown_turnovers
        db = Database(":memory:")
        for i in range(9):
            save_episode(db, "user", "hi", session_id=f"telegram:{i}:v1")
        assert write_shutdown_turnovers(db, "telegram", max_sessions=3) == 3
        db.close()
