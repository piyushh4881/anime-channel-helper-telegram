"""Rate limiter and flood control for Telegram Channel Migrator.

Implements multi-tier rate limiting:
  - Per-minute limit
  - Per-hour limit
  - Per-day limit
  - Random inter-message delay
  - Periodic cooldown pauses
  - FloodWait exception handling with exact sleep
  - Exponential backoff retry

# SAFETY RECOMMENDATIONS FOR LARGE ARCHIVE MIGRATION:
#
# Conservative (safest, recommended for first run):
#   MAX_PER_MINUTE=5, MAX_PER_HOUR=120, MAX_PER_DAY=2000
#   MIN_DELAY=5, MAX_DELAY=12
#   COOLDOWN_EVERY=50, COOLDOWN_MINUTES=15
#
# Moderate (after establishing account trust):
#   MAX_PER_MINUTE=8, MAX_PER_HOUR=200, MAX_PER_DAY=3000
#   MIN_DELAY=3, MAX_DELAY=8
#   COOLDOWN_EVERY=100, COOLDOWN_MINUTES=10
#
# Aggressive (NOT recommended, high ban risk):
#   MAX_PER_MINUTE=15, MAX_PER_HOUR=400, MAX_PER_DAY=5000
#   MIN_DELAY=1, MAX_DELAY=3
#   COOLDOWN_EVERY=200, COOLDOWN_MINUTES=5
#
# For channels with 10,000+ files, expect multi-day migrations.
# Telegram monitors sudden activity spikes — start slow.
# If you receive FloodWait errors, increase delays immediately.
"""

import asyncio
import random
import time
import logging
from collections import deque
from dataclasses import dataclass, field

logger = logging.getLogger("migrator.ratelimit")


