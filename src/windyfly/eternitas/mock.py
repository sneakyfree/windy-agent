"""Mock Eternitas client — full lifecycle backed by SQLite.

Used when ETERNITAS_API_URL is unset or set to ``mock://local``.
Generates local passport IDs (ET-LXXXX) and stores everything in the
same windyfly.db database.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from windyfly.eternitas.models import (
    BotIdentity,
    EternitasPassport,
    RegistrationRequest,
    RevocationResult,
)
from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS eternitas_registry (
    id TEXT PRIMARY KEY,
    passport_id TEXT UNIQUE NOT NULL,
    agent_name TEXT NOT NULL,
    owner_id TEXT DEFAULT '',
    owner_name TEXT DEFAULT '',
    status TEXT DEFAULT 'active',
    provisioned_services JSON DEFAULT '{}',
    credentials JSON DEFAULT '{}',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    revoked_at DATETIME
);
"""


class MockEternitasClient:
    """Local Eternitas mock that stores registrations in SQLite.

    Shares the same interface as EternitasClient so the hatch
    orchestrator doesn't care which one it's using.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_table()
        self._next_seq = self._get_next_seq()

    def _ensure_table(self) -> None:
        self.db.conn.executescript(_CREATE_TABLE_SQL)

    def _get_next_seq(self) -> int:
        row = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM eternitas_registry"
        )
        return (row["cnt"] if row else 0) + 1

    def _make_passport_id(self) -> str:
        seq = self._next_seq
        self._next_seq += 1
        return f"ET-L{seq:05d}"

    async def register(self, request: RegistrationRequest) -> EternitasPassport:
        """Register a new bot locally."""
        # Check for existing registration
        existing = self.db.fetchone(
            "SELECT * FROM eternitas_registry WHERE agent_name = ? AND status = 'active'",
            (request.agent_name,),
        )
        if existing:
            return self._row_to_passport(existing)

        passport_id = self._make_passport_id()
        row_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        self.db.execute(
            """INSERT INTO eternitas_registry
               (id, passport_id, agent_name, owner_id, owner_name, status, created_at)
               VALUES (?, ?, ?, ?, ?, 'active', ?)""",
            (row_id, passport_id, request.agent_name, request.owner_id,
             request.owner_name, now.isoformat()),
        )
        self.db.commit()
        logger.info("Mock Eternitas: registered %s as %s", request.agent_name, passport_id)

        return EternitasPassport(
            passport_id=passport_id,
            agent_name=request.agent_name,
            owner_id=request.owner_id,
            owner_name=request.owner_name,
            status="active",
            issued_at=now,
        )

    async def verify(self, passport_id: str) -> EternitasPassport | None:
        """Verify a passport exists and is active."""
        row = self.db.fetchone(
            "SELECT * FROM eternitas_registry WHERE passport_id = ?",
            (passport_id,),
        )
        if not row:
            return None
        return self._row_to_passport(row)

    async def lookup(self, agent_name: str) -> BotIdentity | None:
        """Look up a bot by name."""
        row = self.db.fetchone(
            "SELECT * FROM eternitas_registry WHERE agent_name = ? AND status = 'active'",
            (agent_name,),
        )
        if not row:
            return None
        services = json.loads(row.get("provisioned_services", "{}") or "{}")
        return BotIdentity(
            passport_id=row["passport_id"],
            agent_name=row["agent_name"],
            owner_id=row.get("owner_id", ""),
            status=row["status"],
            services=list(services.keys()),
        )

    async def revoke(self, passport_id: str, reason: str = "") -> RevocationResult:
        """Revoke a passport and mark services for teardown."""
        row = self.db.fetchone(
            "SELECT * FROM eternitas_registry WHERE passport_id = ? AND status = 'active'",
            (passport_id,),
        )
        if not row:
            return RevocationResult(
                passport_id=passport_id,
                revoked=False,
                error="Passport not found or already revoked",
            )

        services = json.loads(row.get("provisioned_services", "{}") or "{}")
        self.db.execute(
            "UPDATE eternitas_registry SET status = 'revoked', revoked_at = ? WHERE passport_id = ?",
            (datetime.now(timezone.utc).isoformat(), passport_id),
        )
        self.db.commit()
        logger.info("Mock Eternitas: revoked %s", passport_id)

        return RevocationResult(
            passport_id=passport_id,
            revoked=True,
            services_torn_down=list(services.keys()),
        )

    async def update_services(
        self, passport_id: str, services: dict[str, str]
    ) -> EternitasPassport:
        """Update provisioned services for a passport."""
        row = self.db.fetchone(
            "SELECT * FROM eternitas_registry WHERE passport_id = ?",
            (passport_id,),
        )
        if not row:
            raise ValueError(f"Passport {passport_id} not found")

        existing = json.loads(row.get("provisioned_services", "{}") or "{}")
        existing.update(services)
        self.db.execute(
            "UPDATE eternitas_registry SET provisioned_services = ? WHERE passport_id = ?",
            (json.dumps(existing), passport_id),
        )
        self.db.commit()
        return self._row_to_passport(
            self.db.fetchone(
                "SELECT * FROM eternitas_registry WHERE passport_id = ?",
                (passport_id,),
            )
        )

    def _row_to_passport(self, row: dict) -> EternitasPassport:
        services = json.loads(row.get("provisioned_services", "{}") or "{}")
        credentials = json.loads(row.get("credentials", "{}") or "{}")
        return EternitasPassport(
            passport_id=row["passport_id"],
            agent_name=row["agent_name"],
            owner_id=row.get("owner_id", ""),
            owner_name=row.get("owner_name", ""),
            status=row["status"],
            issued_at=row.get("created_at", datetime.now(timezone.utc)),
            provisioned_services=services,
            credentials=credentials,
        )
