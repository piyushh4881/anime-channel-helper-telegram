"""
/start and /help command handlers.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import Config
from database.db import Database

logger = logging.getLogger(__name__)

HELP_TEXT = """
<b>📋 Command Reference</b>

<b>— General —</b>
/start — Register & show welcome
/help  — Show this help message

<b>— Scheduler —</b>
/setinterval <code>&lt;interval&gt;</code> — Set posting interval (e.g. 30s, 5m, 2h, 1d)
/startscheduler — Start the auto-poster
/stopscheduler  — Stop the auto-poster

<b>— Queue —</b>
/queue — Show pending queue
/previewqueue — Preview next item
/deletequeue <code>&lt;id&gt;</code> — Remove a queued item
/clearqueue — Clear all pending items
/instant — Send next queued item NOW

<b>— Channels —</b>
/setchannel <code>&lt;id&gt;</code> — Add a target channel
/listchannels — List target channels
/removechannel <code>&lt;id&gt;</code> — Remove a channel

<b>— Auto-Post —</b>
/autopost — Toggle auto-queue mode

<b>— Stats & Admin —</b>
/stats — Show bot statistics
/admin — Admin control panel
/broadcast <code>&lt;message&gt;</code> — Broadcast to all users

<b>— Auto-Post Mode —</b>
When enabled, any message you send to the bot
will be automatically queued for channel posting.
"""


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — register user and show welcome."""
    user = update.effective_user
    if user is None:
        return

    db: Database = context.bot_data["db"]
    db.add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )

    if user.id == Config.OWNER_ID:
        await update.message.reply_text(
            f"👋 Welcome back, <b>{user.first_name}</b>!\n\n"
            f"You are the <b>bot owner</b>. Use /help to see all commands.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"👋 Hello, <b>{user.first_name}</b>!\n\n"
            f"This bot is private. Only the owner can use its features.",
            parse_mode="HTML",
        )
        logger.info("Non-owner user %s (%s) used /start", user.id, user.username)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show command reference (owner only)."""
    user = update.effective_user
    if user is None or user.id != Config.OWNER_ID:
        return
    await update.message.reply_text(HELP_TEXT, parse_mode="HTML")
