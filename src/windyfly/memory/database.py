"""SQLite database connection, migrations, and query helpers.

Single source of truth — one .db file, WAL mode, zero ops.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

_MIGRATIONS: dict[int, tuple[str, str]] = {
    1: (
        "Phase 0: 6 core tables + FTS",
        """
        CREATE TABLE IF NOT EXISTS nodes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            scope_id TEXT DEFAULT 'personal',
            type TEXT NOT NULL,
            name TEXT NOT NULL,
            metadata JSON,
            epistemic_status TEXT DEFAULT 'inferred',
            confidence REAL DEFAULT 1.0,
            source TEXT DEFAULT 'inferred',
            verification_method TEXT,
            last_verified_at DATETIME,
            valid_from TEXT,
            valid_until TEXT,
            decay_score REAL DEFAULT 1.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS episodes (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            session_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            summary TEXT,
            token_count INTEGER,
            cost_usd REAL,
            emotional_context TEXT,
            embedding BLOB,
            embedding_model TEXT,
            embedding_version INTEGER DEFAULT 1,
            last_accessed_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS soul (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            source TEXT DEFAULT 'default',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS skills (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            version INTEGER DEFAULT 1,
            code TEXT NOT NULL,
            language TEXT NOT NULL,
            description TEXT,
            permissions_required JSON,
            risk_level TEXT DEFAULT 'low',
            eval_score REAL,
            eval_results JSON,
            promoted BOOLEAN DEFAULT FALSE,
            usage_count INTEGER DEFAULT 0,
            success_count INTEGER DEFAULT 0,
            failure_count INTEGER DEFAULT 0,
            parent_skill_id TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used DATETIME
        );

        CREATE TABLE IF NOT EXISTS failures (
            id TEXT PRIMARY KEY,
            fault_type TEXT NOT NULL,
            description TEXT NOT NULL,
            root_cause TEXT,
            correction_action TEXT,
            correction_skill_id TEXT,
            improvement_verified BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        );

        CREATE TABLE IF NOT EXISTS cost_ledger (
            id TEXT PRIMARY KEY,
            model TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            cost_usd REAL,
            task_type TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts
            USING fts5(content, summary, content='episodes', content_rowid='rowid');

        CREATE TRIGGER IF NOT EXISTS episodes_fts_insert AFTER INSERT ON episodes BEGIN
            INSERT INTO episodes_fts(rowid, content, summary)
            VALUES (NEW.rowid, NEW.content, NEW.summary);
        END;

        CREATE TRIGGER IF NOT EXISTS episodes_fts_delete AFTER DELETE ON episodes BEGIN
            INSERT INTO episodes_fts(episodes_fts, rowid, content, summary)
            VALUES ('delete', OLD.rowid, OLD.content, OLD.summary);
        END;

        CREATE TRIGGER IF NOT EXISTS episodes_fts_update AFTER UPDATE ON episodes BEGIN
            INSERT INTO episodes_fts(episodes_fts, rowid, content, summary)
            VALUES ('delete', OLD.rowid, OLD.content, OLD.summary);
            INSERT INTO episodes_fts(rowid, content, summary)
            VALUES (NEW.rowid, NEW.content, NEW.summary);
        END;

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        );
        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (1, 'Phase 0: 6 core tables + FTS');
        """,
    ),
    2: (
        "Phase 3: intents, edges, conflicts, soul_history",
        """
        CREATE TABLE IF NOT EXISTS intents (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL DEFAULT 'default',
            scope_id TEXT DEFAULT 'personal',
            description TEXT NOT NULL,
            status TEXT DEFAULT 'active',
            priority INTEGER DEFAULT 5,
            origin TEXT DEFAULT 'user_said',
            autonomy_policy TEXT DEFAULT 'inform',
            decay_score REAL DEFAULT 1.0,
            linked_nodes JSON,
            last_touched DATETIME DEFAULT CURRENT_TIMESTAMP,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS edges (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            strength REAL DEFAULT 1.0,
            confidence REAL DEFAULT 1.0,
            timestamp_weight REAL DEFAULT 1.0,
            source_weight REAL DEFAULT 1.0,
            decay_score REAL DEFAULT 1.0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS conflicts (
            id TEXT PRIMARY KEY,
            node_id TEXT,
            old_value TEXT,
            new_value TEXT,
            resolution_status TEXT DEFAULT 'unresolved',
            user_resolved BOOLEAN DEFAULT FALSE,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            resolved_at DATETIME
        );

        CREATE TABLE IF NOT EXISTS soul_history (
            id TEXT PRIMARY KEY,
            soul_id TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            changed_by TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (2, 'Phase 3: intents, edges, conflicts, soul_history');
        """,
    ),
    3: (
        "Phase 5: events table for observability",
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            data JSON,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (3, 'Phase 5: events table for observability');
        """,
    ),
    4: (
        "Wave 4: trust_cache shape matches live Eternitas Trust API",
        """
        DROP TABLE IF EXISTS trust_cache;
        CREATE TABLE IF NOT EXISTS trust_cache (
            passport TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            band TEXT NOT NULL,
            clearance_level TEXT NOT NULL,
            tier_multiplier REAL NOT NULL,
            integrity_score INTEGER NOT NULL,
            dimensions JSON NOT NULL,
            allowed_actions JSON NOT NULL,
            denied_actions JSON NOT NULL,
            evaluated_at DATETIME NOT NULL,
            cache_ttl_seconds INTEGER NOT NULL,
            cached_at DATETIME NOT NULL
        );

        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (4, 'Wave 4: trust_cache matches live Eternitas Trust API');
        """,
    ),
    5: (
        "Wave 2 #2: agent_actions audit ledger",
        """
        CREATE TABLE IF NOT EXISTS agent_actions (
            id TEXT PRIMARY KEY,
            capability_id TEXT NOT NULL,
            tier INTEGER NOT NULL,
            band TEXT NOT NULL,
            sandbox_tier TEXT NOT NULL,
            args_json TEXT,
            success INTEGER NOT NULL DEFAULT 0,
            error_class TEXT,
            error_message TEXT,
            duration_ms INTEGER,
            cost_usd REAL DEFAULT 0,
            session_id TEXT,
            user_id TEXT,
            intent_id TEXT,
            parent_action_id TEXT,
            outcome_score REAL,
            started_at DATETIME NOT NULL,
            ended_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_agent_actions_capability
            ON agent_actions(capability_id);
        CREATE INDEX IF NOT EXISTS idx_agent_actions_session
            ON agent_actions(session_id);
        CREATE INDEX IF NOT EXISTS idx_agent_actions_started
            ON agent_actions(started_at);
        CREATE INDEX IF NOT EXISTS idx_agent_actions_success
            ON agent_actions(success, capability_id);

        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (5, 'Wave 2 #2: agent_actions audit ledger');
        """,
    ),
    6: (
        "Wave 6 #1: collaborators — long-running named sub-agents",
        """
        CREATE TABLE IF NOT EXISTS collaborators (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            parent_user_id TEXT NOT NULL DEFAULT 'default',
            persona_prompt TEXT NOT NULL,
            band TEXT NOT NULL DEFAULT 'USER',
            memory_share_policy TEXT NOT NULL DEFAULT '{}',
            model TEXT,
            daily_budget_usd REAL DEFAULT 1.0,
            max_context_tokens INTEGER DEFAULT 8000,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_used_at DATETIME,
            use_count INTEGER DEFAULT 0,
            archived_at DATETIME
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_collaborators_name_user
            ON collaborators(name, parent_user_id)
            WHERE archived_at IS NULL;
        CREATE INDEX IF NOT EXISTS idx_collaborators_user
            ON collaborators(parent_user_id);

        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (6, 'Wave 6 #1: collaborators table');
        """,
    ),
}


class Database:
    """SQLite database wrapper with migrations and dict-like row access."""

    def __init__(self, db_path: str) -> None:
        # Ensure data directory exists
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = db_path
        self.conn = sqlite3.connect(
            db_path,
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
        )
        self.conn.row_factory = sqlite3.Row

        # busy_timeout must come FIRST so every subsequent PRAGMA /
        # schema op waits for contested locks rather than immediately
        # raising "database is locked". Without this, subsequent
        # concurrent Database() opens would fail synchronously (the
        # P1-O4 symptom).
        self.conn.execute("PRAGMA busy_timeout=5000;")
        # PRAGMA journal_mode=WAL needs an exclusive lock to flip
        # modes. If another connection is mid-write on the same file,
        # this raises even with busy_timeout set. The DB only needs
        # to be in WAL mode — that's a per-file property, not
        # per-connection — so a best-effort set is sufficient: the
        # first connection wins; any later connection is already
        # seeing WAL.
        try:
            self.conn.execute("PRAGMA journal_mode=WAL;")
        except sqlite3.OperationalError:
            pass
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")

        self._run_migrations()

    def _get_current_version(self) -> int:
        """Get the current schema version, 0 if table doesn't exist."""
        try:
            cursor = self.conn.execute(
                "SELECT MAX(version) FROM schema_version"
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] is not None else 0
        except sqlite3.OperationalError:
            return 0

    def _run_migrations(self) -> None:
        """Apply pending migrations in order.

        Concurrent Database() opens on the same file can race here —
        two threads may both see "current version 3" and both try to
        run migration 4, which fails on the second attempt because the
        first has already created/dropped the tables.

        We serialize migrations with a BEGIN EXCLUSIVE transaction so
        only one writer runs the migration block at a time, and we
        re-check the version inside the transaction so the second
        thread becomes a no-op rather than replaying the SQL.
        """
        if self._get_current_version() >= max(_MIGRATIONS.keys(), default=0):
            return  # Common path — already migrated.

        try:
            self.conn.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError:
            # Another migration holds the lock; wait and re-check.
            self.conn.execute("BEGIN IMMEDIATE")

        try:
            current = self._get_current_version()
            for version in sorted(_MIGRATIONS.keys()):
                if version <= current:
                    continue
                _desc, sql = _MIGRATIONS[version]
                self.conn.executescript(sql)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single SQL statement."""
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """Execute a SQL statement for each set of params."""
        return self.conn.executemany(sql, params_list)

    def commit(self) -> None:
        """Commit the current transaction.

        Silently succeeds if no transaction is active (e.g. after an
        exception rolled back the implicit transaction).
        """
        try:
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # No transaction to commit — safe to ignore

    def fetchone(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        """Execute SQL and return the first row as a dict, or None."""
        cursor = self.conn.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        """Execute SQL and return all rows as a list of dicts."""
        cursor = self.conn.execute(sql, params)
        return [dict(row) for row in cursor.fetchall()]

    def close(self) -> None:
        """Close the database connection."""
        self.conn.close()
