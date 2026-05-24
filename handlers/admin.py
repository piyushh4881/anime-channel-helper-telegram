"""
/admin — inline keyboard admin control panel.
"""

import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from database.db import Database
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.helpers import human_interval, uptime_string
from config import Config
from datetime import datetime

logger = logging.getLogger(__name__)

# Callback data constants
CB_REFRESH = "admin:refresh"
CB_START_SCHED = "admin:start_sched"
CB_STOP_SCHED = "admin:stop_sched"
CB_TOGGLE_AUTOPOST = "admin:toggle_autopost"
CB_TOGGLE_INSTANT = "admin:toggle_instant"
CB_TOGGLE_RANDOMIZE = "admin:toggle_randomize"
CB_CLEAR_QUEUE = "admin:clear_queue"
CB_VIEW_LOGS = "admin:view_logs"


def _build_panel_text(db: Database, scheduler: Scheduler, start_time: datetime) -> str:
    stats = db.get_stats()
    queue_count = db.get_queue_count()
    auto_post = db.get_setting("auto_post", "0") == "1"
    instant_fwd = db.get_setting("instant_forward", "0") == "1"
    randomize = db.get_setting("randomize", "0") == "1"
    interval_text = human_interval(scheduler.interval) if scheduler.interval else "—"

    return (
        "<b>⚙️ Admin Control Panel</b>\n\n"
        f"<b>Uptime:</b> {uptime_string(start_time)}\n"
        f"<b>Scheduler:</b> {'🟢 Running' if scheduler.is_running else '🔴 Stopped'}\n"
        f"<b>Interval:</b> {interval_text}\n"
        f"<b>Channels:</b> {len(scheduler.channels)}\n"
        f"<b>Queue:</b> {queue_count} pending\n"
        f"<b>Sent:</b> {stats.get('total_sent', 0)}  |  "
        f"<b>Failed:</b> {stats.get('total_failed', 0)}\n\n"
        f"<b>Auto-post:</b> {'✅ ON' if auto_post else '❌ OFF'}\n"
        f"<b>Instant fwd:</b> {'⚡ ON' if instant_fwd else '❌ OFF'}\n"
        f"<b>Randomize:</b> {'🎲 ON' if randomize else '❌ OFF'}"
    )


def _build_keyboard(scheduler: Scheduler, db: Database) -> InlineKeyboardMarkup:
    auto_post = db.get_setting("auto_post", "0") == "1"
    instant_fwd = db.get_setting("instant_forward", "0") == "1"
    randomize = db.get_setting("randomize", "0") == "1"

    rows = [
        [
            InlineKeyboardButton(
                "⏹ Stop Scheduler" if scheduler.is_running else "▶️ Start Scheduler",
                callback_data=CB_STOP_SCHED if scheduler.is_running else CB_START_SCHED,
            )
        ],
        [
            InlineKeyboardButton(
                f"Auto-post: {'✅' if auto_post else '❌'}",
                callback_data=CB_TOGGLE_AUTOPOST,
            ),
            InlineKeyboardButton(
                f"Instant: {'⚡' if instant_fwd else '❌'}",
                callback_data=CB_TOGGLE_INSTANT,
            ),
        ],
        [
            InlineKeyboardButton(
                f"Randomize: {'🎲' if randomize else '❌'}",
                callback_data=CB_TOGGLE_RANDOMIZE,
            ),
            InlineKeyboardButton("🗑 Clear Queue", callback_data=CB_CLEAR_QUEUE),
        ],
        [
            InlineKeyboardButton("📋 Recent Logs", callback_data=CB_VIEW_LOGS),
            InlineKeyboardButton("🔄 Refresh", callback_data=CB_REFRESH),
        ],
    ]
    return InlineKeyboardMarkup(rows)


@owner_only
async def admin_panel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/admin — show the admin control panel with inline buttons."""
    db: Database = context.bot_data["db"]
    scheduler: Scheduler = context.bot_data["scheduler"]
    start_time = context.bot_data.get("start_time", datetime.utcnow())

    text = _build_panel_text(db, scheduler, start_time)
    keyboard = _build_keyboard(scheduler, db)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=keyboard)


async def admin_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle inline keyboard button presses from the admin panel."""
    query = update.callback_query
    user = update.effective_user

    if user is None or user.id != Config.OWNER_ID:
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    await query.answer()

    db: Database = context.bot_data["db"]
    scheduler: Scheduler = context.bot_data["scheduler"]
    start_time = context.bot_data.get("start_time", datetime.utcnow())
    data = query.data

    if data == CB_START_SCHED:
        if not scheduler.is_running:
            interval_str = db.get_setting("interval")
            interval = int(interval_str) if interval_str else Config.DEFAULT_INTERVAL
            randomize = db.get_setting("randomize", "0") == "1"
            scheduler.start(interval, randomize=randomize)

    elif data == CB_STOP_SCHED:
        if scheduler.is_running:
            scheduler.stop()

    elif data == CB_TOGGLE_AUTOPOST:
        current = db.get_setting("auto_post", "0")
        db.set_setting("auto_post", "0" if current == "1" else "1")

    elif data == CB_TOGGLE_INSTANT:
        current = db.get_setting("instant_forward", "0")
        db.set_setting("instant_forward", "0" if current == "1" else "1")

    elif data == CB_TOGGLE_RANDOMIZE:
        current = db.get_setting("randomize", "0")
        new_val = "0" if current == "1" else "1"
        db.set_setting("randomize", new_val)
        # If scheduler is running, restart with new randomize setting
        if scheduler.is_running:
            interval = scheduler.interval
            scheduler.start(interval, randomize=(new_val == "1"))

    elif data == CB_CLEAR_QUEUE:
        count = db.clear_queue()
        await query.answer(f"Cleared {count} items.", show_alert=True)

    elif data == CB_VIEW_LOGS:
        logs = db.get_recent_logs(10)
        if logs:
            lines = ["<b>📋 Recent Logs</b>\n"]
            for log in logs:
                emoji = "ℹ️" if log["level"] == "INFO" else "❌"
                lines.append(
                    f"{emoji} <code>{log['created_at']}</code>\n    {log['message']}"
                )
            await query.message.reply_text("\n".join(lines), parse_mode="HTML")
        else:
            await query.answer("No logs yet.", show_alert=True)
        return  # don't refresh the panel

    elif data == CB_REFRESH:
        pass  # just refresh

    # Refresh the panel
    text = _build_panel_text(db, scheduler, start_time)
    keyboard = _build_keyboard(scheduler, db)

    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=keyboard
        )
    except Exception:
        pass  # message not modified (content unchanged)
