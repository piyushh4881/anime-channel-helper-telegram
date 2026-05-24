"""
/stats command handler.
"""

import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

from database.db import Database
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.helpers import human_interval, uptime_string

logger = logging.getLogger(__name__)


@owner_only
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — display bot statistics."""
    db: Database = context.bot_data["db"]
    scheduler: Scheduler = context.bot_data["scheduler"]
    start_time: datetime = context.bot_data.get("start_time", datetime.utcnow())

    stats = db.get_stats()
    queue_count = db.get_queue_count()
    user_count = db.get_user_count()
    auto_post = db.get_setting("auto_post", "0") == "1"
    instant_fwd = db.get_setting("instant_forward", "0") == "1"

    scheduler_status = "🟢 Running" if scheduler.is_running else "🔴 Stopped"
    interval_text = human_interval(scheduler.interval) if scheduler.interval else "Not set"

    text = (
        "<b>📊 Bot Statistics</b>\n\n"
        f"<b>Uptime:</b> {uptime_string(start_time)}\n"
        f"<b>Users:</b> {user_count}\n\n"
        f"<b>— Scheduler —</b>\n"
        f"Status: {scheduler_status}\n"
        f"Interval: <code>{interval_text}</code>\n"
        f"Channels: {len(scheduler.channels)}\n\n"
        f"<b>— Queue —</b>\n"
        f"Pending: {queue_count}\n\n"
        f"<b>— Messages —</b>\n"
        f"✅ Sent: {stats.get('total_sent', 0)}\n"
        f"❌ Failed: {stats.get('total_failed', 0)}\n"
        f"📅 Last sent: {stats.get('last_sent_at', 'Never')}\n\n"
        f"<b>— Modes —</b>\n"
        f"Auto-post: {'ON ✅' if auto_post else 'OFF ❌'}\n"
        f"Instant forward: {'ON ⚡' if instant_fwd else 'OFF ❌'}"
    )

    await update.message.reply_text(text, parse_mode="HTML")
