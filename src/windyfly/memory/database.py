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

        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY,
            applied_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            description TEXT
        );
        INSERT OR IGNORE INTO schema_version (version, description)
            VALUES (1, 'Phase 0: 6 core tables + FTS');
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

        # Run PRAGMA settings
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")
        self.conn.execute("PRAGMA foreign_keys=ON;")
        self.conn.execute("PRAGMA busy_timeout=5000;")

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
        """Apply pending migrations in order."""
        current = self._get_current_version()

        for version in sorted(_MIGRATIONS.keys()):
            if version <= current:
                continue
            _desc, sql = _MIGRATIONS[version]
            self.conn.executescript(sql)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a single SQL statement."""
        return self.conn.execute(sql, params)

    def executemany(self, sql: str, params_list: list) -> sqlite3.Cursor:
        """Execute a SQL statement for each set of params."""
        return self.conn.executemany(sql, params_list)

    def commit(self) -> None:
        """Commit the current transaction."""
        self.conn.commit()

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
