"""
Scheduler management commands:
  /setinterval, /startscheduler, /stopscheduler
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import Config
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.helpers import human_interval, parse_interval

logger = logging.getLogger(__name__)


@owner_only
async def set_interval_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /setinterval <interval>
    Examples: /setinterval 30s  |  /setinterval 5m  |  /setinterval 2h
    """
    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/setinterval &lt;interval&gt;</code>\n"
            "Examples: <code>30s</code>, <code>5m</code>, <code>2h</code>, <code>1d</code>, <code>1h30m</code>",
            parse_mode="HTML",
        )
        return

    raw = " ".join(context.args)
    seconds = parse_interval(raw)
    if seconds is None or seconds < 10:
        await update.message.reply_text(
            "❌ Invalid interval. Minimum is <b>10 seconds</b>.\n"
            "Examples: <code>30s</code>, <code>5m</code>, <code>2h</code>",
            parse_mode="HTML",
        )
        return

    scheduler: Scheduler = context.bot_data["scheduler"]
    context.bot_data["db"].set_setting("interval", str(seconds))

    # If scheduler is already running, restart with new interval
    if scheduler.is_running:
        randomize = context.bot_data["db"].get_setting("randomize", "0") == "1"
        scheduler.start(seconds, randomize=randomize)
        await update.message.reply_text(
            f"✅ Interval updated & scheduler restarted.\n"
            f"⏱ New interval: <b>{human_interval(seconds)}</b>",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"✅ Interval set to <b>{human_interval(seconds)}</b>.\n"
            f"Use /startscheduler to begin posting.",
            parse_mode="HTML",
        )


@owner_only
async def start_scheduler_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/startscheduler — begin automatic posting."""
    scheduler: Scheduler = context.bot_data["scheduler"]

    if scheduler.is_running:
        await update.message.reply_text(
            "⚠️ Scheduler is already running.\n"
            f"⏱ Current interval: <b>{human_interval(scheduler.interval)}</b>",
            parse_mode="HTML",
        )
        return

    interval_str = context.bot_data["db"].get_setting("interval")
    interval = int(interval_str) if interval_str else Config.DEFAULT_INTERVAL
    randomize = context.bot_data["db"].get_setting("randomize", "0") == "1"

    scheduler.start(interval, randomize=randomize)

    await update.message.reply_text(
        f"▶️ Scheduler <b>started</b>!\n"
        f"⏱ Interval: <b>{human_interval(interval)}</b>\n"
        f"🎲 Randomize: <b>{'ON' if randomize else 'OFF'}</b>\n"
        f"📦 Pending items: <b>{context.bot_data['db'].get_queue_count()}</b>",
        parse_mode="HTML",
    )


@owner_only
async def stop_scheduler_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/stopscheduler — stop automatic posting."""
    scheduler: Scheduler = context.bot_data["scheduler"]

    if not scheduler.is_running:
        await update.message.reply_text("⚠️ Scheduler is not running.", parse_mode="HTML")
        return

    scheduler.stop()
    await update.message.reply_text(
        "⏹ Scheduler <b>stopped</b>.",
        parse_mode="HTML",
    )
