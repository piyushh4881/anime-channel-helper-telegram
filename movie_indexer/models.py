"""Data models for the Movie Indexer Bot.

Simple dataclasses used across all modules for type-safe data passing.
No ORM overhead — raw SQLite is used for performance at scale.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Movie:
    """Represents a unique movie/anime title in the database."""

    id: Optional[int] = None
    title: str = ""
    year: Optional[int] = None
    clean_name: str = ""       # "Title (Year)" or just "Title"
    studio: str = "Unknown Studio"

    def __post_init__(self) -> None:
        if not self.clean_name and self.title:
            self.clean_name = (
                f"{self.title} ({self.year})" if self.year else self.title
            )


@dataclass
class Release:
    """Represents a specific file/quality release of a movie."""

    id: Optional[int] = None
    movie_id: int = 0
    quality: str = "Unknown"
    message_id: int = 0
    channel_id: int = 0
    telegram_link: str = ""


@dataclass
class ScanState:
    """Tracks the progress of a channel scan for resume support."""

    last_message_id: int = 0
    total_scanned: int = 0
    last_scan_time: str = ""   # ISO-8601 timestamp
