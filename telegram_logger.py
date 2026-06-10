"""Telegram log channel reporter for Channel Migrator.

Sends structured progress updates to a dedicated Telegram log channel:
  - Startup notification
  - Hourly progress reports (auto-scheduled)
  - Shutdown report with last migrated message link for easy resume
  - FloodWait alerts
  - Error notifications

This gives you a remote dashboard of the migration without needing
to watch the console.
"""

import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient
from telethon.tl.types import Channel

logger = logging.getLogger("migrator.tg_logger")


class TelegramLogger:
    """Sends migration log updates to a Telegram channel.

    Attributes:
        client: Authenticated TelegramClient.
        log_channel: Log channel entity (ID or username).
        source_channel: Source channel entity (for building message links).
        hourly_task: Background task for hourly reports.
    """

    def __init__(
        self,
        client: TelegramClient,
        log_channel_id: int | str,
        source_channel_id: int | str,
        destination_channel_id: int | str,
    ) -> None:
        self.client = client
        self.log_channel_id = log_channel_id
        self.source_channel_id = source_channel_id
        self.destination_channel_id = destination_channel_id
        self._log_entity = None
        self._source_entity = None
        self._hourly_task: asyncio.Task | None = None
        self._progress_ref = None  # Will hold reference to ProgressTracker
        self._running = False

    async def connect(self) -> bool:
        """Validate access to the log channel.

        Returns:
            True if the log channel is accessible.
        """
        try:
            self._log_entity = await self.client.get_entity(self.log_channel_id)
            logger.info(
                "✅ Log channel connected: %s",
                getattr(self._log_entity, "title", self.log_channel_id),
            )
            return True
        except Exception as e:
            logger.error("❌ Cannot access log channel %s: %s", self.log_channel_id, e)
            return False

    async def _send(self, text: str) -> None:
        """Send a message to the log channel (silently handles errors)."""
        if not self._log_entity:
            return
        try:
            await self.client.send_message(
                self._log_entity,
                text,
                parse_mode="html",
                link_preview=False,
            )
        except Exception as e:
            logger.warning("Failed to send log to Telegram channel: %s", e)

    def _build_message_link(self, channel_id: int | str, message_id: int) -> str:
        """Build a t.me link to a specific message."""
        if isinstance(channel_id, int):
            # Convert -100XXXXXXXXXX to XXXXXXXXXX for t.me/c/ links
            raw_id = str(channel_id)
            if raw_id.startswith("-100"):
                raw_id = raw_id[4:]
            return f"https://t.me/c/{raw_id}/{message_id}"
        else:
            return f"https://t.me/{channel_id}/{message_id}"

    async def send_startup(
        self,
        source_name: str,
        dest_name: str,
        resume_from: int | None,
        already_migrated: int,
        estimated_total: int,
        dry_run: bool = False,
    ) -> None:
        """Send bot startup notification."""
        mode = "🔍 DRY RUN" if dry_run else "🚀 MIGRATION"
        resume_info = ""
        if resume_from:
            link = self._build_message_link(self.source_channel_id, resume_from)
            resume_info = (
                f"\n📂 <b>Resuming from:</b> <a href=\"{link}\">msg #{resume_from}</a>"
                f"\n✅ <b>Already migrated:</b> {already_migrated:,} files"
            )
        else:
            resume_info = "\n📂 <b>Starting fresh</b> (no previous progress)"

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        text = (
            f"{'━' * 30}\n"
            f"  {mode} STARTED\n"
            f"{'━' * 30}\n\n"
            f"📅 <b>Time:</b> {now}\n"
            f"📤 <b>Source:</b> {source_name}\n"
            f"📥 <b>Destination:</b> {dest_name}\n"
            f"📊 <b>Estimated total:</b> ~{estimated_total:,} messages"
            f"{resume_info}\n\n"
            f"⏳ Hourly updates will follow..."
        )
        await self._send(text)

    async def send_hourly_report(self, progress) -> None:
        """Send an hourly progress snapshot."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        pct = ""
        if progress.estimated_total > 0:
            pct_val = (progress.total_messages_scanned / progress.estimated_total) * 100
            pct = f" ({pct_val:.1f}%)"

        last_link = ""
        if progress.current_message_id:
            link = self._build_message_link(
                self.source_channel_id, progress.current_message_id
            )
            last_link = f'\n🔗 <b>Current:</b> <a href="{link}">msg #{progress.current_message_id}</a>'

        text = (
            f"⏰ <b>HOURLY REPORT</b> — {now}\n"
            f"{'─' * 30}\n\n"
            f"📊 <b>Scanned:</b> {progress.total_messages_scanned:,}{pct}\n"
            f"🖼 <b>Media found:</b> {progress.total_media_found:,}\n"
            f"✅ <b>Migrated:</b> {progress.total_migrated:,}\n"
            f"⏭ <b>Skipped (text):</b> {progress.total_skipped_text:,}\n"
            f"🔄 <b>Skipped (dupes):</b> {progress.total_skipped_duplicate:,}\n"
            f"📦 <b>Albums:</b> {progress.total_albums_detected} detected / "
            f"{progress.total_albums_migrated} migrated\n"
            f"❌ <b>Errors:</b> {progress.total_errors}\n"
            f"⏳ <b>Flood waits:</b> {progress.total_flood_waits}\n"
            f"🧊 <b>Cooldowns:</b> {progress.total_cooldowns}\n\n"
            f"⏱ <b>Runtime:</b> {progress.runtime_formatted}\n"
            f"🚄 <b>Speed:</b> {progress.speed_per_hour:.0f} files/hr\n"
            f"⏳ <b>ETA:</b> {progress.estimated_time_remaining}"
            f"{last_link}"
        )
        await self._send(text)

    async def send_batch_report(
        self,
        batch_messages: list,
        next_forward_delay: float,
    ) -> None:
        """Send a summary of the forwarded batch to the log channel."""
        from datetime import timedelta
        from telethon.tl.types import DocumentAttributeFilename

        now = datetime.now(timezone.utc).astimezone()  # Local time
        next_time = now + timedelta(seconds=next_forward_delay)
        next_time_str = next_time.strftime("%Y-%m-%d %H:%M:%S %Z")

        # Compile list of files in the batch
        lines = []
        for msg in batch_messages:
            file_name = ""
            if msg.document and msg.document.attributes:
                for attr in msg.document.attributes:
                    if isinstance(attr, DocumentAttributeFilename):
                        file_name = attr.file_name
                        break
            desc = file_name or (msg.text or "").replace("\n", " ")[:30] or "Document"
            lines.append(f"• <code>#{msg.id}</code>: {desc}")

        # Limit to first 25 files to avoid exceeding message length limit
        max_display = 25
        displayed_lines = lines[:max_display]
        extra = len(lines) - max_display

        file_list_str = "\n".join(displayed_lines)
        if extra > 0:
            file_list_str += f"\n• <i>... and {extra} more files</i>"

        text = (
            f"📦 <b>BATCH FORWARD COMPLETED</b>\n"
            f"📅 <b>Time:</b> {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"📈 <b>Files in Batch:</b> {len(batch_messages)}\n"
            f"{'─' * 30}\n"
            f"{file_list_str}\n"
            f"{'─' * 30}\n"
            f"⏳ <b>Next forward scheduled at:</b>\n"
            f"👉 <code>{next_time_str}</code> (in {int(next_forward_delay / 60)}m)"
        )
        await self._send(text)

    async def send_forward_log(
        self,
        message,
        next_forward_delay: float,
        dest_id: int | None = None,
        dry_run: bool = False,
    ) -> None:
        """Send a notification for a single forwarded message."""
        from datetime import datetime, timezone, timedelta
        from telethon.tl.types import DocumentAttributeFilename

        now = datetime.now(timezone.utc).astimezone()  # Local time
        next_time = now + timedelta(seconds=next_forward_delay)
        next_time_str = next_time.strftime("%Y-%m-%d %H:%M:%S %Z")

        file_name = ""
        if message.document and message.document.attributes:
            for attr in message.document.attributes:
                if isinstance(attr, DocumentAttributeFilename):
                    file_name = attr.file_name
                    break
        desc = file_name or (message.text or "").replace("\n", " ")[:40] or "Document"

        mode_prefix = "🔍 [DRY RUN] " if dry_run else "✅ "

        # Source/Destination links
        link = self._build_message_link(self.source_channel_id, message.id)
        links_str = f"<a href=\"{link}\">Source</a>"
        if not dry_run and dest_id:
            dest_link = self._build_message_link(self.destination_channel_id, dest_id)
            links_str += f" ➔ <a href=\"{dest_link}\">Destination</a>"

        text = (
            f"{mode_prefix}<b>FILE FORWARDED</b>\n"
            f"📅 <b>Time:</b> {now.strftime('%Y-%m-%d %H:%M:%S %Z')}\n"
            f"📄 <b>File:</b> <code>{desc}</code>\n"
            f"🔗 <b>Links:</b> {links_str}\n"
            f"{'─' * 30}\n"
            f"⏳ <b>Next forward at:</b>\n"
            f"👉 <code>{next_time_str}</code> (in {next_forward_delay:.1f}s)"
        )
        await self._send(text)


    async def send_shutdown(
        self,
        progress,
        reason: str = "User stopped (Ctrl+C)",
    ) -> None:
        """Send shutdown notification with last migrated message link."""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        last_link = "N/A"
        resume_note = ""
        if progress.current_message_id:
            link = self._build_message_link(
                self.source_channel_id, progress.current_message_id
            )
            last_link = f'<a href="{link}">msg #{progress.current_message_id}</a>'
            resume_note = (
                f"\n\n💡 <b>To resume tomorrow:</b> Just run <code>python bot.py</code> — "
                f"it will pick up automatically from msg #{progress.current_message_id}"
            )

        remaining = progress.estimated_remaining

        text = (
            f"{'━' * 30}\n"
            f"  ⏹ MIGRATION STOPPED\n"
            f"{'━' * 30}\n\n"
            f"📅 <b>Time:</b> {now}\n"
            f"📝 <b>Reason:</b> {reason}\n\n"
            f"📊 <b>Session Summary:</b>\n"
            f"  • Scanned: {progress.total_messages_scanned:,}\n"
            f"  • Migrated: {progress.total_migrated:,}\n"
            f"  • Errors: {progress.total_errors}\n"
            f"  • Runtime: {progress.runtime_formatted}\n"
            f"  • Speed: {progress.speed_per_hour:.0f} files/hr\n\n"
            f"🔗 <b>Last processed:</b> {last_link}\n"
            f"📦 <b>Remaining (est.):</b> ~{remaining:,} messages"
            f"{resume_note}"
        )
        await self._send(text)

    async def send_flood_alert(self, wait_seconds: int) -> None:
        """Send a FloodWait alert to the log channel."""
        text = (
            f"⚠️ <b>FloodWait received!</b>\n"
            f"Telegram requires a <b>{wait_seconds}s</b> wait.\n"
            f"Bot is sleeping and will resume automatically."
        )
        await self._send(text)

    async def send_error(self, error_msg: str) -> None:
        """Send a critical error notification."""
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        text = f"🔥 <b>ERROR</b> at {now}\n\n<code>{error_msg[:1000]}</code>"
        await self._send(text)

    async def start_hourly_reports(self, progress) -> None:
        """Start the background task for hourly reports."""
        self._progress_ref = progress
        self._running = True
        self._hourly_task = asyncio.create_task(self._hourly_loop())
        logger.info("Hourly Telegram reports scheduled")

    async def _hourly_loop(self) -> None:
        """Send a report every hour."""
        while self._running:
            try:
                await asyncio.sleep(3600)  # 1 hour
                if self._running and self._progress_ref:
                    await self.send_hourly_report(self._progress_ref)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Hourly report error: %s", e)

    async def stop(self) -> None:
        """Stop the hourly report task."""
        self._running = False
        if self._hourly_task and not self._hourly_task.done():
            self._hourly_task.cancel()
            try:
                await self._hourly_task
            except asyncio.CancelledError:
                pass
        logger.info("Hourly Telegram reports stopped")
