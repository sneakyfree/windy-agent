"""Mock Windy Mail server — stores emails in SQLite for local development.

Used when WINDYMAIL_API_URL is unset or the real mail server isn't available.
Provides the same API shape as the real Windy Mail service.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from windyfly.memory.database import Database

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS mock_emails (
    id TEXT PRIMARY KEY,
    from_addr TEXT NOT NULL,
    to_addr TEXT NOT NULL,
    subject TEXT DEFAULT '',
    body TEXT DEFAULT '',
    status TEXT DEFAULT 'sent',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mock_mail_accounts (
    id TEXT PRIMARY KEY,
    email TEXT UNIQUE NOT NULL,
    agent_name TEXT NOT NULL,
    passport_id TEXT DEFAULT '',
    password TEXT DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""


@dataclass
class MockMailAccount:
    email: str
    agent_name: str
    passport_id: str
    password: str


class MockMailServer:
    """Local mock mail server backed by SQLite.

    Simulates Windy Mail provisioning, sending, and inbox queries.
    """

    def __init__(self, db: Database) -> None:
        self.db = db
        self._ensure_tables()

    def _ensure_tables(self) -> None:
        self.db.conn.executescript(_CREATE_TABLE_SQL)

    async def provision_inbox(
        self, agent_name: str, passport_id: str = "", owner_id: str = ""
    ) -> dict:
        """Provision a mock email inbox for an agent.

        Returns a dict matching the real Windy Mail API response.
        """
        email = f"{agent_name.lower().replace(' ', '-')}@windymail.ai"
        password = uuid.uuid4().hex[:16]

        # Check if already exists
        existing = self.db.fetchone(
            "SELECT * FROM mock_mail_accounts WHERE email = ?", (email,)
        )
        if existing:
            pw = existing.get("password", password)
            return {
                "email": existing["email"],
                "jmap_token": f"mock-jmap-{existing['id']}",
                "smtp_password": pw,
                "imap_password": pw,
                "jmap_url": "mock://local/.well-known/jmap",
            }

        row_id = str(uuid.uuid4())
        self.db.execute(
            """INSERT INTO mock_mail_accounts (id, email, agent_name, passport_id, password)
               VALUES (?, ?, ?, ?, ?)""",
            (row_id, email, agent_name, passport_id, password),
        )
        self.db.commit()
        logger.info("Mock mail: provisioned %s", email)

        return {
            "email": email,
            "jmap_token": f"mock-jmap-{row_id}",
            "smtp_password": password,
            "imap_password": password,
            "jmap_url": "mock://local/.well-known/jmap",
        }

    async def send_email(
        self, from_addr: str, to_addr: str, subject: str, body: str
    ) -> dict:
        """Send a mock email (stored in SQLite)."""
        msg_id = str(uuid.uuid4())
        self.db.execute(
            """INSERT INTO mock_emails (id, from_addr, to_addr, subject, body)
               VALUES (?, ?, ?, ?, ?)""",
            (msg_id, from_addr, to_addr, subject, body),
        )
        self.db.commit()
        logger.info("Mock mail: sent from %s to %s", from_addr, to_addr)
        return {"message_id": msg_id, "status": "sent"}

    async def get_inbox(self, email: str, limit: int = 20) -> list[dict]:
        """Get inbox messages for an email address."""
        rows = self.db.fetchall(
            "SELECT * FROM mock_emails WHERE to_addr = ? ORDER BY created_at DESC LIMIT ?",
            (email, limit),
        )
        return rows

    async def get_sent(self, email: str, limit: int = 20) -> list[dict]:
        """Get sent messages from an email address."""
        rows = self.db.fetchall(
            "SELECT * FROM mock_emails WHERE from_addr = ? ORDER BY created_at DESC LIMIT ?",
            (email, limit),
        )
        return rows
