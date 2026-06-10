"""Album (media group) detection and reconstruction for Telegram Channel Migrator.

Telegram albums are groups of media messages sharing the same `grouped_id`.
This module:
  - Detects album boundaries during message iteration
  - Collects all messages belonging to an album
  - Reconstructs albums in the destination channel preserving:
    * Original order
    * Captions (only on first or last item, as Telegram allows)
    * Media count
  - Falls back to individual sends if album reconstruction fails
"""

import asyncio
import hashlib
import logging
import random
from telethon import TelegramClient
from telethon.tl.types import (
    Message,
    InputMediaPhoto,
    InputMediaDocument,
    InputMediaUploadedPhoto,
    InputMediaUploadedDocument,
    MessageMediaPhoto,
    MessageMediaDocument,
)
from telethon.errors import FloodWaitError, MediaEmptyError

from caption_processor import clean_entities
from database import MigrationDatabase

logger = logging.getLogger("migrator.album")


def get_media_type(message: Message) -> str:
    """Determine the media type string for a message."""
    if message.photo:
        return "photo"
    elif message.video:
        return "video"
    elif message.document:
        mime = getattr(message.document, "mime_type", "") or ""
        if "audio" in mime:
            return "audio"
        if "video" in mime:
            return "video"
        return "document"
    elif message.audio:
        return "audio"
    elif message.voice:
        return "voice"
    elif message.sticker:
        return "sticker"
    elif message.gif:
        return "gif"
    elif message.contact:
        return "contact"
    elif message.geo:
        return "geo"
    return "unknown"


def compute_checksum(message: Message) -> str:
    """Compute a dedup checksum for a message based on media ID and caption."""
    parts = []
    if message.photo:
        parts.append(f"photo:{message.photo.id}")
    elif message.document:
        parts.append(f"doc:{message.document.id}")
    if message.text:
        parts.append(message.text[:200])
    raw = "|".join(parts)
    return hashlib.md5(raw.encode()).hexdigest()


def has_media(message: Message) -> bool:
    """Check if a message contains any media to migrate."""
    if not message.media:
        return False

    # Skip web page previews
    from telethon.tl.types import MessageMediaWebPage
    if isinstance(message.media, MessageMediaWebPage):
        return False

    # Accept photos, videos, documents, audios, gifs, voice messages
    if (message.photo or 
        message.video or 
        message.document or 
        message.audio or 
        message.gif or 
        message.voice):
        return True

    return False


class AlbumCollector:
    """Collects messages that belong to the same album (media group).

    Usage:
        collector = AlbumCollector()
        for message in messages:
            album = collector.add(message)
            if album is not None:
                # Process the completed album
                ...
        # Don't forget the last pending album:
        album = collector.flush()
        if album:
            # Process it
    """

    def __init__(self) -> None:
        self._current_group_id: int | None = None
        self._buffer: list[Message] = []

    def add(self, message: Message) -> list[Message] | None:
        """Add a message. Returns a completed album if group boundary detected.

        Returns:
            List of messages forming a complete album, or None.
        """
        group_id = message.grouped_id

        if group_id is None:
            # Not part of any album — flush any pending album
            completed = self.flush()
            return completed

        if group_id == self._current_group_id:
            # Same album, keep collecting
            self._buffer.append(message)
            return None

        # New album started — flush previous
        completed = self.flush()
        self._current_group_id = group_id
        self._buffer.append(message)
        return completed

    def flush(self) -> list[Message] | None:
        """Flush any pending album.

        Returns:
            List of messages forming the album, or None if buffer is empty.
        """
        if not self._buffer:
            return None
        album = self._buffer.copy()
        self._buffer.clear()
        self._current_group_id = None
        return album

    @property
    def has_pending(self) -> bool:
        return len(self._buffer) > 0

    @property
    def pending_group_id(self) -> int | None:
        return self._current_group_id


