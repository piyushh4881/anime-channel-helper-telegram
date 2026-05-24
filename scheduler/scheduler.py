"""
APScheduler-backed message scheduler.
=========================================
Manages periodic posting of queued messages to Telegram channels.
"""

import asyncio
import logging
import random
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.constants import ParseMode
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


class Scheduler:
    """Wraps APScheduler for posting queued messages at a configurable interval."""

    JOB_ID = "queue_poster"
    CLEANUP_JOB_ID = "log_cleanup"

    def __init__(self, db, bot: Bot, channels: list[str]) -> None:
        self._db = db
        self._bot = bot
        self._channels = list(channels)
        self._scheduler = AsyncIOScheduler()
        self._scheduler.start()  # start the event-loop scheduler immediately
        self._running = False
        self._interval: int = 0  # seconds
        self._randomize: bool = False
        self._randomize_pct: int = 20  # ± percentage for randomisation

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def interval(self) -> int:
        return self._interval

    @property
    def channels(self) -> list[str]:
        return list(self._channels)

    # ── Channel management ────────────────────────────────────────────────

    def add_channel(self, channel_id: str) -> None:
        if channel_id not in self._channels:
            self._channels.append(channel_id)
            self._db.add_channel(channel_id)

    def remove_channel(self, channel_id: str) -> bool:
        if channel_id in self._channels:
            self._channels.remove(channel_id)
            self._db.remove_channel(channel_id)
            return True
        return False

    def set_channels(self, channels: list[str]) -> None:
        self._channels = list(channels)

    # ── Scheduler control ─────────────────────────────────────────────────

    def start(self, interval: int, randomize: bool = False) -> None:
        """Start (or restart) the periodic posting job."""
        self._interval = interval
        self._randomize = randomize
        self._running = True

        # Remove old job if present
        if self._scheduler.get_job(self.JOB_ID):
            self._scheduler.remove_job(self.JOB_ID)

        trigger = IntervalTrigger(seconds=interval)
        self._scheduler.add_job(
            self._post_next,
            trigger=trigger,
            id=self.JOB_ID,
            replace_existing=True,
            max_instances=1,
        )

        # Persist state
        self._db.set_setting("scheduler_running", "1")
        self._db.set_setting("interval", str(interval))
        self._db.set_setting("randomize", "1" if randomize else "0")

        logger.info("Scheduler started — interval %ss (randomize=%s)", interval, randomize)

    def stop(self) -> None:
        """Stop the periodic posting job."""
        if self._scheduler.get_job(self.JOB_ID):
            self._scheduler.remove_job(self.JOB_ID)
        self._running = False
        self._db.set_setting("scheduler_running", "0")
        logger.info("Scheduler stopped.")

    def shutdown(self) -> None:
        """Shut down the APScheduler instance entirely."""
        try:
            self._scheduler.shutdown(wait=False)
        except Exception:
            pass

    def schedule_cleanup(self, retention_days: int) -> None:
        """Schedule a daily job to clean old log entries."""
        if self._scheduler.get_job(self.CLEANUP_JOB_ID):
            return  # already scheduled
        self._scheduler.add_job(
            self._run_cleanup,
            trigger=IntervalTrigger(hours=24),
            id=self.CLEANUP_JOB_ID,
            replace_existing=True,
            kwargs={"retention_days": retention_days},
        )

    async def _run_cleanup(self, retention_days: int = 7) -> None:
        from utils.helpers import cleanup_old_logs
        cleanup_old_logs(self._db, retention_days)

    # ── Core posting logic ────────────────────────────────────────────────

    async def _post_next(self) -> None:
        """Fetch the next pending queue item and post it to all channels."""
        item = self._db.get_next_pending()
        if item is None:
            logger.debug("No pending items in queue.")
            return

        # Optional randomised delay
        if self._randomize and self._interval > 0:
            jitter = random.randint(
                -self._interval * self._randomize_pct // 100,
                self._interval * self._randomize_pct // 100,
            )
            if jitter > 0:
                await asyncio.sleep(jitter)

        success = await self.send_to_channels(item)

        if success:
            self._db.mark_sent(item["id"])
            self._db.increment_sent()
            self._db.add_log("INFO", f"Sent queue item #{item['id']} ({item['media_type']})")
        else:
            self._db.mark_failed(item["id"])
            self._db.increment_failed()
            self._db.add_log("ERROR", f"Failed to send queue item #{item['id']}")

    async def send_to_channels(self, item: dict) -> bool:
        """Send a single queue item to every configured channel."""
        if not self._channels:
            logger.warning("No channels configured — skipping send.")
            return False

        all_ok = True
        for channel in self._channels:
            try:
                await self._send_one(channel, item)
            except TelegramError as exc:
                logger.error("Failed to send to %s: %s", channel, exc)
                all_ok = False
            except Exception as exc:
                logger.error("Unexpected error sending to %s: %s", channel, exc)
                all_ok = False
        return all_ok

    async def _send_one(self, channel: str, item: dict) -> None:
        """Dispatch a single queue item to one channel."""
        media_type = item.get("media_type", "text")
        content = item.get("content")
        caption = item.get("caption")
        file_ids: list[str] = item.get("file_ids") or []
        parse_mode = item.get("parse_mode", "HTML")

        pm = ParseMode.HTML if parse_mode == "HTML" else ParseMode.MARKDOWN

        if media_type == "text":
            await self._bot.send_message(
                chat_id=channel, text=content or "", parse_mode=pm
            )

        elif media_type == "photo" and len(file_ids) == 1:
            await self._bot.send_photo(
                chat_id=channel,
                photo=file_ids[0],
                caption=caption,
                parse_mode=pm,
            )

        elif media_type == "photo" and len(file_ids) > 1:
            media_group = []
            for i, fid in enumerate(file_ids):
                media_group.append(
                    InputMediaPhoto(
                        media=fid,
                        caption=caption if i == 0 else None,
                        parse_mode=pm if i == 0 else None,
                    )
                )
            await self._bot.send_media_group(chat_id=channel, media=media_group)

        elif media_type == "video":
            if file_ids:
                await self._bot.send_video(
                    chat_id=channel,
                    video=file_ids[0],
                    caption=caption,
                    parse_mode=pm,
                )

        elif media_type == "document":
            if file_ids:
                await self._bot.send_document(
                    chat_id=channel,
                    document=file_ids[0],
                    caption=caption,
                    parse_mode=pm,
                )

        elif media_type == "media_group":
            # Mixed media group
            media_group = []
            for i, fid in enumerate(file_ids):
                media_group.append(
                    InputMediaPhoto(
                        media=fid,
                        caption=caption if i == 0 else None,
                        parse_mode=pm if i == 0 else None,
                    )
                )
            if media_group:
                await self._bot.send_media_group(chat_id=channel, media=media_group)

        else:
            logger.warning("Unknown media type '%s' for item #%s", media_type, item.get("id"))

    # ── Instant send (bypass queue) ───────────────────────────────────────

    async def send_instant(self, item: dict) -> bool:
        """Immediately send an item to all channels without going through the queue."""
        return await self.send_to_channels(item)
