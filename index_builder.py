"""Index builder for the Movie Indexer Bot.

Generates studio-grouped, paginated index messages from the database
and posts them to the Telegram channel. Each entry is a bold hyperlink:

    🎞 **Studio Ghibli**

    • [**Spirited Away (2001) - Studio Ghibli**](https://t.me/c/123/464)
    • [**Castle in the Sky (1986) - Studio Ghibli**](https://t.me/c/123/32)

Telegram message limit is 4096 characters; pages are split at ~3900
to leave room for the header.
"""

from __future__ import annotations

import logging
from typing import Optional

from telethon import TelegramClient
from telethon.errors import FloodWaitError, MessageNotModifiedError
import asyncio

from config import Config
from database import MovieDatabase
from models import Movie, Release

logger = logging.getLogger(__name__)

# Telegram message character limit
MAX_MSG_LENGTH = 4096
# Leave headroom for the "INDEX (N/M)" header and separator
SAFE_MSG_LENGTH = 3800

SEPARATOR = "──────────────────────────"


class IndexBuilder:
    """Builds and posts paginated movie index messages."""

    def __init__(
        self,
        client: TelegramClient,
        db: MovieDatabase,
        config: Config,
    ) -> None:
        self.client = client
        self.db = db
        self.config = config

    # ── Public API ───────────────────────────────────────────────────

    async def build_index(self) -> list[str]:
        """Generate paginated index pages sorted and grouped by studio.

        Returns
        -------
        list[str]
            List of formatted message strings, each under 4096 chars.
        """
        grouped = await self.db.get_movies_grouped_by_studio()

        if not grouped:
            return ["📭 No movies indexed yet. Run /scan first."]

        lines: list[str] = []
        # Sort studios alphabetically
        sorted_studios = sorted(grouped.keys())

        for studio_name in sorted_studios:
            # Studio header
            lines.append(f"🎞 **{studio_name}**")
            lines.append("")

            # Sort movies within this studio alphabetically by movie title
            movies_list = grouped[studio_name]
            sorted_movies = sorted(movies_list, key=lambda x: x[0].title.lower())

            for movie, releases in sorted_movies:
                if releases:
                    # Entry link using the first/primary release to avoid duplicates in the index
                    entry = f"• [{movie.clean_name}]({releases[0].telegram_link})"
                    lines.append(entry)

            # Add an empty line between studio blocks
            lines.append("")

        # Paginate lines into messages
        pages = self._paginate_lines(lines)

        # Add page headers
        total_pages = len(pages)
        result: list[str] = []
        for i, page_content in enumerate(pages, start=1):
            header = f"𝗜𝗡𝗗𝗘𝗫 ({i}/{total_pages})\n{SEPARATOR}\n\n"
            result.append(header + page_content)

        return result

    async def post_index(self, target_chat_id: int) -> int:
        """Build and send the index directly to the user's private chat.

        Deletes any previously sent index messages in that chat first.

        Returns
        -------
        int
            Number of index pages sent.
        """
        # Delete old index messages in the target chat
        await self._delete_old_index(target_chat_id)

        # Build new index
        pages = await self.build_index()

        # Send each page
        new_message_ids: list[int] = []
        for page in pages:
            try:
                msg = await self.client.send_message(
                    target_chat_id,
                    page,
                    parse_mode="md",
                    link_preview=False,
                )
                new_message_ids.append(msg.id)
                logger.info(f"Sent index page (msg {msg.id}) to PM {target_chat_id}")
                await asyncio.sleep(1.5)  # Rate limit between posts
            except FloodWaitError as exc:
                logger.warning(f"FloodWait sending index: {exc.seconds}s")
                await asyncio.sleep(exc.seconds)
                msg = await self.client.send_message(
                    target_chat_id, page, parse_mode="md", link_preview=False
                )
                new_message_ids.append(msg.id)

        # Save new index message IDs for future cleanup
        await self.db.save_index_message_ids(target_chat_id, new_message_ids)

        logger.info(f"Index sent to user PM: {len(pages)} pages")
        return len(pages)

    def format_search_result(
        self,
        movie: Movie,
        releases: list[Release],
    ) -> str:
        """Format a single movie for /search output.

        Returns something like:
            **Spirited Away (2001) - Studio Ghibli**
            • [1080p BD](link) | [720p BD](link)
        """
        lines: list[str] = []
        header = f"**{movie.clean_name} - {movie.studio}**"
        lines.append(header)

        if releases:
            quality_links = [
                f"[{r.quality}]({r.telegram_link})" for r in releases
            ]
            lines.append("• " + " | ".join(quality_links))

        return "\n".join(lines)

    # ── Private helpers ──────────────────────────────────────────────

    def _format_studio_block(
        self,
        studio_name: str,
        movies: list[tuple[Movie, list[Release]]],
    ) -> str:
        """Format a single studio's movie list.

        Each entry is a bold hyperlink:
            • [**Title (Year) - Studio**](link)
        """
        lines: list[str] = [f"🎞 **{studio_name}**", ""]

        for movie, releases in sorted(movies, key=lambda x: x[0].title.lower()):
            if releases:
                # Use the first release link for the main entry
                primary_link = releases[0].telegram_link
                entry = f"• [**{movie.clean_name} - {movie.studio}**]({primary_link})"
            else:
                entry = f"• **{movie.clean_name} - {movie.studio}**"

            lines.append(entry)

        lines.append("")  # Blank line after studio block
        return "\n".join(lines)

    def _paginate_lines(self, lines: list[str]) -> list[str]:
        """Split index lines into pages that fit Telegram's character limit."""
        pages: list[str] = []
        current_page: list[str] = []
        current_length = 0

        for line in lines:
            line_length = len(line) + 1  # +1 for newline

            # If adding this line would exceed the limit, start a new page
            if current_length + line_length > SAFE_MSG_LENGTH and current_page:
                pages.append("\n".join(current_page))
                current_page = []
                current_length = 0

            current_page.append(line)
            current_length += line_length

        # Don't forget the last page
        if current_page:
            pages.append("\n".join(current_page))

        return pages if pages else ["📭 No movies to display."]

    async def _delete_old_index(self, target_chat_id: int) -> None:
        """Delete previously sent index messages from the specified chat (PM)."""
        old_messages = await self.db.get_index_message_ids()

        if not old_messages:
            return

        deleted_count = 0
        for msg_id, chat_id in old_messages:
            if chat_id == target_chat_id:
                try:
                    await self.client.delete_messages(chat_id, msg_id)
                    logger.debug(f"Deleted old index message {msg_id} in chat {chat_id}")
                    deleted_count += 1
                except Exception as exc:
                    logger.warning(f"Failed to delete index msg {msg_id} in chat {chat_id}: {exc}")

                await asyncio.sleep(0.3)

        logger.info(f"Deleted {deleted_count} old index messages from user PM")
