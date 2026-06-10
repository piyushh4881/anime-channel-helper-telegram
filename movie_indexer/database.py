"""Async SQLite database layer for the Movie Indexer Bot.

Provides typed CRUD operations, batch inserts, scan-state persistence,
and index-message tracking. Uses aiosqlite with WAL mode for
concurrent read performance.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiosqlite

from models import Movie, Release, ScanState

logger = logging.getLogger(__name__)


class MovieDatabase:
    """Async wrapper around the movie_indexer SQLite database."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._db: Optional[aiosqlite.Connection] = None
        # In-memory cache: (lower_title, year) → movie.id
        self._movie_cache: dict[tuple[str, Optional[int]], int] = {}

    # ── Lifecycle ────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Open the database connection and initialise tables."""
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode = WAL")
        await self._db.execute("PRAGMA foreign_keys = ON")
        await self._db.execute("PRAGMA busy_timeout = 5000")
        await self._init_tables()
        logger.info(f"Database connected: {self.db_path}")

    async def close(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
            self._db = None
            logger.info("Database connection closed")

    async def _init_tables(self) -> None:
        """Create tables and indices if they don't exist."""
        await self._db.executescript(
            """
            CREATE TABLE IF NOT EXISTS movies (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                year        INTEGER,
                clean_name  TEXT    NOT NULL,
                studio      TEXT    DEFAULT 'Unknown Studio',
                UNIQUE(title, year)
            );

            CREATE TABLE IF NOT EXISTS releases (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                movie_id       INTEGER NOT NULL,
                quality        TEXT    NOT NULL,
                message_id     INTEGER NOT NULL,
                channel_id     INTEGER NOT NULL,
                telegram_link  TEXT    NOT NULL,
                FOREIGN KEY (movie_id) REFERENCES movies(id) ON DELETE CASCADE,
                UNIQUE(movie_id, message_id)
            );

            CREATE TABLE IF NOT EXISTS scan_state (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS index_messages (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id   INTEGER NOT NULL,
                channel_id   INTEGER NOT NULL,
                page_number  INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_movies_title
                ON movies(title);
            CREATE INDEX IF NOT EXISTS idx_movies_clean_name
                ON movies(clean_name);
            CREATE INDEX IF NOT EXISTS idx_movies_studio
                ON movies(studio);
            CREATE INDEX IF NOT EXISTS idx_releases_movie_id
                ON releases(movie_id);
            """
        )
        await self._db.commit()

    # ── Movie CRUD ───────────────────────────────────────────────────

    async def upsert_movie(
        self,
        title: str,
        year: Optional[int],
        studio: str = "Unknown Studio",
    ) -> int:
        """Insert a movie or return the existing ID. Updates studio if better info found.

        Returns
        -------
        int
            The movie's database ID.
        """
        cache_key = (title.lower().strip(), year)

        if cache_key in self._movie_cache:
            movie_id = self._movie_cache[cache_key]
            # Upgrade studio from "Unknown" if we now have a real name
            if studio and studio != "Unknown Studio":
                await self._db.execute(
                    "UPDATE movies SET studio = ? WHERE id = ? AND studio = 'Unknown Studio'",
                    (studio, movie_id),
                )
            return movie_id

        clean_name = f"{title} ({year})" if year else title

        try:
            cursor = await self._db.execute(
                "INSERT INTO movies (title, year, clean_name, studio) VALUES (?, ?, ?, ?)",
                (title, year, clean_name, studio),
            )
            movie_id = cursor.lastrowid
        except aiosqlite.IntegrityError:
            # Already exists — fetch it
            cursor = await self._db.execute(
                "SELECT id FROM movies WHERE title = ? AND year IS ?",
                (title, year),
            )
            row = await cursor.fetchone()
            movie_id = row[0]
            if studio and studio != "Unknown Studio":
                await self._db.execute(
                    "UPDATE movies SET studio = ? WHERE id = ?",
                    (studio, movie_id),
                )

        self._movie_cache[cache_key] = movie_id
        return movie_id

    async def add_release(
        self,
        movie_id: int,
        quality: str,
        message_id: int,
        channel_id: int,
        telegram_link: str,
    ) -> bool:
        """Add a release entry. Returns False if it's a duplicate."""
        try:
            await self._db.execute(
                "INSERT INTO releases (movie_id, quality, message_id, channel_id, telegram_link) "
                "VALUES (?, ?, ?, ?, ?)",
                (movie_id, quality, message_id, channel_id, telegram_link),
            )
            return True
        except aiosqlite.IntegrityError:
            return False

    async def commit(self) -> None:
        """Flush pending writes to disk."""
        if self._db:
            await self._db.commit()

    # ── Queries ──────────────────────────────────────────────────────

    async def get_movies_grouped_by_studio(
        self,
    ) -> dict[str, list[tuple[Movie, list[Release]]]]:
        """Return all movies grouped by studio, sorted alphabetically."""
        cursor = await self._db.execute(
            "SELECT id, title, year, clean_name, studio "
            "FROM movies ORDER BY studio, title"
        )
        rows = await cursor.fetchall()

        result: dict[str, list[tuple[Movie, list[Release]]]] = {}

        for row in rows:
            movie = Movie(
                id=row["id"],
                title=row["title"],
                year=row["year"],
                clean_name=row["clean_name"],
                studio=row["studio"],
            )

            rel_cursor = await self._db.execute(
                "SELECT id, movie_id, quality, message_id, channel_id, telegram_link "
                "FROM releases WHERE movie_id = ? ORDER BY quality",
                (movie.id,),
            )
            rel_rows = await rel_cursor.fetchall()
            releases = [
                Release(
                    id=r["id"],
                    movie_id=r["movie_id"],
                    quality=r["quality"],
                    message_id=r["message_id"],
                    channel_id=r["channel_id"],
                    telegram_link=r["telegram_link"],
                )
                for r in rel_rows
            ]

            studio_key = movie.studio or "Unknown Studio"
            if studio_key not in result:
                result[studio_key] = []
            result[studio_key].append((movie, releases))

        return result

    async def search_movies(
        self, query: str
    ) -> list[tuple[Movie, list[Release]]]:
        """Search movies by title (case-insensitive LIKE)."""
        cursor = await self._db.execute(
            "SELECT id, title, year, clean_name, studio FROM movies "
            "WHERE title LIKE ? OR clean_name LIKE ? "
            "ORDER BY title LIMIT 25",
            (f"%{query}%", f"%{query}%"),
        )
        rows = await cursor.fetchall()

        results: list[tuple[Movie, list[Release]]] = []
        for row in rows:
            movie = Movie(
                id=row["id"],
                title=row["title"],
                year=row["year"],
                clean_name=row["clean_name"],
                studio=row["studio"],
            )
            rel_cursor = await self._db.execute(
                "SELECT id, movie_id, quality, message_id, channel_id, telegram_link "
                "FROM releases WHERE movie_id = ?",
                (movie.id,),
            )
            rel_rows = await rel_cursor.fetchall()
            releases = [
                Release(
                    id=r["id"],
                    movie_id=r["movie_id"],
                    quality=r["quality"],
                    message_id=r["message_id"],
                    channel_id=r["channel_id"],
                    telegram_link=r["telegram_link"],
                )
                for r in rel_rows
            ]
            results.append((movie, releases))

        return results

    async def get_stats(self) -> dict[str, str | int]:
        """Aggregate statistics for the /stats command."""
        stats: dict[str, str | int] = {}

        cursor = await self._db.execute("SELECT COUNT(*) FROM movies")
        stats["total_movies"] = (await cursor.fetchone())[0]

        cursor = await self._db.execute("SELECT COUNT(*) FROM releases")
        stats["total_releases"] = (await cursor.fetchone())[0]

        cursor = await self._db.execute(
            "SELECT COUNT(DISTINCT studio) FROM movies WHERE studio != 'Unknown Studio'"
        )
        stats["total_studios"] = (await cursor.fetchone())[0]

        cursor = await self._db.execute(
            "SELECT COUNT(DISTINCT message_id) FROM releases"
        )
        stats["total_messages"] = (await cursor.fetchone())[0]

        # Database file size
        if os.path.exists(self.db_path):
            size_bytes = os.path.getsize(self.db_path)
            if size_bytes >= 1_048_576:
                stats["db_size"] = f"{size_bytes / 1_048_576:.1f} MB"
            else:
                stats["db_size"] = f"{size_bytes / 1024:.1f} KB"
        else:
            stats["db_size"] = "N/A"

        return stats

    # ── Scan state (resume support) ──────────────────────────────────

    async def get_scan_state(self) -> Optional[ScanState]:
        """Retrieve the last scan checkpoint."""
        try:
            rows: dict[str, str] = {}
            cursor = await self._db.execute("SELECT key, value FROM scan_state")
            for row in await cursor.fetchall():
                rows[row["key"]] = row["value"]

            if "last_message_id" not in rows:
                return None

            return ScanState(
                last_message_id=int(rows.get("last_message_id", "0")),
                total_scanned=int(rows.get("total_scanned", "0")),
                last_scan_time=rows.get("last_scan_time", ""),
            )
        except Exception as exc:
            logger.warning(f"Failed to read scan state: {exc}")
            return None

    async def set_scan_state(self, state: ScanState) -> None:
        """Save a scan checkpoint for resume support."""
        for key, value in [
            ("last_message_id", str(state.last_message_id)),
            ("total_scanned", str(state.total_scanned)),
            ("last_scan_time", state.last_scan_time),
        ]:
            await self._db.execute(
                "INSERT OR REPLACE INTO scan_state (key, value) VALUES (?, ?)",
                (key, value),
            )
        await self._db.commit()

    # ── Index message tracking ───────────────────────────────────────

    async def save_index_message_ids(
        self, channel_id: int, message_ids: list[int]
    ) -> None:
        """Store the message IDs of the posted index pages."""
        await self._db.execute("DELETE FROM index_messages")
        for page_num, msg_id in enumerate(message_ids, start=1):
            await self._db.execute(
                "INSERT INTO index_messages (message_id, channel_id, page_number) "
                "VALUES (?, ?, ?)",
                (msg_id, channel_id, page_num),
            )
        await self._db.commit()

    async def get_index_message_ids(self) -> list[tuple[int, int]]:
        """Return [(message_id, channel_id), ...] for existing index messages."""
        cursor = await self._db.execute(
            "SELECT message_id, channel_id FROM index_messages ORDER BY page_number"
        )
        return [(row["message_id"], row["channel_id"]) for row in await cursor.fetchall()]

    # ── Maintenance ──────────────────────────────────────────────────

    async def clear_all(self) -> None:
        """Wipe all data (for full re-scan)."""
        await self._db.executescript(
            """
            DELETE FROM releases;
            DELETE FROM movies;
            DELETE FROM scan_state;
            DELETE FROM index_messages;
            """
        )
        await self._db.commit()
        self._movie_cache.clear()
        logger.info("Database cleared")

    async def load_studio_cache(self) -> dict[str, str]:
        """Load all known title→studio mappings for AniList cache priming."""
        cursor = await self._db.execute(
            "SELECT title, studio FROM movies WHERE studio != 'Unknown Studio'"
        )
        return {row["title"]: row["studio"] for row in await cursor.fetchall()}