@dataclass
class RateLimiter:
    """Multi-tier rate limiter with cooldown and flood handling."""

    # Limits
    max_per_minute: int = 8
    max_per_hour: int = 200
    max_per_day: int = 3000

    # Delays (seconds)
    min_delay: float = 3.0
    max_delay: float = 8.0

    # Cooldowns
    cooldown_every: int = 100
    cooldown_minutes: float = 10.0
    large_cooldown_every: int = 1000
    large_cooldown_minutes: float = 45.0

    # Internal state
    _minute_timestamps: deque = field(default_factory=deque)
    _hour_timestamps: deque = field(default_factory=deque)
    _day_timestamps: deque = field(default_factory=deque)
    _total_sent: int = 0
    _consecutive_errors: int = 0
    _cooldown_callback: object = None  # callable for progress tracking

    def set_cooldown_callback(self, callback) -> None:
        """Set a callback function called when cooldowns start/end."""
        self._cooldown_callback = callback

    def _prune_timestamps(self) -> None:
        """Remove expired timestamps from sliding windows."""
        now = time.time()
        while self._minute_timestamps and now - self._minute_timestamps[0] > 60:
            self._minute_timestamps.popleft()
        while self._hour_timestamps and now - self._hour_timestamps[0] > 3600:
            self._hour_timestamps.popleft()
        while self._day_timestamps and now - self._day_timestamps[0] > 86400:
            self._day_timestamps.popleft()

    async def wait_if_needed(self) -> None:
        """Block until sending is safe under all rate limits.

        Checks minute/hour/day windows and waits if any limit
        would be exceeded.
        """
        while True:
            self._prune_timestamps()

            # Check per-minute limit
            if len(self._minute_timestamps) >= self.max_per_minute:
                wait_until = self._minute_timestamps[0] + 60
                wait_time = wait_until - time.time()
                if wait_time > 0:
                    logger.debug(
                        "Per-minute limit reached (%d/%d), waiting %.1fs",
                        len(self._minute_timestamps),
                        self.max_per_minute,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time + 0.5)
                    continue

            # Check per-hour limit
            if len(self._hour_timestamps) >= self.max_per_hour:
                wait_until = self._hour_timestamps[0] + 3600
                wait_time = wait_until - time.time()
                if wait_time > 0:
                    logger.warning(
                        "Per-hour limit reached (%d/%d), waiting %.0fs (%.1f min)",
                        len(self._hour_timestamps),
                        self.max_per_hour,
                        wait_time,
                        wait_time / 60,
                    )
                    await asyncio.sleep(wait_time + 1.0)
                    continue

            # Check per-day limit
            if len(self._day_timestamps) >= self.max_per_day:
                wait_until = self._day_timestamps[0] + 86400
                wait_time = wait_until - time.time()
                if wait_time > 0:
                    logger.warning(
                        "Per-day limit reached (%d/%d), waiting %.0fs (%.1f min)",
                        len(self._day_timestamps),
                        self.max_per_day,
                        wait_time,
                        wait_time / 60,
                    )
                    await asyncio.sleep(wait_time + 1.0)
                    continue

            break  # All limits OK

    async def apply_delay(self) -> None:
        """Apply a random delay between messages."""
        delay = random.uniform(self.min_delay, self.max_delay)
        logger.debug("Applying inter-message delay: %.1fs", delay)
        await asyncio.sleep(delay)

    def record_send(self) -> None:
        """Record that a message was sent successfully."""
        now = time.time()
        self._minute_timestamps.append(now)
        self._hour_timestamps.append(now)
        self._day_timestamps.append(now)
        self._total_sent += 1
        self._consecutive_errors = 0

    async def check_cooldown(self) -> None:
        """Check if a cooldown pause is needed based on total files sent."""
        # Large cooldown check (e.g., every 1000 files)
        if (
            self.large_cooldown_every > 0
            and self._total_sent > 0
            and self._total_sent % self.large_cooldown_every == 0
        ):
            minutes = random.uniform(
                self.large_cooldown_minutes * 0.7,
                self.large_cooldown_minutes * 1.3,
            )
            logger.warning(
                "🧊 LARGE COOLDOWN: %d files sent. Pausing for %.1f minutes...",
                self._total_sent,
                minutes,
            )
            if self._cooldown_callback:
                self._cooldown_callback()
            await asyncio.sleep(minutes * 60)
            logger.info("🔥 Large cooldown ended. Resuming migration.")
            return

        # Regular cooldown check (e.g., every 100 files)
        if (
            self.cooldown_every > 0
            and self._total_sent > 0
            and self._total_sent % self.cooldown_every == 0
        ):
            minutes = random.uniform(
                self.cooldown_minutes * 0.5,
                self.cooldown_minutes * 1.5,
            )
            logger.info(
                "🧊 Cooldown: %d files sent. Pausing for %.1f minutes...",
                self._total_sent,
                minutes,
            )
            if self._cooldown_callback:
                self._cooldown_callback()
            await asyncio.sleep(minutes * 60)
            logger.info("🔥 Cooldown ended. Resuming migration.")

    async def handle_flood_wait(self, wait_seconds: int) -> None:
        """Handle a FloodWait exception by sleeping the exact duration.

        Args:
            wait_seconds: Number of seconds Telegram requires us to wait.
        """
        # Add a small buffer for safety
        total_wait = wait_seconds + random.uniform(5, 15)
        logger.warning(
            "⏳ FloodWait received: %ds. Sleeping for %.0fs (with buffer)...",
            wait_seconds,
            total_wait,
        )
        await asyncio.sleep(total_wait)
        logger.info("FloodWait sleep completed. Resuming.")

    def get_retry_delay(self) -> float:
        """Get exponential backoff delay for retries.

        Returns:
            Delay in seconds with jitter.
        """
        self._consecutive_errors += 1
        # Exponential backoff: 5s, 10s, 20s, 40s, ... up to 5 minutes
        base_delay = min(5 * (2 ** (self._consecutive_errors - 1)), 300)
        jitter = random.uniform(0, base_delay * 0.3)
        delay = base_delay + jitter
        logger.info(
            "Retry #%d — backing off for %.1fs",
            self._consecutive_errors,
            delay,
        )
        return delay

    def reset_errors(self) -> None:
        """Reset consecutive error counter after a successful operation."""
        self._consecutive_errors = 0

    @property
    def total_sent(self) -> int:
        return self._total_sent

    @total_sent.setter
    def total_sent(self, value: int) -> None:
        self._total_sent = value
