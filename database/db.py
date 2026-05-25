"""
SQLite Database Layer
======================
Handles all persistent storage: users, message queue, stats,
settings, and logs.  Uses synchronous sqlite3 (adequate for
a single-owner bot with modest throughput).

Migration-safe: new columns are added dynamically at startup
without dropping or recreating tables.
"""

import json
import logging
import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any, Optional

logger = logging.getLogger(__name__)


class Database:
    """Thread-safe SQLite wrapper for the scheduler bot."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None

    # ── Connection helpers ────────────────────────────────────────────────

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        return self._conn

    def _execute(
        self, sql: str, params: tuple = (), *, commit: bool = True
    ) -> sqlite3.Cursor:
        with self._lock:
            conn = self._get_conn()
            cur = conn.execute(sql, params)
            if commit:
                conn.commit()
            return cur

    def _fetchone(self, sql: str, params: tuple = ()) -> Optional[sqlite3.Row]:
        with self._lock:
            conn = self._get_conn()
            return conn.execute(sql, params).fetchone()

    def _fetchall(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            conn = self._get_conn()
            return conn.execute(sql, params).fetchall()

    # ── Schema initialisation ─────────────────────────────────────────────

    def initialize(self) -> None:
        """Create tables if they do not exist."""
        schema = """
        CREATE TABLE IF NOT EXISTS users (
            user_id     INTEGER PRIMARY KEY,
            username    TEXT,
            first_name  TEXT,
            last_name   TEXT,
            joined_at   TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS queue (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            media_type      TEXT NOT NULL DEFAULT 'text',
            content         TEXT,
            caption         TEXT,
            file_ids        TEXT,
            parse_mode      TEXT DEFAULT 'HTML',
            created_at      TEXT NOT NULL DEFAULT (datetime('now')),
            status          TEXT NOT NULL DEFAULT 'pending',
            retry_count     INTEGER NOT NULL DEFAULT 0,
            message_type    TEXT,
            file_id         TEXT,
            media_group_id  TEXT,
            source_chat_id  INTEGER,
            source_message_id INTEGER,
            last_error      TEXT,
            last_attempt    TEXT,
            paused          INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS stats (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            total_sent      INTEGER NOT NULL DEFAULT 0,
            total_failed    INTEGER NOT NULL DEFAULT 0,
            last_sent_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            level       TEXT NOT NULL,
            message     TEXT NOT NULL,
            created_at  TEXT NOT NULL DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS channels (
            channel_id  TEXT PRIMARY KEY,
            added_at    TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
        with self._lock:
            conn = self._get_conn()
            conn.executescript(schema)
            # Ensure the singleton stats row exists
            conn.execute(
                "INSERT OR IGNORE INTO stats (id, total_sent, total_failed) VALUES (1, 0, 0)"
            )
            conn.commit()
        logger.info("Database initialised at %s", self._db_path)

    def migrate(self) -> None:
        """
        Safe schema migration — add missing columns to existing tables
        without destroying data. Called after initialize() on every startup.
        """
        with self._lock:
            conn = self._get_conn()
            existing = {
                row[1]
                for row in conn.execute("PRAGMA table_info(queue)").fetchall()
            }

        new_columns = {
            "message_type":       "TEXT",
            "file_id":            "TEXT",
            "media_group_id":     "TEXT",
            "source_chat_id":     "INTEGER",
            "source_message_id":  "INTEGER",
            "last_error":         "TEXT",
            "last_attempt":       "TEXT",
            "paused":             "INTEGER NOT NULL DEFAULT 0",
        }

        for col, col_type in new_columns.items():
            if col not in existing:
                try:
                    self._execute(
                        f"ALTER TABLE queue ADD COLUMN {col} {col_type}",
                        commit=True,
                    )
                    logger.info("Migration: added column queue.%s", col)
                except sqlite3.OperationalError as exc:
                    # Column may have been added by another process — safe to ignore
                    logger.debug("Migration skipped for %s: %s", col, exc)

        logger.info("Database migration complete.")

    # ── Users ─────────────────────────────────────────────────────────────

    def add_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        self._execute(
            """
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, username, first_name, last_name),
        )

    def get_all_users(self) -> list[dict]:
        rows = self._fetchall("SELECT * FROM users ORDER BY joined_at DESC")
        return [dict(r) for r in rows]

    def get_user_count(self) -> int:
        row = self._fetchone("SELECT COUNT(*) AS cnt FROM users")
        return row["cnt"] if row else 0

    # ── Queue ─────────────────────────────────────────────────────────────

    def add_to_queue(
        self,
        media_type: str = "text",
        content: Optional[str] = None,
        caption: Optional[str] = None,
        file_ids: Optional[list[str]] = None,
        parse_mode: str = "HTML",
        message_type: Optional[str] = None,
        file_id: Optional[str] = None,
        media_group_id: Optional[str] = None,
        source_chat_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
    ) -> int:
        """Insert a new item into the message queue. Returns the row ID."""
        cur = self._execute(
            """
            INSERT INTO queue (
                media_type, content, caption, file_ids, parse_mode,
                message_type, file_id, media_group_id,
                source_chat_id, source_message_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                media_type,
                content,
                caption,
                json.dumps(file_ids) if file_ids else None,
                parse_mode,
                message_type or media_type,
                file_id or (file_ids[0] if file_ids else None),
                media_group_id,
                source_chat_id,
                source_message_id,
            ),
        )
        item_id = cur.lastrowid
        logger.info(
            "Queue insert: #%s type=%s media_group=%s src=%s/%s",
            item_id, media_type, media_group_id, source_chat_id, source_message_id,
        )
        return item_id  # type: ignore[return-value]

    def get_queue_item(self, item_id: int) -> Optional[dict]:
        """Fetch a single queue item by ID."""
        row = self._fetchone("SELECT * FROM queue WHERE id = ?", (item_id,))
        if row:
            d = dict(row)
            if d.get("file_ids"):
                d["file_ids"] = json.loads(d["file_ids"])
            return d
        return None

    def get_next_pending(self) -> Optional[dict]:
        """Return the oldest non-paused pending queue item."""
        row = self._fetchone(
            "SELECT * FROM queue WHERE status = 'pending' AND paused = 0 ORDER BY id ASC LIMIT 1"
        )
        if row:
            d = dict(row)
            if d.get("file_ids"):
                d["file_ids"] = json.loads(d["file_ids"])
            return d
        return None

    def get_all_pending(self) -> list[dict]:
        """Return all pending queue items (paused or not) ordered by id."""
        rows = self._fetchall(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY id ASC"
        )
        results = []
        for r in rows:
            d = dict(r)
            if d.get("file_ids"):
                d["file_ids"] = json.loads(d["file_ids"])
            results.append(d)
        return results

    def get_queue_count(self) -> int:
        row = self._fetchone(
            "SELECT COUNT(*) AS cnt FROM queue WHERE status = 'pending'"
        )
        return row["cnt"] if row else 0

    def get_media_group_items(self, media_group_id: str) -> list[dict]:
        """Return all queue items belonging to a media group."""
        rows = self._fetchall(
            "SELECT * FROM queue WHERE media_group_id = ? AND status = 'pending' ORDER BY id ASC",
            (media_group_id,),
        )
        results = []
        for r in rows:
            d = dict(r)
            if d.get("file_ids"):
                d["file_ids"] = json.loads(d["file_ids"])
            results.append(d)
        return results

    def mark_sent(self, item_id: int) -> None:
        self._execute(
            "UPDATE queue SET status = 'sent' WHERE id = ?", (item_id,)
        )
        logger.info("Queue item #%s marked sent", item_id)

    def mark_failed(self, item_id: int, error: Optional[str] = None) -> None:
        """Increment retry count; mark as 'failed' after MAX_RETRIES exceeded."""
        self._execute(
            """
            UPDATE queue
            SET retry_count   = retry_count + 1,
                last_error    = ?,
                last_attempt  = datetime('now'),
                status        = CASE WHEN retry_count + 1 >= 3 THEN 'failed' ELSE 'pending' END
            WHERE id = ?
            """,
            (error, item_id),
        )
        logger.warning("Queue item #%s marked failed (error: %s)", item_id, error)

    def increment_retry(self, item_id: int, error: str) -> int:
        """
        Increment retry_count and store last_error.
        Returns the new retry_count.
        """
        row = self._fetchone("SELECT retry_count FROM queue WHERE id = ?", (item_id,))
        current = (row["retry_count"] if row else 0) + 1
        self._execute(
            """
            UPDATE queue
            SET retry_count  = ?,
                last_error   = ?,
                last_attempt = datetime('now')
            WHERE id = ?
            """,
            (current, error, item_id),
        )
        return current

    def pause_item(self, item_id: int) -> bool:
        cur = self._execute(
            "UPDATE queue SET paused = 1 WHERE id = ? AND status = 'pending'",
            (item_id,),
        )
        return cur.rowcount > 0

    def resume_item(self, item_id: int) -> bool:
        cur = self._execute(
            "UPDATE queue SET paused = 0 WHERE id = ? AND status = 'pending'",
            (item_id,),
        )
        return cur.rowcount > 0

    def delete_queue_item(self, item_id: int) -> bool:
        cur = self._execute("DELETE FROM queue WHERE id = ?", (item_id,))
        return cur.rowcount > 0

    def clear_queue(self) -> int:
        cur = self._execute("DELETE FROM queue WHERE status = 'pending'")
        return cur.rowcount

    # ── Stats ─────────────────────────────────────────────────────────────

    def increment_sent(self) -> None:
        self._execute(
            "UPDATE stats SET total_sent = total_sent + 1, last_sent_at = datetime('now') WHERE id = 1"
        )

    def increment_failed(self) -> None:
        self._execute(
            "UPDATE stats SET total_failed = total_failed + 1 WHERE id = 1"
        )

    def get_stats(self) -> dict:
        row = self._fetchone("SELECT * FROM stats WHERE id = 1")
        return dict(row) if row else {"total_sent": 0, "total_failed": 0}

    # ── Settings (key-value) ──────────────────────────────────────────────

    def set_setting(self, key: str, value: str) -> None:
        self._execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def get_settings(self) -> dict[str, str]:
        rows = self._fetchall("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in rows}

    # ── Channels ──────────────────────────────────────────────────────────

    def add_channel(self, channel_id: str) -> None:
        self._execute(
            "INSERT OR IGNORE INTO channels (channel_id) VALUES (?)",
            (channel_id,),
        )

    def remove_channel(self, channel_id: str) -> bool:
        cur = self._execute(
            "DELETE FROM channels WHERE channel_id = ?", (channel_id,)
        )
        return cur.rowcount > 0

    def get_channels(self) -> list[str]:
        rows = self._fetchall("SELECT channel_id FROM channels")
        return [r["channel_id"] for r in rows]

    # ── Logs ──────────────────────────────────────────────────────────────

    def add_log(self, level: str, message: str) -> None:
        self._execute(
            "INSERT INTO logs (level, message) VALUES (?, ?)",
            (level, message),
        )

    def get_recent_logs(self, limit: int = 20) -> list[dict]:
        rows = self._fetchall(
            "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    def cleanup_logs(self, retention_days: int = 7) -> int:
        cutoff = (datetime.utcnow() - timedelta(days=retention_days)).isoformat()
        cur = self._execute(
            "DELETE FROM logs WHERE created_at < ?", (cutoff,)
        )
        return cur.rowcount