async def send_album(
    client: TelegramClient,
    destination: int | str,
    album_messages: list[Message],
    db: MigrationDatabase,
    dry_run: bool = False,
) -> bool:
    """Reconstruct and send an album to the destination channel.

    Attempts to send as a grouped media. If that fails, falls back to
    sending each item individually.

    Args:
        client: Authenticated TelegramClient.
        destination: Destination channel entity.
        album_messages: List of messages forming the album.
        db: Database for recording migrations.
        dry_run: If True, only log without sending.

    Returns:
        True if all items were sent successfully.
    """
    if not album_messages:
        return True

    group_id = album_messages[0].grouped_id
    logger.info(
        "📦 Processing album (group_id=%s): %d items",
        group_id,
        len(album_messages),
    )

    if dry_run:
        for msg in album_messages:
            logger.info(
                "  [DRY RUN] Would send album item: msg_id=%d, type=%s",
                msg.id,
                get_media_type(msg),
            )
            await db.record_migration(
                source_message_id=msg.id,
                destination_message_id=None,
                media_type=get_media_type(msg),
                album_group_id=group_id,
                status="dry_run",
                checksum=compute_checksum(msg),
            )
        return True

    try:
        # Build the file list for send_file with album grouping
        files = []
        captions = []

        for i, msg in enumerate(album_messages):
            # Get the media input for re-sending
            if msg.photo:
                files.append(msg.photo)
            elif msg.document:
                files.append(msg.document)
            else:
                files.append(msg.media)

            # Only the first message in an album typically carries the caption
            if i == 0 and msg.text:
                cleaned_text, cleaned_entities = clean_entities(
                    msg.text, list(msg.entities) if msg.entities else None
                )
                captions.append(cleaned_text or "")
            else:
                caption_text = msg.text or ""
                if caption_text:
                    cleaned_text, _ = clean_entities(caption_text, None)
                    captions.append(cleaned_text or "")
                else:
                    captions.append("")

        # Send as album
        results = await client.send_file(
            destination,
            file=files,
            caption=captions,
            parse_mode=None,  # We handle entities manually
        )

        # Record each album item
        if not isinstance(results, list):
            results = [results]

        for msg, result in zip(album_messages, results):
            dest_id = result.id if result else None
            await db.record_migration(
                source_message_id=msg.id,
                destination_message_id=dest_id,
                media_type=get_media_type(msg),
                album_group_id=group_id,
                status="success",
                checksum=compute_checksum(msg),
            )

        logger.info(
            "✅ Album sent successfully (group_id=%s, %d items)",
            group_id,
            len(album_messages),
        )
        return True

    except (MediaEmptyError, ValueError, TypeError) as e:
        logger.warning(
            "Album send failed (%s), falling back to individual sends: %s",
            type(e).__name__,
            e,
        )
        return await _send_album_individually(
            client, destination, album_messages, db
        )


async def _send_album_individually(
    client: TelegramClient,
    destination: int | str,
    album_messages: list[Message],
    db: MigrationDatabase,
) -> bool:
    """Fallback: send each album item as an individual message."""
    success = True
    for msg in album_messages:
        try:
            cleaned_text, cleaned_entities = clean_entities(
                msg.text, list(msg.entities) if msg.entities else None
            )

            if msg.photo:
                media = msg.photo
            elif msg.document:
                media = msg.document
            else:
                media = msg.media

            result = await client.send_file(
                destination,
                file=media,
                caption=cleaned_text,
                parse_mode=None,
            )

            await db.record_migration(
                source_message_id=msg.id,
                destination_message_id=result.id if result else None,
                media_type=get_media_type(msg),
                album_group_id=msg.grouped_id,
                status="success",
                checksum=compute_checksum(msg),
            )
            # Small delay between individual sends within a broken album
            await asyncio.sleep(random.uniform(1.5, 3.0))

        except Exception as e:
            logger.error(
                "Failed to send album item msg_id=%d individually: %s",
                msg.id,
                e,
            )
            await db.record_migration(
                source_message_id=msg.id,
                destination_message_id=None,
                media_type=get_media_type(msg),
                album_group_id=msg.grouped_id,
                status="error",
                checksum=compute_checksum(msg),
            )
            success = False

    return success
