"""
/broadcast command — send a message to all registered users.
"""

import logging

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from database.db import Database
from utils.decorators import owner_only

logger = logging.getLogger(__name__)


@owner_only
async def broadcast_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /broadcast <message>
    Sends <message> to every user who has ever used /start.
    """
    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/broadcast &lt;message&gt;</code>",
            parse_mode="HTML",
        )
        return

    text = " ".join(context.args)
    db: Database = context.bot_data["db"]
    users = db.get_all_users()

    sent = 0
    failed = 0

    status_msg = await update.message.reply_text(
        f"📡 Broadcasting to <b>{len(users)}</b> users …",
        parse_mode="HTML",
    )

    for user in users:
        try:
            await context.bot.send_message(
                chat_id=user["user_id"],
                text=text,
                parse_mode="HTML",
            )
            sent += 1
        except TelegramError as exc:
            logger.warning("Broadcast failed for user %s: %s", user["user_id"], exc)
            failed += 1

    await status_msg.edit_text(
        f"📡 Broadcast complete!\n\n"
        f"✅ Delivered: <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>",
        parse_mode="HTML",
    )

    db.add_log("INFO", f"Broadcast sent to {sent}/{len(users)} users")
