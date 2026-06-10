"""Movie Indexer Bot -- Entry point.

Dual-client architecture:
  - USERBOT (your personal Telegram account) for channel operations:
    scanning history, editing captions, posting index messages.
  - BOT CLIENT (bot token from @BotFather) for receiving user commands
    via private messages (/scan, /search, /stats, etc.).

Telegram restricts bots from reading channel history and editing
other users' messages, so the userbot handles all channel work.

Usage
-----
    cd telegram-movie-indexer
    python bot.py

First run will prompt for your phone number to authenticate the userbot.
The session is saved locally so you only need to do this once.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path

from telethon import TelegramClient

from anilist import AniListClient
from config import load_config
from commands import register_commands
from database import MovieDatabase

# -- Logging setup ---------------------------------------------------------

def setup_logging(level: str) -> None:
    """Configure structured logging to console and file."""
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / "movie_indexer.log"

    root = logging.getLogger()
    root.setLevel(getattr(logging, level, logging.INFO))

    # Console handler (ASCII-safe for Windows cp1252)
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)-20s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(console_fmt)
    root.addHandler(console)

    # File handler (UTF-8)
    file_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    )
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(file_fmt)
    root.addHandler(file_handler)

    # Quiet noisy libraries
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


logger = logging.getLogger("movie_indexer")


# -- Main ------------------------------------------------------------------

async def main() -> None:
    """Start the Movie Indexer Bot."""
    # Load and validate config
    try:
        config = load_config()
    except ValueError as exc:
        print(f"\nConfiguration Error:\n{exc}\n")
        print("Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    setup_logging(config.log_level)

    logger.info("=" * 50)
    logger.info("  Movie Indexer Bot starting")
    logger.info("=" * 50)

    # -- Database ----------------------------------------------------------
    db = MovieDatabase(config.database_path)
    await db.connect()

    # -- AniList client (cache primed from DB) -----------------------------
    anilist = AniListClient()
    cached_studios = await db.load_studio_cache()
    for title, studio in cached_studios.items():
        anilist.set_cache(title, studio)
    logger.info(f"AniList cache primed with {len(cached_studios)} entries from DB")

    # -- Bot client ---------------------------------
    bot_session = str(Path(__file__).parent / f"{config.session_name}_bot")
    bot = TelegramClient(bot_session, config.api_id, config.api_hash)

    logger.info("Connecting bot...")
    await bot.start(bot_token=config.bot_token)
    me_bot = await bot.get_me()
    logger.info(f"Bot authenticated as: @{me_bot.username} ({me_bot.first_name})")

    # -- Register command handlers -----------------------------------------
    register_commands(
        bot_client=bot,
        db=db,
        anilist=anilist,
        config=config,
    )

    logger.info(f"Monitoring channel: {config.channel_id}")
    logger.info(f"Admin users: {config.admin_users or 'everyone (no restriction)'}")
    logger.info("Bot is running. Send /scan to the bot to start indexing.")

    # -- Run bot client ----------------------------------------------------
    try:
        await bot.run_until_disconnected()
    except KeyboardInterrupt:
        logger.info("KeyboardInterrupt received")
    finally:
        logger.info("Shutting down...")
        await anilist.close()
        await db.close()
        if bot.is_connected():
            await bot.disconnect()
        logger.info("Bot stopped cleanly")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
