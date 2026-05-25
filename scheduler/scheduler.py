"""
APScheduler-backed message scheduler.
=========================================
Manages periodic posting of queued messages to Telegram channels.

Improvements:
- asyncio.Lock() prevents duplicate concurrent sends
- copy_message() preferred over reconstructed sends
- FloodWait, FileReferenceInvalid, and other errors handled gracefully
- Retry logic with configurable MAX_RETRIES
- Media group (album) support
- Full send logging
"""

import asyncio
import logging
import random
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from telegram import Bot, InputMediaPhoto, InputMediaVideo, InputMediaDocument, InputMediaAnimation, InputMediaAudio
from telegram.constants import ParseMode
from telegram.error import TelegramError, FloodWait, BadRequest

from config import Config

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
        self._send_lock = asyncio.Lock()  # prevent concurrent sends

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
        """
        Fetch the next pending queue item and post it to all channels.
        Uses a lock to prevent duplicate concurrent execution.
        """
        # If another send is already running, skip this tick
        if self._send_lock.locked():
            logger.debug("Scheduler tick skipped — previous send still in progress.")
            return

        async with self._send_lock:
            logger.debug("Scheduler tick — checking queue...")
            item = self._db.get_next_pending()
            if item is None:
                logger.debug("No pending items in queue.")
                return

            # Check if item is part of a media group — send the whole group together
            media_group_id = item.get("media_group_id")
            if media_group_id:
                group_items = self._db.get_media_group_items(media_group_id)
                if group_items:
                    await self._post_media_group(group_items)
                    return

            # Optional randomised delay
            if self._randomize and self._interval > 0:
                jitter = random.randint(
                    -self._interval * self._randomize_pct // 100,
                    self._interval * self._randomize_pct // 100,
                )
                if jitter > 0:
                    await asyncio.sleep(jitter)

            await self._post_item(item)

    async def _post_item(self, item: dict) -> None:
        """Send a single item to all channels and update its status."""
        item_id = item["id"]
        logger.info("Sending queue item #%s (type=%s)", item_id, item.get("media_type"))
        success = await self.send_to_channels(item)

        if success:
            self._db.mark_sent(item_id)
            self._db.increment_sent()
            self._db.add_log(
                "INFO",
                f"✅ Sent queue item #{item_id} ({item.get('media_type', 'unknown')})",
            )
            logger.info("Queue item #%s sent successfully.", item_id)
        else:
            retry_count = item.get("retry_count", 0) + 1
            if retry_count >= Config.MAX_RETRIES:
                self._db.mark_failed(item_id, error="Max retries exceeded")
                self._db.increment_failed()
                self._db.add_log("ERROR", f"❌ Item #{item_id} permanently failed after {retry_count} retries")
                logger.error("Queue item #%s permanently failed.", item_id)
            else:
                self._db.increment_retry(item_id, error="Send failed — will retry")
                self._db.add_log("WARNING", f"⚠️ Item #{item_id} failed, retry {retry_count}/{Config.MAX_RETRIES}")

    async def _post_media_group(self, items: list[dict]) -> None:
        """Send an album (media group) to all channels."""
        logger.info(
            "Sending media group '%s' (%d items)",
            items[0].get("media_group_id"), len(items),
        )

        all_ok = True
        for channel in self._channels:
            try:
                await self._send_media_group_to_channel(channel, items)
            except FloodWait as e:
                logger.warning("FloodWait %ss for channel %s — sleeping", e.retry_after, channel)
                await asyncio.sleep(e.retry_after)
                try:
                    await self._send_media_group_to_channel(channel, items)
                except TelegramError as exc2:
                    logger.error("Retry failed for media group to %s: %s", channel, exc2)
                    all_ok = False
            except TelegramError as exc:
                logger.error("Failed to send media group to %s: %s", channel, exc)
                all_ok = False

        # Mark all items in the group
        for item in items:
            if all_ok:
                self._db.mark_sent(item["id"])
            else:
                self._db.mark_failed(item["id"], error="Media group send failed")

        if all_ok:
            self._db.increment_sent()
            self._db.add_log("INFO", f"✅ Sent media group ({len(items)} items)")
        else:
            self._db.increment_failed()
            self._db.add_log("ERROR", f"❌ Media group send failed ({len(items)} items)")

    async def _send_media_group_to_channel(self, channel: str, items: list[dict]) -> None:
        """Build and send an InputMedia album to one channel."""
        # First try copy_message for each if source is available
        first = items[0]
        if first.get("source_chat_id") and first.get("source_message_id"):
            # Copy all messages from source
            for item in items:
                src_chat = item.get("source_chat_id")
                src_msg = item.get("source_message_id")
                if src_chat and src_msg:
                    await self._bot.copy_message(
                        chat_id=channel,
                        from_chat_id=src_chat,
                        message_id=src_msg,
                    )
            return

        # Fallback: reconstruct media group
        media_list = []
        for i, item in enumerate(items):
            file_ids: list = item.get("file_ids") or []
            file_id = item.get("file_id") or (file_ids[0] if file_ids else None)
            caption = item.get("caption") if i == 0 else None
            parse_mode = item.get("parse_mode", "HTML")
            pm = ParseMode.HTML if parse_mode == "HTML" else ParseMode.MARKDOWN_V2
            m_type = item.get("media_type", item.get("message_type", "photo"))

            if m_type == "photo":
                media_list.append(InputMediaPhoto(media=file_id, caption=caption, parse_mode=pm if caption else None))
            elif m_type == "video":
                media_list.append(InputMediaVideo(media=file_id, caption=caption, parse_mode=pm if caption else None))
            elif m_type == "document":
                media_list.append(InputMediaDocument(media=file_id, caption=caption, parse_mode=pm if caption else None))
            elif m_type == "audio":
                media_list.append(InputMediaAudio(media=file_id, caption=caption, parse_mode=pm if caption else None))
            else:
                media_list.append(InputMediaPhoto(media=file_id, caption=caption, parse_mode=pm if caption else None))

        if media_list:
            await self._bot.send_media_group(chat_id=channel, media=media_list)

    async def send_to_channels(self, item: dict) -> bool:
        """Send a single queue item to every configured channel."""
        if not self._channels:
            logger.warning("No channels configured — skipping send.")
            return False

        all_ok = True
        for channel in self._channels:
            try:
                await self._send_one(channel, item)
            except FloodWait as e:
                wait_secs = e.retry_after
                logger.warning("FloodWait %ss — sleeping then retrying for %s", wait_secs, channel)
                self._db.add_log("WARNING", f"⏳ FloodWait {wait_secs}s for {channel}")
                await asyncio.sleep(wait_secs)
                try:
                    await self._send_one(channel, item)
                except TelegramError as exc2:
                    logger.error("Retry failed for %s: %s", channel, exc2)
                    self._db.add_log("ERROR", f"❌ Retry failed for {channel}: {exc2}")
                    all_ok = False
            except BadRequest as exc:
                err = str(exc)
                logger.error("BadRequest sending to %s: %s", channel, err)
                self._db.add_log("ERROR", f"❌ BadRequest for {channel}: {err}")
                # Don't retry on bad_request — mark as permanently failed
                all_ok = False
            except TelegramError as exc:
                logger.error("Failed to send to %s: %s", channel, exc)
                self._db.add_log("ERROR", f"❌ Send failed to {channel}: {exc}")
                all_ok = False
            except Exception as exc:
                logger.error("Unexpected error sending to %s: %s", channel, exc)
                all_ok = False
        return all_ok

    async def _send_one(self, channel: str, item: dict) -> None:
        """
        Dispatch a single queue item to one channel.
        Prefers copy_message() when source_chat_id + source_message_id are available.
        Falls back to reconstructed send using stored file_id/text/caption.
        """
        src_chat = item.get("source_chat_id")
        src_msg = item.get("source_message_id")

        # ── Preferred path: copy the original message ─────────────────────
        if src_chat and src_msg:
            try:
                await self._bot.copy_message(
                    chat_id=channel,
                    from_chat_id=src_chat,
                    message_id=src_msg,
                )
                logger.debug("copy_message success: channel=%s src=%s/%s", channel, src_chat, src_msg)
                return
            except TelegramError as exc:
                logger.warning(
                    "copy_message failed (src=%s/%s), falling back to reconstruction: %s",
                    src_chat, src_msg, exc,
                )
                # Fall through to reconstruction

        # ── Fallback: reconstruct from stored data ─────────────────────────
        media_type = item.get("media_type", "text")
        content = item.get("content")
        caption = item.get("caption")
        file_ids: list[str] = item.get("file_ids") or []
        file_id = item.get("file_id") or (file_ids[0] if file_ids else None)
        parse_mode = item.get("parse_mode", "HTML")

        pm = ParseMode.HTML if parse_mode == "HTML" else ParseMode.MARKDOWN_V2

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
            media_group = [
                InputMediaPhoto(
                    media=fid,
                    caption=caption if i == 0 else None,
                    parse_mode=pm if i == 0 else None,
                )
                for i, fid in enumerate(file_ids)
            ]
            await self._bot.send_media_group(chat_id=channel, media=media_group)

        elif media_type == "video":
            if file_id:
                await self._bot.send_video(
                    chat_id=channel,
                    video=file_id,
                    caption=caption,
                    parse_mode=pm,
                )

        elif media_type == "document":
            if file_id:
                await self._bot.send_document(
                    chat_id=channel,
                    document=file_id,
                    caption=caption,
                    parse_mode=pm,
                )

        elif media_type == "animation":
            if file_id:
                await self._bot.send_animation(
                    chat_id=channel,
                    animation=file_id,
                    caption=caption,
                    parse_mode=pm,
                )

        elif media_type == "audio":
            if file_id:
                await self._bot.send_audio(
                    chat_id=channel,
                    audio=file_id,
                    caption=caption,
                    parse_mode=pm,
                )

        elif media_type == "media_group":
            # Mixed media group stored as file_ids list
            media_group = [
                InputMediaPhoto(
                    media=fid,
                    caption=caption if i == 0 else None,
                    parse_mode=pm if i == 0 else None,
                )
                for i, fid in enumerate(file_ids)
            ]
            if media_group:
                await self._bot.send_media_group(chat_id=channel, media=media_group)

        else:
            logger.warning("Unknown media type '%s' for item #%s", media_type, item.get("id"))

    # ── Instant send (bypass queue) ───────────────────────────────────────

    async def send_instant(self, item: dict) -> bool:
        """Immediately send an item to all channels without going through the queue."""
        return await self.send_to_channels(item)
