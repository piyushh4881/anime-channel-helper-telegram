"""Command handlers for the Movie Indexer Bot.

Commands:
    /scan    -- full channel scan (oldest to newest)
    /rebuild -- rebuild index from database
    /update  -- incremental scan (new messages only)
    /search  -- search movies by title
    /stats   -- show database statistics
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from telethon import TelegramClient, events
from telethon.tl.types import (
    DocumentAttributeFilename,
    MessageMediaDocument,
)
from telethon.errors import FloodWaitError

from anilist import AniListClient
from config import Config
from database import MovieDatabase
from filename_cleaner import extract_movie_info, format_combined_title
from index_builder import IndexBuilder
from models import ScanState
from scanner import ChannelScanner

logger = logging.getLogger(__name__)

SEPARATOR = "------------------------------"


def register_commands(
    bot_client: TelegramClient,
    db: MovieDatabase,
    anilist: AniListClient,
    config: Config,
) -> None:
    """Register all bot commands and the live-upload listener.

    Parameters
    ----------
    bot_client : TelegramClient
        Bot-token client that receives user commands and performs channel operations.
    """
    # Scanner and index builder use the bot_client for channel access
    scanner = ChannelScanner(bot_client, db, anilist, config)
    index_builder = IndexBuilder(bot_client, db, config)

    # -- Helper: admin check -----------------------------------------------

    def is_admin(event) -> bool:
        """Check if the sender is in the admin list."""
        if event.is_channel:
            return True
        if not config.admin_users:
            return True
        return event.sender_id in config.admin_users

    # -- Helper: progress updater ------------------------------------------

    async def make_progress_cb(status_msg):
        """Create a progress callback that edits a status message."""
        last_text = [""]

        async def cb(processed: int, found: int, status: str) -> None:
            if status == last_text[0]:
                return
            last_text[0] = status
            try:
                await status_msg.edit(status)
            except Exception:
                pass

        return cb

    # -- /scan -- Full channel scan (oldest to newest) ---------------------

    @bot_client.on(events.NewMessage(pattern=r"^/scan$"))
    async def handle_scan(event: events.NewMessage.Event) -> None:
        """Scan the entire channel history and index all .mkv files."""
        if not is_admin(event):
            return

        logger.info(f"/scan triggered by user {event.sender_id}")

        status = await event.respond(
            "**Starting full channel scan...**\n"
            "Scanning from the oldest message to newest.\n"
            "This may take a while for large channels."
        )
        progress_cb = await make_progress_cb(status)

        try:
            found = await scanner.scan_full(progress_cb=progress_cb)

            await status.edit(
                f"**Scan complete!**\n"
                f"- {found} movies indexed\n\n"
                f"Building index..."
            )

            # Auto-build and post the index to PM
            pages = await index_builder.post_index(event.sender_id)

            await status.edit(
                f"**Scan complete!**\n"
                f"- {found} movies indexed\n"
                f"- {pages} index pages sent to your PM"
            )

        except Exception as exc:
            logger.error(f"/scan failed: {exc}", exc_info=True)
            await status.edit(f"**Scan failed:** {exc}")

    # -- /rebuild -- Rebuild index from database ---------------------------

    @bot_client.on(events.NewMessage(pattern=r"^/rebuild$"))
    async def handle_rebuild(event: events.NewMessage.Event) -> None:
        """Rebuild and re-post the index from existing database data."""
        if not is_admin(event):
            return

        logger.info(f"/rebuild triggered by user {event.sender_id}")
        status = await event.respond("**Rebuilding index from database...**")

        try:
            pages = await index_builder.post_index(event.sender_id)
            await status.edit(
                f"**Index rebuilt!**\n- {pages} pages sent to your PM"
            )
        except Exception as exc:
            logger.error(f"/rebuild failed: {exc}", exc_info=True)
            await status.edit(f"**Rebuild failed:** {exc}")

    # -- /update -- Incremental scan ---------------------------------------

    @bot_client.on(events.NewMessage(pattern=r"^/update$"))
    async def handle_update(event: events.NewMessage.Event) -> None:
        """Scan only new messages since the last scan."""
        if not is_admin(event):
            return

        logger.info(f"/update triggered by user {event.sender_id}")
        status = await event.respond("**Scanning for new messages...**")
        progress_cb = await make_progress_cb(status)

        try:
            found = await scanner.scan_incremental(progress_cb=progress_cb)

            if found > 0:
                await status.edit(
                    f"**Update complete!**\n"
                    f"- {found} new movies indexed\n\n"
                    f"Updating index..."
                )
                pages = await index_builder.post_index(event.sender_id)
                await status.edit(
                    f"**Update complete!**\n"
                    f"- {found} new movies indexed\n"
                    f"- {pages} index pages refreshed in your PM"
                )
            else:
                await status.edit("**No new movies found.** Index is up to date.")

        except Exception as exc:
            logger.error(f"/update failed: {exc}", exc_info=True)
            await status.edit(f"**Update failed:** {exc}")

    # -- /search <query> -- Search movies ----------------------------------

    @bot_client.on(events.NewMessage(pattern=r"^/search\s+(.+)$"))
    async def handle_search(event: events.NewMessage.Event) -> None:
        """Search for a movie by title."""
        if not is_admin(event):
            return

        query = event.pattern_match.group(1).strip()
        logger.info(f"/search '{query}' by user {event.sender_id}")

        try:
            results = await db.search_movies(query)

            if not results:
                await event.respond(
                    f"No results for **{query}**.\n"
                    f"Try a different spelling or run /scan first."
                )
                return

            # Format results
            lines: list[str] = [f"**Search results for:** {query}\n"]

            for movie, releases in results[:10]:
                lines.append(index_builder.format_search_result(movie, releases))
                lines.append("")

            response = "\n".join(lines)

            # Truncate if too long
            if len(response) > 4000:
                response = response[:3950] + "\n\n_...results truncated_"

            await event.respond(response, parse_mode="md", link_preview=False)

        except Exception as exc:
            logger.error(f"/search failed: {exc}", exc_info=True)
            await event.respond(f"**Search failed:** {exc}")

    # -- /stats -- Show statistics -----------------------------------------

    @bot_client.on(events.NewMessage(pattern=r"^/stats$"))
    async def handle_stats(event: events.NewMessage.Event) -> None:
        """Display database statistics."""
        if not is_admin(event):
            return

        logger.info(f"/stats triggered by user {event.sender_id}")

        try:
            stats = await db.get_stats()
            scan_state = await db.get_scan_state()

            msg = (
                "**Movie Indexer Stats**\n"
                f"{SEPARATOR}\n\n"
                f"Total Movies: {stats['total_movies']}\n"
                f"Total Releases: {stats['total_releases']}\n"
                f"Studios Found: {stats['total_studios']}\n"
                f"Indexed Messages: {stats['total_messages']}\n"
                f"Database Size: {stats['db_size']}\n"
                f"AniList Cache: {anilist.cache_size()} entries\n"
            )

            if scan_state:
                msg += (
                    f"\n**Last Scan:**\n"
                    f"- Messages scanned: {scan_state.total_scanned}\n"
                    f"- Last message ID: {scan_state.last_message_id}\n"
                    f"- Timestamp: {scan_state.last_scan_time}\n"
                )

            await event.respond(msg, parse_mode="md")

        except Exception as exc:
            logger.error(f"/stats failed: {exc}", exc_info=True)
            await event.respond(f"**Stats failed:** {exc}")

    # -- .ani / /ani <query> -- Fetch full anime metadata & poster card ------

    @bot_client.on(events.NewMessage(pattern=r"^[/\.]ani\s+(.+)$"))
    async def handle_ani(event: events.NewMessage.Event) -> None:
        """Fetch anime metadata from AniList and send a stylized card with poster."""
        if not is_admin(event):
            return

        query = event.pattern_match.group(1).strip()
        logger.info(f".ani triggered for query '{query}' by user {event.sender_id}")

        # Delete the trigger message
        try:
            await event.delete()
        except Exception as exc:
            logger.warning(f"Failed to delete trigger message: {exc}")

        try:
            # Fetch metadata
            media = await anilist.fetch_anime_metadata(query)
            if not media:
                await bot_client.send_message(
                    event.chat_id,
                    f"❌ Could not find anime info for **{query}** on AniList."
                )
                return

            # Extract titles
            title_bold = media['title']['english'] or media['title']['romaji']
            native_title = media['title']['native']
            header = f"**{title_bold}**(`{native_title}`)" if native_title else f"**{title_bold}**"

            # Extract fields
            m_type = media.get('type') or 'N/A'
            m_status = media.get('status') or 'N/A'
            episodes = media.get('episodes') or '1'
            duration = media.get('duration') or 'N/A'
            score = media.get('averageScore') or 'N/A'
            
            genres_list = media.get('genres') or []
            genres = ", ".join(genres_list) if genres_list else "N/A"

            # Extract studios (limit to first 4, e.g. Production I.G, Bandai Visual, etc.)
            studios_nodes = media.get('studios', {}).get('nodes', [])
            studios_names = [node['name'] for node in studios_nodes]
            studios = ", ".join(studios_names[:4]) if studios_names else "N/A"

            # Format description (strip HTML tags, wrap in double underscores)
            raw_desc = media.get('description') or 'No description available.'
            import re
            clean_desc = re.sub(r'<[^>]+>', '', raw_desc).strip()
            # Telegram caption limit is 1024 characters.
            # Keep description length capped to prevent overflow.
            if len(clean_desc) > 650:
                clean_desc = clean_desc[:620] + "..."

            caption = (
                f"{header}\n\n"
                f"**Type**: {m_type}\n"
                f"**Status**: {m_status}\n"
                f"**Episodes**: `{episodes}`\n"
                f"**Duration**: `{duration} Per Ep.`\n"
                f"**Score**: {score}\n"
                f"**Genres**: `{genres}`\n"
                f"**Studios**: `{studios}`\n\n\n"
                f"__{clean_desc}__"
            )

            # Get poster URL
            cover_image_url = None
            if media.get('coverImage'):
                cover_image_url = media['coverImage'].get('extraLarge') or media['coverImage'].get('large')

            if cover_image_url:
                import io
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    async with session.get(cover_image_url) as resp:
                        if resp.status == 200:
                            image_bytes = await resp.read()
                            image_file = io.BytesIO(image_bytes)
                            image_file.name = "poster.jpg"
                            await bot_client.send_file(
                                event.chat_id,
                                file=image_file,
                                caption=caption,
                                parse_mode="md",
                            )
                            logger.info(f"Successfully posted metadata card for '{title_bold}'")
                            return

            # Fallback to text message if cover image failed
            await bot_client.send_message(
                event.chat_id,
                caption,
                parse_mode="md",
            )
            logger.info(f"Successfully posted text-only metadata card for '{title_bold}'")

        except Exception as exc:
            logger.error(f".ani command failed: {exc}", exc_info=True)
            await bot_client.send_message(event.chat_id, f"❌ Failed to process .ani command: {exc}")

    # -- Live listener: auto-process new .mkv uploads ----------------------
    # Uses the bot client to listen on the channel (since the bot is an admin,
    # it receives messages and can edit others' posts).

    @bot_client.on(events.NewMessage(chats=config.channel_id))
    async def handle_new_upload(event: events.NewMessage.Event) -> None:
        """Automatically process new .mkv documents posted to the channel."""
        message = event.message

        # Must be a document
        if not message.document:
            return
        if not isinstance(message.media, MessageMediaDocument):
            return

        # Find filename
        filename = None
        for attr in message.document.attributes:
            if isinstance(attr, DocumentAttributeFilename):
                filename = attr.file_name
                break

        if not filename or not filename.lower().endswith(".mkv"):
            return

        logger.info(f"New .mkv detected: {filename}")

        try:
            title_from_file, year, quality = extract_movie_info(filename)

            # Try to locate preceding metadata message
            clean_title = None
            studios_list = []
            meta = await scanner._find_metadata_message(message)
            if meta:
                clean_title, studios_list = meta

            if clean_title:
                title = clean_title
            else:
                title = title_from_file

            # Look up studio name and year
            from scanner import BLACKLISTED_PRODUCERS
            studio = "Unknown Studio"
            try:
                info = await anilist.search_anime_info(title)
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

            if (not studio or studio == "Unknown Studio") and studios_list:
                filtered_studios = [
                    s for s in studios_list 
                    if s.lower().strip() not in BLACKLISTED_PRODUCERS
                ]
                if filtered_studios:
                    studio = filtered_studios[0]
                else:
                    studio = studios_list[0]

            channel_id_short = config.channel_id_short
            telegram_link = f"https://t.me/c/{channel_id_short}/{message.id}"

            # Database insert
            movie_id = await db.upsert_movie(title, year, studio)
            await db.add_release(
                movie_id, quality, message.id, config.channel_id, telegram_link
            )
            await db.commit()

            # Edit caption using the bot_client (has permission)
            clean_name = f"{title} ({year})" if year else title
            new_caption = f"**{clean_name} - {studio}**"

            current = message.text or message.message or ""
            if current.strip() != new_caption.strip():
                try:
                    await bot_client.edit_message(
                        message.peer_id,
                        message.id,
                        new_caption,
                        parse_mode="md",
                    )
                    logger.info(f"Caption edited: {clean_name} - {studio}")
                except Exception as exc:
                    logger.warning(f"Caption edit failed: {exc}")

            logger.info(f"Auto-indexed: {clean_name} - {studio} [{quality}]")

        except Exception as exc:
            logger.error(
                f"Auto-index failed for {filename}: {exc}", exc_info=True
            )

    logger.info("All commands registered")
