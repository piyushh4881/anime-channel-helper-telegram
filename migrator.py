"""Core migration engine for Telegram Channel Migrator.

Orchestrates the entire migration workflow:
  1. Validate channel access
  2. Restore progress from SQLite
  3. Iterate source channel history (oldest → newest)
  4. Detect albums via grouped_id
  5. Clean captions (remove DDL references)
  6. Send media to destination (no forwarding attribution)
  7. Save progress after every successful transfer
  8. Handle FloodWait, rate limits, cooldowns
  9. Optionally switch to live-monitor mode

The migrator uses client.send_file() with the source media's InputMedia
reference to avoid downloading and re-uploading. This minimises bandwidth
and reduces Telegram spam-detection risk.
"""

import asyncio
import logging
import random
import re
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.types import (
    Message,
    Channel,
    Chat,
    MessageMediaPhoto,
    MessageMediaDocument,
    MessageMediaWebPage,
    MessageService,
)
from telethon.errors import (
    FloodWaitError,
    ChannelPrivateError,
    ChatWriteForbiddenError,
    MediaEmptyError,
    FileReferenceExpiredError,
    ChatForwardsRestrictedError,
)

from config import Config
from database import MigrationDatabase
from rate_limiter import RateLimiter
from album_handler import (
    AlbumCollector,
    send_album,
    has_media,
    get_media_type,
    compute_checksum,
)
from caption_processor import clean_entities
from progress_tracker import ProgressTracker
from telegram_logger import TelegramLogger

logger = logging.getLogger("migrator.core")

# Maximum consecutive errors before aborting
MAX_CONSECUTIVE_ERRORS = 20

# Maximum retries per single message
MAX_RETRIES_PER_MESSAGE = 5


def should_migrate_file(file_name: str | None) -> bool:
    """Check if the document filename matches the allowed names and filters."""
    if not file_name:
        return False

    name_lower = file_name.lower()

    # 1. Check for specific release names with word boundaries
    allowed_terms = ['joy', 'utr', 'qxr', 'tigole', 'ghost', 'natty']
    has_allowed = any(re.search(rf'\b{term}\b', name_lower) for term in allowed_terms)
    
    # Check for "psa" (as a word) and "1080p" both present
    has_psa = bool(re.search(r'\bpsa\b', name_lower))
    has_1080p = "1080p" in name_lower

    if not (has_allowed or (has_psa and has_1080p)):
        return False

    # 2. Check for season/episode pattern like s01e01, s1e1, s01e02, s01.e01, etc.
    if re.search(r's\d+[\s._-]?e\d+', name_lower):
        return False

    return True


