"""SQLite database layer for migration state persistence.

Stores migration progress so the bot can resume from the last
successfully migrated message after any interruption.

Schema:
    migrations:
        - source_message_id  (PRIMARY KEY)
        - destination_message_id
        - media_type
        - album_group_id
        - migrated_at
        - status  (success / error / skipped)
        - checksum  (md5 of caption + media_id for dedup)

    state:
        - key   (PRIMARY KEY)
        - value
"""

import aiosqlite
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger("migrator.database")

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS migrations (
    source_message_id   INTEGER PRIMARY KEY,
    destination_message_id INTEGER,
    media_type          TEXT,
    album_group_id      INTEGER,
    migrated_at         TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'success',
    checksum            TEXT
);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_migrations_status ON migrations(status);
CREATE INDEX IF NOT EXISTS idx_migrations_album ON migrations(album_group_id);
CREATE INDEX IF NOT EXISTS idx_migrations_checksum ON migrations(checksum);
"""


class MigrationDatabase:
    """Async SQLite wrapper for migration state."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Open database connection and ensure schema exists."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(CREATE_TABLES_SQL)
        await self._db.commit()
        logger.info("Database connected: %s", self.db_path)

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database connection closed")

    async def is_migrated(self, source_message_id: int) -> bool:
        """Check if a message has already been migrated successfully."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM migrations WHERE source_message_id = ? AND status = 'success'",
            (source_message_id,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def is_checksum_exists(self, checksum: str) -> bool:
        """Check if a checksum already exists (duplicate detection)."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT 1 FROM migrations WHERE checksum = ? AND status = 'success'",
            (checksum,),
        )
        row = await cursor.fetchone()
        return row is not None

    async def record_migration(
        self,
        source_message_id: int,
        destination_message_id: int | None,
        media_type: str = "",
        album_group_id: int | None = None,
        status: str = "success",
        checksum: str | None = None,
    ) -> None:
        """Record a completed migration."""
        assert self._db is not None
        now = datetime.now(timezone.utc).isoformat()
        await self._db.execute(
            """
            INSERT OR REPLACE INTO migrations
                (source_message_id, destination_message_id, media_type,
                 album_group_id, migrated_at, status, checksum)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_message_id,
                destination_message_id,
                media_type,
                album_group_id,
                now,
                status,
                checksum,
            ),
        )
        await self._db.commit()

    async def record_skip(
        self, source_message_id: int, reason: str = "text_only"
    ) -> None:
        """Record a skipped message."""
        await self.record_migration(
            source_message_id=source_message_id,
            destination_message_id=None,
            status=f"skipped:{reason}",
        )

    async def get_last_migrated_id(self) -> int | None:
        """Get the highest source_message_id that was processed.

        Returns None if no messages have been processed yet.
        """
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT MAX(source_message_id) as max_id FROM migrations WHERE status != 'dry_run'"
        )
        row = await cursor.fetchone()
        if row and row["max_id"] is not None:
            return int(row["max_id"])
        return None

    async def get_migration_count(self, status: str = "success") -> int:
        """Get count of migrations with a given status."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM migrations WHERE status = ?",
            (status,),
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def get_total_processed(self) -> int:
        """Get total number of processed messages (all statuses)."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT COUNT(*) as cnt FROM migrations"
        )
        row = await cursor.fetchone()
        return int(row["cnt"]) if row else 0

    async def set_state(self, key: str, value: str) -> None:
        """Store a key-value state entry."""
        assert self._db is not None
        await self._db.execute(
            "INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)",
            (key, value),
        )
        await self._db.commit()

    async def get_state(self, key: str) -> str | None:
        """Retrieve a state value by key."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT value FROM state WHERE key = ?", (key,)
        )
        row = await cursor.fetchone()
        return str(row["value"]) if row else None

    async def get_failed_ids(self) -> list[int]:
        """Get source_message_ids of failed migrations for retry."""
        assert self._db is not None
        cursor = await self._db.execute(
            "SELECT source_message_id FROM migrations WHERE status = 'error' ORDER BY source_message_id"
        )
        rows = await cursor.fetchall()
        return [int(row["source_message_id"]) for row in rows]
