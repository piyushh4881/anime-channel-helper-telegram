"""Channel scanner for the Movie Indexer Bot.

Iterates Telegram channel history, detects .mkv document messages,
extracts movie metadata, looks up studios via AniList, edits captions,
and stores everything in the database.

Designed for 50k+ message channels with:
- Resume support via scan_state checkpoints
- Batch database writes
- Rate limiting for both Telegram and AniList APIs
- Progress callbacks for real-time status updates
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Awaitable

from telethon import TelegramClient
from telethon.tl.types import (
    DocumentAttributeFilename,
    MessageMediaDocument,
)
from telethon.errors import (
    FloodWaitError,
    MessageNotModifiedError,
    ChatAdminRequiredError,
    ChannelPrivateError,
)
from telethon.tl.functions.channels import GetFullChannelRequest

from anilist import AniListClient
from config import Config
from database import MovieDatabase
from filename_cleaner import extract_movie_info, clean_filename, strip_brackets_from_title, format_combined_title
from models import ScanState

logger = logging.getLogger(__name__)

# Type alias for the progress callback
ProgressCallback = Optional[Callable[[int, int, str], Awaitable[None]]]

BLACKLISTED_PRODUCERS = {
    "aniplex", "bandai visual", "kodansha", "manga entertainment", "horipro",
    "half h.p studio", "fuji tv", "toho", "kadokawa", "pony canyon", "shueisha",
    "lantis", "warner bros", "nbcuniversal", "king records", "dentsu",
    "mainichi broadcasting", "tv tokyo", "asatsu dk", "sotsu", "at-x", "nhk",
    "tbs", "shogakukan", "shochiku", "tatsunoko", "genco", "kodokawa shoten"
}


class ChannelScanner:
    """Scans a Telegram channel for .mkv document messages."""

    def __init__(
        self,
        client: TelegramClient,
        db: MovieDatabase,
        anilist: AniListClient,
        config: Config,
    ) -> None:
        self.client = client
        self.db = db
        self.anilist = anilist
        self.config = config
        self._cancelled = False

    def cancel(self) -> None:
        """Signal the scanner to stop after the current batch."""
        self._cancelled = True

    async def _find_metadata_message(self, message) -> Optional[tuple[str, list[str]]]:
        """Look back in the channel history to find the metadata message for a document.

        Returns
        -------
        tuple[str, list[str]] or None
            (clean_title, studios_list) if found, else None
        """
        channel = self.config.channel_id
        # Look back up to 10 messages from message.id - 1
        lookback_ids = list(range(max(1, message.id - 10), message.id))
        lookback_ids.reverse()  # check closest first

        logger.info(f"Looking back for metadata of msg {message.id} (lookback IDs: {lookback_ids})...")
        try:
            prev_msgs = await self.client.get_messages(channel, ids=lookback_ids)
            for prev_msg in prev_msgs:
                if not prev_msg:
                    logger.info(f"  Msg {prev_msg} is None")
                    continue
                logger.info(f"  Checking msg {prev_msg.id}: HasText={prev_msg.text is not None}")
                if not prev_msg.text:
                    continue

                text = prev_msg.text.strip()
                logger.info(f"    Snippet: {text[:80]}")
                has_studios = "Studios:" in text or "Type:" in text or "**Studios**:" in text
                logger.info(f"    HasStudiosOrType: {has_studios}")
                if has_studios:
                    # Extract title: bold text at start
                    import re
                    title_match = re.match(r"^\*\*([^\*]+)\*\*", text)
                    logger.info(f"    TitleMatch: {title_match}")
                    if not title_match:
                        continue

                    title = title_match.group(1).strip()
                    title = strip_brackets_from_title(title)
                    logger.info(f"    Extracted Title: '{title}'")

                    # Extract studios
                    studios = []
                    studios_match = re.search(r"(?:\*\*Studios\*\*|Studios):\s*`?([^`\n\r]+)`?", text, re.IGNORECASE)
                    logger.info(f"    StudiosMatch: {studios_match}")
                    if studios_match:
                        studios_str = studios_match.group(1)
                        studios = [s.strip() for s in studios_str.split(",") if s.strip()]
                        logger.info(f"    Extracted Studios: {studios}")

                    if title:
                        logger.info(f"  Found metadata! Title: '{title}', Studios: {studios}")
                        return title, studios
        except Exception as exc:
            logger.warning(f"Failed to find metadata message for msg {message.id}: {exc}", exc_info=True)

        logger.info(f"  No metadata found for msg {message.id}")
        return None

    # ── Public scanning methods ──────────────────────────────────────

    async def scan_full(
        self,
        progress_cb: ProgressCallback = None,
    ) -> int:
        """Scan the entire channel history from oldest to newest.

        Parameters
        ----------
        progress_cb : callable, optional
            ``async def cb(processed: int, found: int, status: str)``

        Returns
        -------
        int
            Total number of .mkv files indexed.
        """
        self._cancelled = False
        logger.info("Starting full channel scan")

        # Clear previous data for a clean re-scan
        await self.db.clear_all()

        return await self._scan(
            min_id=0,
            progress_cb=progress_cb,
        )

    async def scan_incremental(
        self,
        progress_cb: ProgressCallback = None,
    ) -> int:
        """Scan only new messages since the last scan checkpoint.

        Returns
        -------
        int
            Number of new .mkv files indexed.
        """
        self._cancelled = False
        state = await self.db.get_scan_state()
        min_id = state.last_message_id if state else 0

        if min_id:
            logger.info(f"Incremental scan from message ID {min_id}")
        else:
            logger.info("No previous scan state, running full scan")

        return await self._scan(
            min_id=min_id,
            progress_cb=progress_cb,
        )

    # ── Core scanning loop ───────────────────────────────────────────

    async def _scan(
        self,
        min_id: int = 0,
        progress_cb: ProgressCallback = None,
    ) -> int:
        """Core scanner that iterates channel messages.

        Processes messages chronologically (oldest first) using
        reverse=True, so captions are edited from the beginning
        of the channel history.
        """
        channel = self.config.channel_id
        channel_id_short = self.config.channel_id_short
        found = 0
        processed = 0
        batch_pending = 0
        max_message_id = min_id

        try:
            # Verify channel access
            entity = await self.client.get_entity(channel)
            logger.info(f"Scanning channel: {getattr(entity, 'title', channel)}")
        except (ChannelPrivateError, ValueError) as exc:
            logger.error(f"Cannot access channel {channel}: {exc}")
            raise

        try:
            full_channel = await self.client(GetFullChannelRequest(entity))
            max_id_in_channel = getattr(full_channel.full_chat, 'read_inbox_max_id', 0)
            logger.info(f"Max message ID in channel: {max_id_in_channel}")
        except Exception as exc:
            logger.error(f"Cannot get full channel info: {exc}")
            raise

        current_id = min_id + 1
        batch_size = 100

        try:
            while current_id <= max_id_in_channel:
                if self._cancelled:
                    logger.info("Scan cancelled by user")
                    break

                end_id = min(current_id + batch_size - 1, max_id_in_channel)
                ids_to_fetch = list(range(current_id, end_id + 1))

                try:
                    messages = await self.client.get_messages(entity, ids=ids_to_fetch)
                except FloodWaitError as exc:
                    logger.warning(f"FloodWait: sleeping {exc.seconds}s")
                    await asyncio.sleep(exc.seconds)
                    continue
                except Exception as exc:
                    logger.error(f"Error fetching batch {current_id} to {end_id}: {exc}")
                    current_id += batch_size
                    continue

                for message in messages:
                    if self._cancelled:
                        break

                    if not message:
                        continue

                    processed += 1

                    # Track highest message ID for resume
                    if message.id > max_message_id:
                        max_message_id = message.id

                    # Process only .mkv document messages
                    result = self._extract_mkv_info(message)
                    if result is None:
                        # Progress update every 200 messages
                        if processed % 200 == 0 and progress_cb:
                            await progress_cb(
                                processed, found,
                                f"Scanning... {processed} messages checked, {found} movies found"
                            )
                        continue

                    filename, title_from_file, year, quality = result

                    # Try to locate preceding metadata message
                    clean_title = None
                    studios_list = []
                    meta = await self._find_metadata_message(message)
                    if meta:
                        clean_title, studios_list = meta

                    # Clean the title (remove brackets etc.)
                    if clean_title:
                        title = clean_title
                    else:
                        title = title_from_file

                    # Look up studio name and year
                    studio = "Unknown Studio"
                    try:
                        info = await self.anilist.search_anime_info(title)
                        if info:
                            romaji = info.get("romaji") or title
                            english = info.get("english")
                            title = format_combined_title(romaji, english)
                            
                            studio = info.get("studio") or "Unknown Studio"
                            
                            al_year = info.get("year")
                            if not year and al_year:
                                year = al_year
                    except Exception as exc:
                        logger.warning(f"AniList query failed for {title}: {exc}")

                    # If AniList failed to find a valid studio, fall back to parsed studios list
                    if (not studio or studio == "Unknown Studio") and studios_list:
                        # Filter out non-animation companies
                        filtered_studios = [
                            s for s in studios_list 
                            if s.lower().strip() not in BLACKLISTED_PRODUCERS
                        ]
                        if filtered_studios:
                            studio = filtered_studios[0]
                        else:
                            studio = studios_list[0]

                    # Build Telegram deep link
                    telegram_link = f"https://t.me/c/{channel_id_short}/{message.id}"

                    # Database insert
                    movie_id = await self.db.upsert_movie(title, year, studio)
                    added = await self.db.add_release(
                        movie_id, quality, message.id, channel, telegram_link
                    )

                    if added:
                        found += 1
                        logger.debug(
                            f"Indexed: {title} ({year}) [{quality}] - {studio}"
                        )

                    # Edit the message caption with clean name + studio
                    await self._edit_caption(message, title, year, studio)

                    # Batch commit
                    batch_pending += 1
                    if batch_pending >= self.config.scan_batch_size:
                        await self.db.commit()
                        batch_pending = 0

                        # Save checkpoint
                        await self.db.set_scan_state(
                            ScanState(
                                last_message_id=max_message_id,
                                total_scanned=processed,
                                last_scan_time=datetime.now(timezone.utc).isoformat(),
                            )
                        )

                    # Progress update
                    if processed % 50 == 0 and progress_cb:
                        await progress_cb(
                            processed, found,
                            f"Scanning... {processed} messages, {found} movies indexed"
                        )

                    # Rate limit
                    await asyncio.sleep(self.config.rate_limit_delay)

                current_id += len(ids_to_fetch)

        except FloodWaitError as exc:
            logger.warning(f"FloodWait: sleeping {exc.seconds}s")
            await asyncio.sleep(exc.seconds)
            # Save progress before potentially retrying
            await self.db.commit()

        # Final commit and checkpoint
        await self.db.commit()
        await self.db.set_scan_state(
            ScanState(
                last_message_id=max_message_id,
                total_scanned=processed,
                last_scan_time=datetime.now(timezone.utc).isoformat(),
            )
        )

        logger.info(
            f"Scan complete: {processed} messages processed, {found} movies indexed"
        )

        if progress_cb:
            await progress_cb(
                processed, found,
                f"✅ Scan complete — {found} movies indexed from {processed} messages"
            )

        return found

    # ── Message processing helpers ───────────────────────────────────

    def _extract_mkv_info(
        self, message
    ) -> Optional[tuple[str, str, Optional[int], str]]:
        """Extract .mkv file info from a message, or None if not applicable.

        Returns
        -------
        tuple or None
            ``(original_filename, title, year, quality)`` if the message
            contains a .mkv document, else ``None``.
        """
        # Must be a document (not a Telegram-native video)
        if not message.document:
            return None

        if not isinstance(message.media, MessageMediaDocument):
            return None

        # Find the filename attribute
        filename = None
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break

        if not filename:
            return None

        # Must be .mkv
        if not filename.lower().endswith(".mkv"):
            return None

        # Extract movie info
        title, year, quality = extract_movie_info(filename)
        return filename, title, year, quality

    async def _edit_caption(
        self,
        message,
        title: str,
        year: Optional[int],
        studio: str,
    ) -> None:
        """Edit the message caption to show cleaned title + studio.

        New caption format:  ``Title (Year) — Studio Name``
        """
        clean_name = f"{title} ({year})" if year else title
        new_caption = f"**{clean_name} - {studio}**"

        # Skip if caption is already correct
        current = message.text or message.message or ""
        if current.strip() == new_caption.strip():
            return

        try:
            await self.client.edit_message(
                message.peer_id,
                message.id,
                new_caption,
                parse_mode="md",
            )
            logger.debug(f"Caption edited: {new_caption}")
        except MessageNotModifiedError:
            pass  # Caption already matches
        except ChatAdminRequiredError:
            logger.warning(
                "Bot needs admin rights with 'Edit Messages' permission"
            )
        except FloodWaitError as exc:
            logger.warning(f"Caption edit FloodWait: {exc.seconds}s")
            await asyncio.sleep(exc.seconds)
        except Exception as exc:
            logger.warning(f"Failed to edit caption for msg {message.id}: {exc}")
