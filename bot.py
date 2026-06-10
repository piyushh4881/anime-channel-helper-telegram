"""Telegram Channel Migrator — Entry Point.

This is the main entry point for the Telegram channel migration userbot.
It initialises the Telethon client, loads configuration, connects to the
database, validates channel access, and runs the migration.

Usage:
    python bot.py              # Run migration (or resume)
    python bot.py --dry-run    # Scan without sending
    python bot.py --retry      # Retry failed migrations
    python bot.py --live-only  # Skip historical, go straight to live monitor
    python bot.py --stats      # Show migration statistics

Requirements:
    - Python 3.11+
    - Fill in .env file (copy from .env.example)
    - First run will prompt for Telegram phone number & code
"""

import asyncio
import argparse
import sys
import signal
import logging

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from config import load_config, Config
from database import MigrationDatabase
from migrator import ChannelMigrator
from logger import setup_logger

logger: logging.Logger | None = None


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Telegram Channel Migrator — migrate media between channels safely",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python bot.py                 Full migration (resumes if interrupted)
  python bot.py --dry-run       Scan source channel without sending anything
  python bot.py --retry         Retry all previously failed messages
  python bot.py --live-only     Skip historical scan, only watch for new uploads
  python bot.py --stats         Print migration statistics and exit
        """,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Scan and log without actually sending messages",
    )
    parser.add_argument(
        "--retry",
        action="store_true",
        help="Retry all previously failed migrations",
    )
    parser.add_argument(
        "--live-only",
        action="store_true",
        help="Skip historical migration, go straight to live monitoring",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print migration statistics and exit",
    )
    parser.add_argument(
        "--from-beginning",
        action="store_true",
        help="Scan from the beginning of the channel to fill in missing files",
    )
    return parser.parse_args()


async def show_stats(db: MigrationDatabase) -> None:
    """Display migration statistics from the database."""
    await db.connect()

    success_count = await db.get_migration_count("success")
    error_count = await db.get_migration_count("error")
    total_processed = await db.get_total_processed()
    last_id = await db.get_last_migrated_id()
    failed_ids = await db.get_failed_ids()

    print("\n╔══════════════════════════════════════════╗")
    print("║       📊 MIGRATION STATISTICS            ║")
    print("╠══════════════════════════════════════════╣")
    print(f"║  Total processed  : {total_processed:<20} ║")
    print(f"║  Successful       : {success_count:<20} ║")
    print(f"║  Errors           : {error_count:<20} ║")
    print(f"║  Last message ID  : {str(last_id or 'N/A'):<20} ║")
    print(f"║  Pending retries  : {len(failed_ids):<20} ║")
    print("╚══════════════════════════════════════════╝")

    if failed_ids:
        print(f"\nFailed message IDs: {failed_ids[:20]}")
        if len(failed_ids) > 20:
            print(f"  ... and {len(failed_ids) - 20} more")

    await db.close()


async def main() -> None:
    """Main async entry point."""
    global logger

    import sys
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except AttributeError:
        pass

    args = parse_args()

    # Load configuration
    try:
        config = load_config()
    except ValueError as e:
        print(f"\n❌ Configuration Error:\n{e}")
        print("\nPlease copy .env.example to .env and fill in your settings.")
        sys.exit(1)

    # Override dry_run from CLI flag
    if args.dry_run:
        import dataclasses
        config = dataclasses.replace(config, dry_run=True)

    # Setup logging
    logger = setup_logger("migrator", config.log_level)

    # Stats mode — just show stats and exit
    if args.stats:
        db = MigrationDatabase(config.database_path)
        await show_stats(db)
        return

    # Banner
    logger.info("╔══════════════════════════════════════════════════════╗")
    logger.info("║        TELEGRAM CHANNEL MIGRATOR v1.0               ║")
    logger.info("║   Safe, resumable media migration between channels  ║")
    logger.info("╚══════════════════════════════════════════════════════╝")
    logger.info("")
    logger.info("Configuration:")
    logger.info("  Source      : %s", config.source_channel)
    logger.info("  Destination : %s", config.destination_channel)
    logger.info("  Delay       : %.1f–%.1f seconds", config.min_delay, config.max_delay)
    logger.info(
        "  Rate limits : %d/min, %d/hr, %d/day",
        config.max_per_minute,
        config.max_per_hour,
        config.max_per_day,
    )
    logger.info(
        "  Cooldown    : every %d files → %d min pause",
        config.cooldown_every,
        int(config.cooldown_minutes),
    )
    logger.info("  Dry run     : %s", config.dry_run)
    logger.info("  Live mode   : %s", config.live_mode)
    logger.info("  Log channel : %s", config.log_channel or "disabled")
    logger.info("  Database    : %s", config.database_path)
    logger.info("")

    # Initialize database
    db = MigrationDatabase(config.database_path)
    await db.connect()

    # Initialize Telethon client
    client = TelegramClient(
        config.session_name,
        config.api_id,
        config.api_hash,
    )

    migrator = None
    try:
        logger.info("Connecting to Telegram...")
        await client.start()
        me = await client.get_me()
        logger.info("✅ Logged in as: %s (ID: %d)", me.first_name, me.id)

        # Create migrator
        migrator = ChannelMigrator(client, config, db)

        # Validate channels
        if not await migrator.validate_channels():
            logger.error("❌ Channel validation failed. Exiting.")
            return

        # Retry mode
        if args.retry:
            logger.info("Running in retry mode...")
            await migrator.retry_failed()
            return

        # Historical migration (unless --live-only)
        if not args.live_only:
            await migrator.run_historical_migration(from_beginning=args.from_beginning)

        # Live monitoring mode
        if config.live_mode or args.live_only:
            await migrator.run_live_monitor()
        else:
            logger.info(
                "Historical migration complete. Set LIVE_MODE=true to "
                "continue monitoring for new uploads."
            )

    except KeyboardInterrupt:
        logger.info("\n⏹️  Migration stopped by user (Ctrl+C)")
        logger.info("Progress has been saved. Run again to resume.")
        # Send shutdown report to log channel with last message link
        if migrator and migrator.tg_logger:
            try:
                if migrator.tg_logger._hourly_task:
                    await migrator.tg_logger.stop()
                await migrator.tg_logger.send_shutdown(
                    migrator.progress, reason="User stopped (Ctrl+C)"
                )
            except Exception:
                pass
    except Exception as e:
        if logger:
            logger.critical("🔥 Fatal error: %s: %s", type(e).__name__, e, exc_info=True)
        else:
            print(f"Fatal error: {e}")
        # Send error to log channel
        if migrator and migrator.tg_logger:
            try:
                await migrator.tg_logger.stop()
                await migrator.tg_logger.send_shutdown(
                    migrator.progress, reason=f"Fatal error: {type(e).__name__}: {e}"
                )
            except Exception:
                pass
        sys.exit(1)
    finally:
        await db.close()
        if client.is_connected():
            await client.disconnect()
        if logger:
            logger.info("Cleanup complete. Goodbye.")


if __name__ == "__main__":
    # Handle Ctrl+C gracefully
    if sys.platform == "win32":
        # Windows needs special event loop policy for Ctrl+C
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped.")
