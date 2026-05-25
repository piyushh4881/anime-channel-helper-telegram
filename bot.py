"""
Telegram Scheduler Bot — Main Entry Point
==========================================
Production-ready Telegram automation bot with scheduling,
queue management, multi-channel posting, and admin controls.
"""

import asyncio
import logging
import sys
from datetime import datetime

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from config import Config
from database.db import Database
from scheduler.scheduler import Scheduler
from handlers.start import start_command, help_command
from handlers.scheduler_handlers import (
    set_interval_command,
    start_scheduler_command,
    stop_scheduler_command,
)
from handlers.queue_handlers import (
    instant_command,
    queue_command,
    next_command,
    delete_queue_command,
    delete_queue_callback,
    preview_queue_command,
    clear_queue_command,
    pause_queue_command,
    resume_queue_command,
    logs_command,
)
from handlers.channel_handlers import (
    auto_post_toggle_command,
    set_channel_command,
    list_channels_command,
    remove_channel_command,
    handle_private_message,
)
from handlers.stats_handlers import stats_command
from handlers.broadcast import broadcast_command
from handlers.admin import admin_panel_command, admin_callback_handler
from utils.helpers import cleanup_old_logs

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(name)-18s | %(levelname)-7s | %(message)s",
    level=getattr(logging, Config.LOG_LEVEL, logging.INFO),
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)

# Suppress noisy third-party loggers
for noisy in ("httpx", "httpcore", "apscheduler"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


async def post_init(application: Application) -> None:
    """Runs after the bot application has been fully initialised."""
    db: Database = application.bot_data["db"]
    scheduler: Scheduler = application.bot_data["scheduler"]

    # Record uptime start
    application.bot_data["start_time"] = datetime.utcnow()

    # Restore scheduler state if it was running before shutdown
    settings = db.get_settings()
    if settings.get("scheduler_running") == "1":
        interval = int(settings.get("interval", Config.DEFAULT_INTERVAL))
        scheduler.start(interval)
        logger.info("Scheduler restored from persistent state (interval=%ss)", interval)

    # Schedule daily log cleanup
    scheduler.schedule_cleanup(Config.LOG_RETENTION_DAYS)

    logger.info("Bot initialised successfully. Owner ID: %s", Config.OWNER_ID)
    logger.info("Target channels: %s", Config.CHANNELS)


async def post_shutdown(application: Application) -> None:
    """Graceful shutdown hook."""
    scheduler: Scheduler = application.bot_data["scheduler"]
    scheduler.shutdown()
    logger.info("Bot shut down gracefully.")


def main() -> None:
    """Build and run the Telegram bot."""
    Config.validate()

    # ── Database ──────────────────────────────────────────────────────────
    db = Database(Config.DATABASE_PATH)
    db.initialize()
    db.migrate()   # safe backward-compatible schema migration

    # ── Application ───────────────────────────────────────────────────────
    app = (
        Application.builder()
        .token(Config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # ── Shared state ──────────────────────────────────────────────────────
    scheduler = Scheduler(db=db, bot=app.bot, channels=Config.CHANNELS)
    app.bot_data["db"] = db
    app.bot_data["scheduler"] = scheduler

    # ── Command handlers ──────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setinterval", set_interval_command))
    app.add_handler(CommandHandler("startscheduler", start_scheduler_command))
    app.add_handler(CommandHandler("stopscheduler", stop_scheduler_command))
    app.add_handler(CommandHandler("instant", instant_command))
    app.add_handler(CommandHandler("queue", queue_command))
    app.add_handler(CommandHandler("next", next_command))
    app.add_handler(CommandHandler("deletequeue", delete_queue_command))
    app.add_handler(CommandHandler("previewqueue", preview_queue_command))
    app.add_handler(CommandHandler("clearqueue", clear_queue_command))
    app.add_handler(CommandHandler("pausequeue", pause_queue_command))
    app.add_handler(CommandHandler("resumequeue", resume_queue_command))
    app.add_handler(CommandHandler("logs", logs_command))
    app.add_handler(CommandHandler("autopost", auto_post_toggle_command))
    app.add_handler(CommandHandler("setchannel", set_channel_command))
    app.add_handler(CommandHandler("listchannels", list_channels_command))
    app.add_handler(CommandHandler("removechannel", remove_channel_command))
    app.add_handler(CommandHandler("stats", stats_command))
    app.add_handler(CommandHandler("broadcast", broadcast_command))
    app.add_handler(CommandHandler("admin", admin_panel_command))

    # ── Callback query handlers (inline keyboard) ─────────────────────────
    # delete_{id} callbacks for queue item deletion (must be first for specificity)
    app.add_handler(
        CallbackQueryHandler(delete_queue_callback, pattern=r"^delete_\d+$")
    )
    # Admin panel callbacks (all other callbacks)
    app.add_handler(CallbackQueryHandler(admin_callback_handler))

    # ── Auto-post: catch all private media / text ─────────────────────────
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND,
            handle_private_message,
        )
    )

    # ── Start polling ─────────────────────────────────────────────────────
    logger.info("Starting bot in polling mode …")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