class ChannelMigrator:
    """Main migration controller.

    Attributes:
        client: Authenticated TelegramClient instance.
        config: Loaded Config dataclass.
        db: MigrationDatabase for state persistence.
        rate_limiter: RateLimiter for flood control.
        progress: ProgressTracker for display.
    """

    def __init__(
        self,
        client: TelegramClient,
        config: Config,
        db: MigrationDatabase,
    ) -> None:
        self.client = client
        self.config = config
        self.db = db

        self.rate_limiter = RateLimiter(
            max_per_minute=config.max_per_minute,
            max_per_hour=config.max_per_hour,
            max_per_day=config.max_per_day,
            min_delay=config.min_delay,
            max_delay=config.max_delay,
            cooldown_every=config.cooldown_every,
            cooldown_minutes=config.cooldown_minutes,
            large_cooldown_every=config.large_cooldown_every,
            large_cooldown_minutes=config.large_cooldown_minutes,
        )
        self.progress = ProgressTracker()
        self.rate_limiter.set_cooldown_callback(self.progress.record_cooldown)

        self._source_entity = None
        self._dest_entity = None
        self._consecutive_errors = 0
        self._files_sent_this_run = 0

        # Telegram log channel reporter (optional)
        self.tg_logger: TelegramLogger | None = None
        if config.log_channel:
            self.tg_logger = TelegramLogger(
                client=client,
                log_channel_id=config.log_channel,
                source_channel_id=config.source_channel,
                destination_channel_id=config.destination_channel,
            )

    async def validate_channels(self) -> bool:
        """Validate access to both source and destination channels.

        Returns:
            True if both channels are accessible.
        """
        logger.info("Validating channel access...")

        try:
            self._source_entity = await self.client.get_entity(
                self.config.source_channel
            )
            logger.info(
                "✅ Source channel: %s (ID: %s)",
                getattr(self._source_entity, "title", self.config.source_channel),
                getattr(self._source_entity, "id", "?"),
            )
        except (ChannelPrivateError, ValueError) as e:
            logger.error("❌ Cannot access source channel: %s", e)
            return False

        try:
            self._dest_entity = await self.client.get_entity(
                self.config.destination_channel
            )
            logger.info(
                "✅ Destination channel: %s (ID: %s)",
                getattr(self._dest_entity, "title", self.config.destination_channel),
                getattr(self._dest_entity, "id", "?"),
            )
        except (ChannelPrivateError, ValueError) as e:
            logger.error("❌ Cannot access destination channel: %s", e)
            return False

        # Verify write access to destination by checking permissions
        try:
            # For channels, check if we're admin or can post
            if isinstance(self._dest_entity, Channel):
                if self._dest_entity.creator or (
                    self._dest_entity.admin_rights
                    and self._dest_entity.admin_rights.post_messages
                ):
                    logger.info("✅ Write access confirmed for destination channel")
                else:
                    logger.warning(
                        "⚠️ May not have post permissions in destination channel. "
                        "Will attempt migration anyway."
                    )
        except Exception as e:
            logger.warning("Could not verify permissions: %s", e)

        # Connect log channel if configured
        if self.tg_logger:
            if not await self.tg_logger.connect():
                logger.warning("Log channel unavailable — continuing without it")
                self.tg_logger = None

        return True

    async def _get_total_messages(self) -> int:
        """Get approximate total message count in source channel."""
        try:
            # Get the channel's full info for message count estimate
            async for msg in self.client.iter_messages(
                self._source_entity, limit=1
            ):
                return msg.id  # Approximate: highest message ID
        except Exception:
            pass
        return 0

    async def _handle_session_limit_cooldown(self) -> None:
        """Handle cooldown when session limit is reached."""
        cooldown_mins = self.config.session_limit_cooldown_minutes
        logger.warning(
            "🧊 SESSION LIMIT COOLDOWN: %d files migrated. Pausing for %.1f minutes...",
            self.config.session_limit,
            cooldown_mins,
        )
        
        # Send update to Telegram log channel if available
        if self.tg_logger:
            try:
                await self.tg_logger.client.send_message(
                    self.tg_logger.log_channel_id,
                    f"🧊 **Session Limit Reached**: {self.config.session_limit} files migrated.\n"
                    f"Pausing for {cooldown_mins} minutes before resuming...",
                )
            except Exception as e:
                logger.warning("Failed to send limit cooldown log to log channel: %s", e)

        # Pause
        await asyncio.sleep(cooldown_mins * 60)
        
        # Reset counter for this run's limit cycle
        self._files_sent_this_run = 0
        
        logger.info("🔥 Session limit cooldown ended. Resuming migration.")
        if self.tg_logger:
            try:
                await self.tg_logger.client.send_message(
                    self.tg_logger.log_channel_id,
                    "🔥 **Session Limit Cooldown Ended**. Resuming migration...",
                )
            except Exception as e:
                pass

    async def _forward_single_message(self, message: Message) -> bool:
        """Forward a single message to the destination channel.

        Args:
            message: The source message to forward.

        Returns:
            True if forwarding succeeded or was a dry-run, False otherwise.
        """
        # Determine delay for the next message
        delay = random.uniform(self.config.min_delay, self.config.max_delay)

        if self.config.dry_run:
            logger.info(
                "  [DRY RUN] Would forward: msg_id=%d, type=%s, caption=%r",
                message.id,
                get_media_type(message),
                (message.text or "")[:80],
            )
            self.progress.record_migrated()
            self._files_sent_this_run += 1
            
            # Send dry-run update to Telegram log channel
            if self.tg_logger:
                try:
                    await self.tg_logger.send_forward_log(
                        message=message,
                        next_forward_delay=delay,
                        dest_id=None,
                        dry_run=True,
                    )
                except Exception as e:
                    logger.warning("Failed to send forward log to log channel: %s", e)
            return True

        # Actual sending
        retries = 0
        while retries < MAX_RETRIES_PER_MESSAGE:
            # Wait for rate limits
            await self.rate_limiter.wait_if_needed()

            try:
                # Forward using client.forward_messages
                results = await self.client.forward_messages(
                    entity=self._dest_entity,
                    messages=message.id,
                    from_peer=self._source_entity,
                    drop_author=True,
                )

                # Pair message with results
                dest_id = None
                if results:
                    if isinstance(results, list):
                        dest_id = results[0].id if results[0] else None
                    else:
                        dest_id = results.id

                status = "success" if dest_id else "error"

                await self.db.record_migration(
                    source_message_id=message.id,
                    destination_message_id=dest_id,
                    media_type=get_media_type(message),
                    album_group_id=message.grouped_id,
                    status=status,
                    checksum=compute_checksum(message),
                )
                if not self.config.dry_run:
                    await self.db.set_state("last_scanned_id", str(message.id))

                if status == "success":
                    if dest_id:
                        await self._edit_caption_if_needed(message, dest_id)

                    self.progress.record_migrated()
                    self.rate_limiter.record_send()
                    self._consecutive_errors = 0
                    self._files_sent_this_run += 1

                    file_name = ""
                    if message.document and message.document.attributes:
                        from telethon.tl.types import DocumentAttributeFilename
                        for attr in message.document.attributes:
                            if isinstance(attr, DocumentAttributeFilename):
                                file_name = attr.file_name
                                break
                    desc = file_name or (message.text or "").replace("\n", " ")[:40] or "Document"
                    logger.info("  ✅ Forwarded: msg_id=%d -> dest_id=%s (%s)", message.id, dest_id, desc)

                    # Send update to Telegram log channel
                    if self.tg_logger:
                        try:
                            await self.tg_logger.send_forward_log(
                                message=message,
                                next_forward_delay=delay,
                                dest_id=dest_id,
                                dry_run=False,
                            )
                        except Exception as e:
                            logger.warning("Failed to send forward log to log channel: %s", e)

                    logger.info("⏳ Spacing wait: %.1f seconds...", delay)
                    await asyncio.sleep(delay)
                    return True
                else:
                    self.progress.record_error()
                    logger.error("  ❌ Forward failed: msg_id=%d", message.id)
                    return False
            except ChatForwardsRestrictedError:
                logger.warning(
                    "🔒 Chat forwards restricted for msg_id=%d. Falling back to download and upload...",
                    message.id,
                )
                try:
                    sent_msg = await self._download_and_upload_message(message)
                    if sent_msg:
                        dest_id = sent_msg.id
                        await self.db.record_migration(
                            source_message_id=message.id,
                            destination_message_id=dest_id,
                            media_type=get_media_type(message),
                            album_group_id=message.grouped_id,
                            status="success",
                            checksum=compute_checksum(message),
                        )
                        if not self.config.dry_run:
                            await self.db.set_state("last_scanned_id", str(message.id))

                        self.progress.record_migrated()
                        self.rate_limiter.record_send()
                        self._consecutive_errors = 0
                        self._files_sent_this_run += 1

                        file_name = ""
                        if message.document and message.document.attributes:
                            from telethon.tl.types import DocumentAttributeFilename
                            for attr in message.document.attributes:
                                if isinstance(attr, DocumentAttributeFilename):
                                    file_name = attr.file_name
                                    break
                        desc = file_name or (message.text or "").replace("\n", " ")[:40] or "Document"
                        logger.info("  ✅ Migrated via Download/Upload: msg_id=%d -> dest_id=%s (%s)", message.id, dest_id, desc)

                        if self.tg_logger:
                            try:
                                await self.tg_logger.send_forward_log(
                                    message=message,
                                    next_forward_delay=delay,
                                    dest_id=dest_id,
                                    dry_run=False,
                                )
                            except Exception as e:
                                logger.warning("Failed to send forward log to log channel: %s", e)

                        logger.info("⏳ Spacing wait: %.1f seconds...", delay)
                        await asyncio.sleep(delay)
                        return True
                    else:
                        self.progress.record_error()
                        logger.error("  ❌ Download/Upload fallback failed: msg_id=%d", message.id)
                        return False
                except Exception as fallback_err:
                    logger.error("  ❌ Exception in Download/Upload fallback for msg_id=%d: %s", message.id, fallback_err)
                    self.progress.record_error()
                    return False

            except FloodWaitError as e:
                self.progress.record_flood_wait()
                if self.tg_logger:
                    try:
                        await self.tg_logger.send_flood_alert(e.seconds)
                    except Exception:
                        pass
                await self.rate_limiter.handle_flood_wait(e.seconds)
                retries += 1

            except Exception as e:
                self._consecutive_errors += 1
                logger.error(
                    "Error forwarding message %d: %s: %s",
                    message.id,
                    type(e).__name__,
                    e,
                )

                if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    logger.critical(
                        "🔥 %d consecutive errors — aborting migration for safety.",
                        MAX_CONSECUTIVE_ERRORS,
                    )
                    raise RuntimeError(
                        f"Too many consecutive errors ({MAX_CONSECUTIVE_ERRORS})"
                    )

                retries += 1
                await asyncio.sleep(2)  # Cooldown before retry

        # If retries exceeded
        logger.error("❌ Max retries reached for msg_id=%d", message.id)
        await self.db.record_migration(
            source_message_id=message.id,
            destination_message_id=None,
            media_type=get_media_type(message),
            album_group_id=message.grouped_id,
            status="error",
            checksum=compute_checksum(message),
        )
        self.progress.record_error()
        return False

    async def _forward_batch(self, batch_messages: list[Message]) -> bool:
        """Forward a batch of messages to the destination channel in smaller spaced sub-batches.

        Args:
            batch_messages: List of source messages to forward.

        Returns:
            True if all sub-batches were processed.
        """
        if not batch_messages:
            return True

        sub_batch_size = max(1, self.config.sub_batch_size)
        logger.info(
            "Processing batch of %d messages in sub-batches of %d...",
            len(batch_messages),
            sub_batch_size,
        )

        if self.config.dry_run:
            for msg in batch_messages:
                logger.info(
                    "  [DRY RUN] Would forward: msg_id=%d, type=%s, caption=%r",
                    msg.id,
                    get_media_type(msg),
                    (msg.text or "")[:80],
                )
                self.progress.record_migrated()
                self._files_sent_this_run += 1
            
            # Send dry-run update to Telegram log channel
            if self.tg_logger:
                try:
                    await self.tg_logger.send_batch_report(
                        batch_messages=batch_messages,
                        next_forward_delay=self.config.batch_delay,
                    )
                except Exception as e:
                    logger.warning("Failed to send batch report to log channel: %s", e)
            return True

        # Split batch into sub-batches
        sub_batches = [
            batch_messages[i : i + sub_batch_size]
            for i in range(0, len(batch_messages), sub_batch_size)
        ]

        for idx, sub_batch in enumerate(sub_batches):
            sub_batch_ids = [msg.id for msg in sub_batch]
            logger.info(
                "Forwarding sub-batch %d/%d (%d messages)...",
                idx + 1,
                len(sub_batches),
                len(sub_batch),
            )

            # Try to send this sub-batch, retrying on FloodWait
            success = False
            while not success:
                # Wait for rate limits
                await self.rate_limiter.wait_if_needed()

                try:
                    results = await self.client.forward_messages(
                        entity=self._dest_entity,
                        messages=sub_batch_ids,
                        from_peer=self._source_entity,
                        drop_author=True,
                    )

                    if not isinstance(results, list):
                        if hasattr(results, "__iter__") and not isinstance(results, (str, bytes)):
                            results = list(results)
                        else:
                            results = [results]

                    # Pair messages with results
                    for i, msg in enumerate(sub_batch):
                        result = results[i] if i < len(results) else None
                        dest_id = result.id if result else None
                        status = "success" if result else "error"

                        await self.db.record_migration(
                            source_message_id=msg.id,
                            destination_message_id=dest_id,
                            media_type=get_media_type(msg),
                            album_group_id=msg.grouped_id,
                            status=status,
                            checksum=compute_checksum(msg),
                        )
                        if not self.config.dry_run:
                            await self.db.set_state("last_scanned_id", str(msg.id))
                        if status == "success":
                            if dest_id:
                                await self._edit_caption_if_needed(msg, dest_id)

                            self.progress.record_migrated()
                            self._files_sent_this_run += 1
                            file_name = ""
                            if msg.document and msg.document.attributes:
                                from telethon.tl.types import DocumentAttributeFilename
                                for attr in msg.document.attributes:
                                    if isinstance(attr, DocumentAttributeFilename):
                                        file_name = attr.file_name
                                        break
                            desc = file_name or (msg.text or "").replace("\n", " ")[:40] or "Document"
                            logger.info("  ✅ Forwarded: msg_id=%d -> dest_id=%s (%s)", msg.id, dest_id, desc)
                        else:
                            self.progress.record_error()
                            logger.error("  ❌ Forward failed: msg_id=%d", msg.id)

                    self._consecutive_errors = 0

                    # Record sends in rate limiter
                    for _ in sub_batch:
                        self.rate_limiter.record_send()

                    # Short random delay between sub-batches
                    sub_batch_delay = random.uniform(2.0, 5.0)
                    logger.info("⏳ Sub-batch complete. Spacing wait: %.1f seconds...", sub_batch_delay)
                    await asyncio.sleep(sub_batch_delay)
                    success = True

                except FloodWaitError as e:
                    self.progress.record_flood_wait()
                    if self.tg_logger:
                        await self.tg_logger.send_flood_alert(e.seconds)
                    await self.rate_limiter.handle_flood_wait(e.seconds)

                except Exception as e:
                    self._consecutive_errors += 1
                    logger.error(
                        "Error forwarding sub-batch of %d: %s: %s",
                        len(sub_batch),
                        type(e).__name__,
                        e,
                    )

                    if self._consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        logger.critical(
                            "🔥 %d consecutive errors — aborting migration for safety.",
                            MAX_CONSECUTIVE_ERRORS,
                        )
                        raise RuntimeError(
                            f"Too many consecutive errors ({MAX_CONSECUTIVE_ERRORS})"
                        )

                    # Fall back to forwarding individually
                    logger.warning("Falling back to forwarding individually...")
                    await self._forward_individually(sub_batch)
                    success = True  # Marked as done since fallback handled it

        # Send update to Telegram log channel after the ENTIRE batch is completed
        if self.tg_logger:
            try:
                await self.tg_logger.send_batch_report(
                    batch_messages=batch_messages,
                    next_forward_delay=self.config.batch_delay,
                )
            except Exception as e:
                logger.warning("Failed to send batch report to log channel: %s", e)

        # Batch cooldown delay at the end of the full batch execution, unless we have reached the session limit
        if not self.config.dry_run:
            if self.config.session_limit > 0 and self._files_sent_this_run >= self.config.session_limit:
                if self.config.session_limit_cooldown_minutes > 0:
                    logger.info("⏹️ Session limit reached. Skipping batch cooldown for session limit cooldown.")
                else:
                    logger.info("⏹️ Session limit reached. Skipping batch cooldown as the bot is stopping.")
            else:
                logger.info("⏳ Batch complete. Entering batch cooldown of %.1f seconds...", self.config.batch_delay)
                await asyncio.sleep(self.config.batch_delay)

        return True

    async def _forward_individually(self, batch_messages: list[Message]) -> bool:
        """Fallback: forward messages one by one."""
        success = True
        for msg in batch_messages:
            res = await self._forward_single_message(msg)
            if not res:
                success = False
        return success

    async def _download_and_upload_message(self, message: Message) -> Message | None:
        """Download the media from a restricted message and upload it to the destination.

        Returns:
            The sent message object if successful, None otherwise.
        """
        cleaned_text, cleaned_entities = clean_entities(
            message.text, list(message.entities) if message.entities else None
        )

        logger.info("Downloading media for message %d...", message.id)
        import os
        os.makedirs("downloads", exist_ok=True)
        
        # Download media to downloads directory
        local_path = await self.client.download_media(message, file="downloads/")
        if not local_path:
            logger.error("Failed to download media for message %d", message.id)
            return None

        logger.info("Uploading downloaded media for message %d: %s", message.id, local_path)
        try:
            # Upload standard post with cleaned captions and entities
            result = await self.client.send_file(
                entity=self._dest_entity,
                file=local_path,
                caption=cleaned_text,
                formatting_entities=cleaned_entities,
                parse_mode=None,
            )
            return result
        except Exception as e:
            logger.error("Failed to upload downloaded media for message %d: %s", message.id, e)
            return None
        finally:
            # Clean up the downloaded file to prevent disk fill-up
            try:
                if os.path.exists(local_path):
                    os.remove(local_path)
            except Exception as e:
                logger.warning("Failed to delete temp file %s: %s", local_path, e)

    async def _edit_caption_if_needed(self, source_msg: Message, dest_id: int) -> None:
        """Edit the caption of a forwarded destination message if the cleaned version differs from original."""
        if not source_msg.text:
            return

        cleaned_text, cleaned_entities = clean_entities(
            source_msg.text, list(source_msg.entities) if source_msg.entities else None
        )

        if cleaned_text != source_msg.text:
            logger.info("  Cleaning caption for forwarded msg_id=%d -> dest_id=%s...", source_msg.id, dest_id)
            try:
                await self.client.edit_message(
                    entity=self._dest_entity,
                    message=dest_id,
                    text=cleaned_text or "",
                    formatting_entities=cleaned_entities,
                    parse_mode=None,
                )
            except Exception as e:
                logger.warning("  Failed to edit caption for dest_id=%s: %s", dest_id, e)


    async def run_historical_migration(self, from_beginning: bool = False) -> None:
        """Execute the full historical migration.

        Iterates the source channel from oldest to newest message,
        skipping text-only messages, and forwarding media in batches of 100
        to the destination channel with a 1-minute delay between batches.
        """
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("  🚀 STARTING HISTORICAL MIGRATION (BATCH MODE)")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        if self.config.dry_run:
            logger.info("  ⚠️  DRY RUN MODE — no messages will be sent")

        # Estimate total messages
        self.progress.estimated_total = await self._get_total_messages()
        logger.info(
            "Estimated total messages in source: ~%d",
            self.progress.estimated_total,
        )

        # Check for resume point from state table, falling back to last migrated ID
        last_scanned_str = await self.db.get_state("last_scanned_id")
        last_id = None
        if last_scanned_str:
            try:
                last_id = int(last_scanned_str)
            except ValueError:
                pass
        if last_id is None:
            last_id = await self.db.get_last_migrated_id()

        min_id = 0
        already_done = 0
        if last_id and not from_beginning:
            min_id = last_id
            already_done = await self.db.get_migration_count("success")
            self.progress.total_migrated = already_done
            self.rate_limiter.total_sent = already_done
            logger.info(
                "📂 Resuming from message ID %d (%d already migrated)",
                last_id,
                already_done,
            )
        elif from_beginning:
            already_done = await self.db.get_migration_count("success")
            self.progress.total_migrated = already_done
            self.rate_limiter.total_sent = already_done
            logger.info("📂 Scanning from the beginning to fill missing gaps (%d already successfully migrated)", already_done)
        else:
            logger.info("📂 Starting fresh migration from the beginning")

        # Send startup notification to log channel
        if self.tg_logger:
            source_name = getattr(self._source_entity, "title", str(self.config.source_channel))
            dest_name = getattr(self._dest_entity, "title", str(self.config.destination_channel))
            await self.tg_logger.send_startup(
                source_name=source_name,
                dest_name=dest_name,
                resume_from=last_id,
                already_migrated=already_done,
                estimated_total=self.progress.estimated_total,
                dry_run=self.config.dry_run,
            )
            # Start hourly reports
            await self.tg_logger.start_hourly_reports(self.progress)

        _pending_batch: list[Message] = []

        logger.info("Scanning source channel messages (oldest → newest)...")

        try:
            async for message in self.client.iter_messages(
                self._source_entity,
                min_id=min_id,
                reverse=True,  # oldest → newest
                wait_time=0.2,  # Wait between Telegram API pages to reduce load
            ):
                # Check session limit before scanning/processing
                if self.config.session_limit > 0 and self._files_sent_this_run >= self.config.session_limit:
                    if self.config.session_limit_cooldown_minutes > 0:
                        await self._handle_session_limit_cooldown()
                    else:
                        logger.info("🛑 Session limit of %d files reached. Stopping migration.", self.config.session_limit)
                        break

                self.progress.record_scan(message.id)

                # Skip service messages
                if isinstance(message, MessageService):
                    if not self.config.dry_run:
                        await self.db.record_skip(message.id, reason="service_message")
                        await self.db.set_state("last_scanned_id", str(message.id))
                    continue

                # Skip messages without document media (only document files are migrated)
                if not message.document:
                    self.progress.record_skipped_text()
                    if not self.config.dry_run:
                        await self.db.record_skip(message.id, reason="not_a_document")
                        await self.db.set_state("last_scanned_id", str(message.id))
                    continue

                # Get filename and check filter rules
                from telethon.tl.types import DocumentAttributeFilename
                file_name = None
                if message.document.attributes:
                    for attr in message.document.attributes:
                        if isinstance(attr, DocumentAttributeFilename):
                            file_name = attr.file_name
                            break

                if not file_name or not should_migrate_file(file_name):
                    self.progress.record_skipped_duplicate()
                    if not self.config.dry_run:
                        await self.db.record_skip(message.id, reason="filtered_out")
                        await self.db.set_state("last_scanned_id", str(message.id))
                    continue

                # Check if already migrated
                if await self.db.is_migrated(message.id):
                    self.progress.record_skipped_duplicate()
                    if not self.config.dry_run:
                        await self.db.set_state("last_scanned_id", str(message.id))
                    continue

                # Check checksum-based dedup
                checksum = compute_checksum(message)
                if await self.db.is_checksum_exists(checksum):
                    self.progress.record_skipped_duplicate()
                    if not self.config.dry_run:
                        await self.db.record_skip(message.id, reason="duplicate_checksum")
                        await self.db.set_state("last_scanned_id", str(message.id))
                    continue

                self.progress.record_media_found()

                if self.config.batch_mode:
                    # Flush batch if it reaches batch_size messages, avoiding splitting albums
                    if len(_pending_batch) >= self.config.batch_size:
                        last_msg = _pending_batch[-1]
                        if not message.grouped_id or message.grouped_id != last_msg.grouped_id:
                            # Trim batch to not exceed session limit
                            if self.config.session_limit > 0:
                                space_left = self.config.session_limit - self._files_sent_this_run
                                if space_left <= 0:
                                    if self.config.session_limit_cooldown_minutes > 0:
                                        await self._handle_session_limit_cooldown()
                                        space_left = self.config.session_limit - self._files_sent_this_run
                                    else:
                                        break
                                if len(_pending_batch) > space_left:
                                    _pending_batch = _pending_batch[:space_left]

                            await self._forward_batch(_pending_batch)
                            _pending_batch.clear()

                            # Re-check session limit
                            if self.config.session_limit > 0 and self._files_sent_this_run >= self.config.session_limit:
                                if self.config.session_limit_cooldown_minutes > 0:
                                    await self._handle_session_limit_cooldown()
                                else:
                                    logger.info("🛑 Session limit of %d files reached. Stopping migration.", self.config.session_limit)
                                    break

                    _pending_batch.append(message)
                else:
                    # Forward message immediately one-by-one
                    await self._forward_single_message(message)
                    
                    # Re-check session limit
                    if self.config.session_limit > 0 and self._files_sent_this_run >= self.config.session_limit:
                        if self.config.session_limit_cooldown_minutes > 0:
                            await self._handle_session_limit_cooldown()
                        else:
                            logger.info("🛑 Session limit of %d files reached. Stopping migration.", self.config.session_limit)
                            break

                self.progress.display()

            # Flush any remaining messages if in batch_mode
            if self.config.batch_mode and _pending_batch:
                if self.config.session_limit > 0:
                    space_left = self.config.session_limit - self._files_sent_this_run
                    if space_left > 0:
                        if len(_pending_batch) > space_left:
                            _pending_batch = _pending_batch[:space_left]
                        await self._forward_batch(_pending_batch)
                else:
                    await self._forward_batch(_pending_batch)
                _pending_batch.clear()

        finally:
            self.progress.display(force=True)
            self.progress.summary()

            # Send shutdown report to log channel
            if self.tg_logger:
                await self.tg_logger.stop()
                await self.tg_logger.send_shutdown(
                    self.progress, reason="Historical migration complete or interrupted"
                )

        logger.info("Historical migration phase complete.")

    async def run_live_monitor(self) -> None:
        """Monitor the source channel for new uploads and copy them in real-time."""
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info("  👁️  LIVE MONITORING MODE ACTIVE")
        logger.info("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        logger.info(
            "Watching source channel for new media uploads... (Ctrl+C to stop)"
        )

        source_id = getattr(self._source_entity, "id", None)
        if source_id is None:
            logger.error("Cannot determine source channel ID for live monitoring")
            return

        @self.client.on(events.NewMessage(chats=source_id))
        async def on_new_message(event: events.NewMessage.Event) -> None:
            message = event.message

            if not has_media(message):
                logger.debug("Live: skipping text-only msg_id=%d", message.id)
                return

            if isinstance(message.media, MessageMediaWebPage):
                logger.debug("Live: skipping webpage preview msg_id=%d", message.id)
                return

            if await self.db.is_migrated(message.id):
                logger.debug("Live: already migrated msg_id=%d", message.id)
                return

            checksum = compute_checksum(message)
            if await self.db.is_checksum_exists(checksum):
                logger.info("Live: skipping duplicate checksum msg_id=%d", message.id)
                await self.db.record_skip(message.id, reason="duplicate_checksum")
                return

            logger.info(
                "📥 Live: new media detected — msg_id=%d, type=%s",
                message.id,
                get_media_type(message),
            )

            # Wait for rate limits
            await self.rate_limiter.wait_if_needed()
            try:
                dest_id = None
                try:
                    results = await self.client.forward_messages(
                        entity=self._dest_entity,
                        messages=message.id,
                        from_peer=self._source_entity,
                        drop_author=True,
                    )
                    if results:
                        if isinstance(results, list):
                            dest_id = results[0].id if results[0] else None
                        else:
                            dest_id = results.id
                except ChatForwardsRestrictedError:
                    logger.warning("🔒 Live: Chat forwards restricted for msg_id=%d. Falling back to download and upload...", message.id)
                    sent_msg = await self._download_and_upload_message(message)
                    if sent_msg:
                        dest_id = sent_msg.id

                status = "success" if dest_id else "error"
                await self.db.record_migration(
                    source_message_id=message.id,
                    destination_message_id=dest_id,
                    media_type=get_media_type(message),
                    album_group_id=message.grouped_id,
                    status=status,
                    checksum=checksum,
                )
                if status == "success":
                    if dest_id:
                        await self._edit_caption_if_needed(message, dest_id)

                    self.rate_limiter.record_send()
                    logger.info("Live: forwarded msg_id=%d -> dest_id=%s", message.id, dest_id)
                    if self.tg_logger:
                        try:
                            # In live mode there is no "next forward delay" so we set it to 0
                            await self.tg_logger.send_forward_log(
                                message=message,
                                next_forward_delay=0.0,
                                dest_id=dest_id,
                                dry_run=False,
                            )
                        except Exception:
                            pass
            except Exception as e:
                logger.error("Live: forward failed for msg_id=%d: %s", message.id, e)

        logger.info("Live monitor registered. Waiting for new messages...")
        try:
            await self.client.run_until_disconnected()
        except KeyboardInterrupt:
            logger.info("Live monitor stopped by user.")

    async def retry_failed(self) -> None:
        """Retry all previously failed migrations."""
        failed_ids = await self.db.get_failed_ids()
        if not failed_ids:
            logger.info("No failed migrations to retry.")
            return

        logger.info("🔄 Retrying %d failed migrations...", len(failed_ids))

        for msg_id in failed_ids:
            try:
                message = await self.client.get_messages(
                    self._source_entity, ids=msg_id
                )
                if message and has_media(message):
                    await self.rate_limiter.wait_if_needed()
                    dest_id = None
                    try:
                        result = await self.client.forward_messages(
                            entity=self._dest_entity,
                            messages=message.id,
                            from_peer=self._source_entity,
                            drop_author=True,
                        )
                        dest_id = result[0].id if (result and isinstance(result, list)) else (result.id if result else None)
                    except ChatForwardsRestrictedError:
                        logger.warning("🔒 Retry: Chat forwards restricted for msg_id=%d. Falling back to download and upload...", message.id)
                        sent_msg = await self._download_and_upload_message(message)
                        if sent_msg:
                            dest_id = sent_msg.id

                    if dest_id:
                        await self._edit_caption_if_needed(message, dest_id)
                        await self.db.record_migration(
                            source_message_id=message.id,
                            destination_message_id=dest_id,
                            media_type=get_media_type(message),
                            status="success",
                            checksum=compute_checksum(message),
                        )
                        self.rate_limiter.record_send()
                    else:
                        logger.error("Retry failed for msg_id=%d", msg_id)
                    await self.rate_limiter.apply_delay()
                else:
                    logger.warning(
                        "Could not re-fetch failed msg_id=%d — may be deleted",
                        msg_id,
                    )
            except FloodWaitError as e:
                self.progress.record_flood_wait()
                await self.rate_limiter.handle_flood_wait(e.seconds)
            except Exception as e:
                logger.error("Retry failed for msg_id=%d: %s", msg_id, e)

