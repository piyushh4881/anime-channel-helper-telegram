"""Real-time progress display for Telegram Channel Migrator.

Displays migration statistics including:
  - Total messages scanned
  - Total media found
  - Total migrated
  - Current message ID
  - Estimated remaining
  - Runtime duration
  - Transfer speed (files/hour)
"""

import time
import logging
from dataclasses import dataclass, field

logger = logging.getLogger("migrator.progress")


@dataclass
class ProgressTracker:
    """Tracks and displays migration progress."""

    total_messages_scanned: int = 0
    total_media_found: int = 0
    total_migrated: int = 0
    total_skipped_text: int = 0
    total_skipped_duplicate: int = 0
    total_albums_detected: int = 0
    total_albums_migrated: int = 0
    total_errors: int = 0
    total_flood_waits: int = 0
    total_cooldowns: int = 0
    current_message_id: int = 0
    estimated_total: int = 0
    start_time: float = field(default_factory=time.time)
    _last_display: float = field(default_factory=time.time)
    _display_interval: float = 30.0  # Display progress every N seconds

    @property
    def runtime_seconds(self) -> float:
        return time.time() - self.start_time

    @property
    def runtime_formatted(self) -> str:
        seconds = int(self.runtime_seconds)
        hours, remainder = divmod(seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    @property
    def speed_per_hour(self) -> float:
        hours = self.runtime_seconds / 3600
        return self.total_migrated / hours if hours > 0 else 0

    @property
    def estimated_remaining(self) -> int:
        if self.estimated_total > 0:
            return max(0, self.estimated_total - self.total_messages_scanned)
        return 0

    @property
    def estimated_time_remaining(self) -> str:
        if self.speed_per_hour <= 0 or self.estimated_remaining <= 0 or self.total_messages_scanned <= 0:
            return "--:--:--"
        # Rough estimate: assume same ratio of media to messages
        remaining_hours = self.estimated_remaining / (
            self.total_messages_scanned / max(self.runtime_seconds / 3600, 0.001)
        )
        remaining_seconds = int(remaining_hours * 3600)
        hours, remainder = divmod(remaining_seconds, 3600)
        minutes, secs = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def record_scan(self, message_id: int) -> None:
        """Record a message scan."""
        self.total_messages_scanned += 1
        self.current_message_id = message_id

    def record_media_found(self) -> None:
        self.total_media_found += 1

    def record_migrated(self) -> None:
        self.total_migrated += 1

    def record_skipped_text(self) -> None:
        self.total_skipped_text += 1

    def record_skipped_duplicate(self) -> None:
        self.total_skipped_duplicate += 1

    def record_album(self) -> None:
        self.total_albums_detected += 1

    def record_album_migrated(self) -> None:
        self.total_albums_migrated += 1

    def record_error(self) -> None:
        self.total_errors += 1

    def record_flood_wait(self) -> None:
        self.total_flood_waits += 1

    def record_cooldown(self) -> None:
        self.total_cooldowns += 1

    def should_display(self) -> bool:
        """Check if it's time to display progress."""
        now = time.time()
        if now - self._last_display >= self._display_interval:
            self._last_display = now
            return True
        return False

    def display(self, force: bool = False) -> None:
        """Log current progress statistics."""
        if not force and not self.should_display():
            return

        self._last_display = time.time()
        remaining = self.estimated_remaining
        pct = ""
        if self.estimated_total > 0:
            pct_val = (self.total_messages_scanned / self.estimated_total) * 100
            pct = f" ({pct_val:.1f}%)"

        logger.info(
            "\n"
            "══════════════════════════════════════════\n"
            "  📊 MIGRATION PROGRESS\n"
            "──────────────────────────────────────────\n"
            "  Messages scanned : %d%s\n"
            "  Media found      : %d\n"
            "  Migrated         : %d\n"
            "  Skipped (text)   : %d\n"
            "  Skipped (dupes)  : %d\n"
            "  Albums           : %d detected / %d migrated\n"
            "  Errors           : %d\n"
            "  Flood waits      : %d\n"
            "  Cooldowns        : %d\n"
            "──────────────────────────────────────────\n"
            "  Current msg ID   : %d\n"
            "  Remaining (est.) : %d messages\n"
            "  Runtime          : %s\n"
            "  Speed            : %.1f files/hour\n"
            "  ETA              : %s\n"
            "══════════════════════════════════════════",
            self.total_messages_scanned,
            pct,
            self.total_media_found,
            self.total_migrated,
            self.total_skipped_text,
            self.total_skipped_duplicate,
            self.total_albums_detected,
            self.total_albums_migrated,
            self.total_errors,
            self.total_flood_waits,
            self.total_cooldowns,
            self.current_message_id,
            remaining,
            self.runtime_formatted,
            self.speed_per_hour,
            self.estimated_time_remaining,
        )

    def summary(self) -> None:
        """Display final migration summary."""
        logger.info(
            "\n"
            "╔══════════════════════════════════════════╗\n"
            "║       🏁 MIGRATION COMPLETE              ║\n"
            "╠══════════════════════════════════════════╣\n"
            "║  Messages scanned : %-20d ║\n"
            "║  Media found      : %-20d ║\n"
            "║  Migrated         : %-20d ║\n"
            "║  Errors           : %-20d ║\n"
            "║  Total runtime    : %-20s ║\n"
            "║  Avg speed        : %-16.1f f/h  ║\n"
            "╚══════════════════════════════════════════╝",
            self.total_messages_scanned,
            self.total_media_found,
            self.total_migrated,
            self.total_errors,
            self.runtime_formatted,
            self.speed_per_hour,
        )
