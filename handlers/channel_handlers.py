"""
Channel management & auto-post mode handlers.
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from config import Config
from database.db import Database
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.formatting import format_html_bold

logger = logging.getLogger(__name__)


@owner_only
async def auto_post_toggle_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/autopost — toggle auto-queue mode on/off."""
    db: Database = context.bot_data["db"]
    current = db.get_setting("auto_post", "0")
    new_value = "0" if current == "1" else "1"
    db.set_setting("auto_post", new_value)

    status = "ON ✅" if new_value == "1" else "OFF ❌"
    await update.message.reply_text(
        f"🔄 Auto-post mode: <b>{status}</b>\n\n"
        + (
            "Every message you send here will be automatically queued."
            if new_value == "1"
            else "Messages will no longer be auto-queued."
        ),
        parse_mode="HTML",
    )


@owner_only
async def set_channel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/setchannel <channel_id or @username>"""
    scheduler: Scheduler = context.bot_data["scheduler"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/setchannel &lt;channel_id or @username&gt;</code>",
            parse_mode="HTML",
        )
        return

    channel = context.args[0].strip()
    scheduler.add_channel(channel)
    await update.message.reply_text(
        f"✅ Channel <code>{channel}</code> added.\n"
        f"Total channels: <b>{len(scheduler.channels)}</b>",
        parse_mode="HTML",
    )


@owner_only
async def list_channels_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/listchannels — show all target channels."""
    scheduler: Scheduler = context.bot_data["scheduler"]
    channels = scheduler.channels

    if not channels:
        await update.message.reply_text(
            "📭 No channels configured.\nUse /setchannel to add one.",
            parse_mode="HTML",
        )
        return

    lines = ["<b>📡 Target Channels</b>\n"]
    for i, ch in enumerate(channels, 1):
        lines.append(f"{i}. <code>{ch}</code>")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@owner_only
async def remove_channel_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/removechannel <channel_id or @username>"""
    scheduler: Scheduler = context.bot_data["scheduler"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/removechannel &lt;channel_id or @username&gt;</code>",
            parse_mode="HTML",
        )
        return

    channel = context.args[0].strip()
    if scheduler.remove_channel(channel):
        await update.message.reply_text(
            f"✅ Channel <code>{channel}</code> removed.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"❌ Channel <code>{channel}</code> not found.",
            parse_mode="HTML",
        )


async def handle_private_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Catch-all for private messages.
    If auto-post is ON and the sender is the owner, queue the message.
    """
    user = update.effective_user
    if user is None or user.id != Config.OWNER_ID:
        return

    db: Database = context.bot_data["db"]
    scheduler: Scheduler = context.bot_data["scheduler"]
    auto_post = db.get_setting("auto_post", "0") == "1"

    if not auto_post:
        return  # auto-post is off, ignore

    msg = update.effective_message
    if msg is None:
        return

    parse_mode = db.get_setting("parse_mode", Config.PARSE_MODE)

    # ── Determine media type and collect data ─────────────────────────────
    if msg.media_group_id:
        # Media group — collect file IDs
        # Note: python-telegram-bot does not natively batch media groups.
        # We queue each photo individually; the scheduler sends them together.
        file_ids = []
        if msg.photo:
            file_ids.append(msg.photo[-1].file_id)
        elif msg.video:
            file_ids.append(msg.video.file_id)
        elif msg.document:
            file_ids.append(msg.document.file_id)

        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)

        item_id = db.add_to_queue(
            media_type="photo" if msg.photo else "video" if msg.video else "document",
            caption=caption,
            file_ids=file_ids,
            parse_mode=parse_mode,
        )

    elif msg.photo:
        file_id = msg.photo[-1].file_id  # highest resolution
        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)

        item_id = db.add_to_queue(
            media_type="photo",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
        )

    elif msg.video:
        file_id = msg.video.file_id
        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)

        item_id = db.add_to_queue(
            media_type="video",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
        )

    elif msg.document:
        file_id = msg.document.file_id
        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)

        item_id = db.add_to_queue(
            media_type="document",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
        )

    elif msg.text:
        content = msg.text
        if parse_mode == "HTML":
            content = format_html_bold(content)

        item_id = db.add_to_queue(
            media_type="text",
            content=content,
            parse_mode=parse_mode,
        )
    else:
        return  # unsupported message type

    # ── Check for instant-forward mode ────────────────────────────────────
    instant_mode = db.get_setting("instant_forward", "0") == "1"
    if instant_mode:
        item = db.get_next_pending()
        if item:
            success = await scheduler.send_to_channels(item)
            if success:
                db.mark_sent(item["id"])
                db.increment_sent()
                await msg.reply_text("⚡ Forwarded instantly!", parse_mode="HTML")
            else:
                db.mark_failed(item["id"])
                db.increment_failed()
                await msg.reply_text("❌ Instant forward failed.", parse_mode="HTML")
            return

    await msg.reply_text(
        f"✅ Queued as <code>#{item_id}</code>  |  "
        f"📦 {db.get_queue_count()} pending",
        parse_mode="HTML",
    )
