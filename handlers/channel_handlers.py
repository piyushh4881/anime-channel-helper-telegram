"""
Channel management & auto-post mode handlers.

Improvements:
- Media group (album) buffering with 1-second collection window
- Stores source_chat_id + source_message_id for each message
- Stores file_id, caption, media_type individually
- Backward-compatible with existing queue entries
"""

import asyncio
import logging
from typing import Optional

from telegram import Update, Message
from telegram.ext import ContextTypes

from config import Config
from database.db import Database
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.formatting import format_html_bold

logger = logging.getLogger(__name__)

# ── Media group buffer ────────────────────────────────────────────────────────
# Maps media_group_id -> list of messages waiting to be flushed
_media_group_buffer: dict[str, list[Message]] = {}
_media_group_tasks: dict[str, asyncio.Task] = {}


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
    Properly handles media groups (albums) by buffering for 1 second.
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

    # ── Media group (album) handling ─────────────────────────────────────────
    if msg.media_group_id:
        await _handle_media_group_message(msg, db, scheduler, parse_mode, context)
        return

    # ── Single message handling ──────────────────────────────────────────────
    item_id = await _queue_single_message(msg, db, parse_mode)
    if item_id is None:
        return  # unsupported type

    # ── Check for instant-forward mode ────────────────────────────────────
    instant_mode = db.get_setting("instant_forward", "0") == "1"
    if instant_mode:
        item = db.get_queue_item(item_id)
        if item:
            success = await scheduler.send_to_channels(item)
            if success:
                db.mark_sent(item["id"])
                db.increment_sent()
                db.add_log("INFO", f"Instant-forwarded item #{item['id']}")
                await msg.reply_text("⚡ Forwarded instantly!", parse_mode="HTML")
            else:
                db.increment_retry(item["id"], "Instant forward failed")
                db.increment_failed()
                await msg.reply_text("❌ Instant forward failed.", parse_mode="HTML")
            return

    await msg.reply_text(
        f"✅ Queued as <code>#{item_id}</code>  |  "
        f"📦 {db.get_queue_count()} pending",
        parse_mode="HTML",
    )


async def _handle_media_group_message(
    msg: Message,
    db: Database,
    scheduler: Scheduler,
    parse_mode: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Buffer media group messages and flush as a linked batch after 1 second.
    All messages in the group share the same media_group_id in the DB.
    """
    group_id = msg.media_group_id

    # Add message to the buffer
    if group_id not in _media_group_buffer:
        _media_group_buffer[group_id] = []
    _media_group_buffer[group_id].append(msg)

    # Cancel any existing flush task for this group (reset the timer)
    if group_id in _media_group_tasks and not _media_group_tasks[group_id].done():
        _media_group_tasks[group_id].cancel()

    # Schedule flush after 1 second
    task = asyncio.create_task(
        _flush_media_group(group_id, db, scheduler, parse_mode, context)
    )
    _media_group_tasks[group_id] = task


async def _flush_media_group(
    group_id: str,
    db: Database,
    scheduler: Scheduler,
    parse_mode: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Wait 1 second, then queue all buffered messages as a media group."""
    await asyncio.sleep(1.0)

    messages = _media_group_buffer.pop(group_id, [])
    _media_group_tasks.pop(group_id, None)

    if not messages:
        return

    # Sort by message_id to preserve album order
    messages.sort(key=lambda m: m.message_id)

    first_item_id: Optional[int] = None
    item_ids: list[int] = []

    for i, msg in enumerate(messages):
        # Determine file_id and type
        if msg.photo:
            file_id = msg.photo[-1].file_id
            m_type = "photo"
        elif msg.video:
            file_id = msg.video.file_id
            m_type = "video"
        elif msg.document:
            file_id = msg.document.file_id
            m_type = "document"
        elif msg.audio:
            file_id = msg.audio.file_id
            m_type = "audio"
        else:
            continue

        caption = msg.caption or ""
        if parse_mode == "HTML" and caption and i == 0:
            caption = format_html_bold(caption)

        iid = db.add_to_queue(
            media_type=m_type,
            caption=caption if i == 0 else None,
            file_ids=[file_id],
            parse_mode=parse_mode,
            message_type=m_type,
            file_id=file_id,
            media_group_id=group_id,
            source_chat_id=msg.chat.id,
            source_message_id=msg.message_id,
        )
        item_ids.append(iid)
        if first_item_id is None:
            first_item_id = iid

    if not item_ids:
        return

    db.add_log("INFO", f"Queued media group '{group_id}' with {len(item_ids)} items (#{item_ids[0]}–#{item_ids[-1]})")
    logger.info(
        "Media group '%s' flushed: %d items, ids=%s",
        group_id, len(item_ids), item_ids,
    )

    # Try to notify user (best-effort — the last message in the group)
    try:
        await messages[-1].reply_text(
            f"✅ Album queued — <b>{len(item_ids)}</b> items "
            f"(#{item_ids[0]}–#{item_ids[-1]})  |  "
            f"📦 {db.get_queue_count()} pending",
            parse_mode="HTML",
        )
    except Exception as exc:
        logger.debug("Could not send media group confirmation: %s", exc)


async def _queue_single_message(
    msg: Message, db: Database, parse_mode: str
) -> Optional[int]:
    """
    Queue a single (non-album) private message.
    Stores source_chat_id + source_message_id for copy_message support.
    Returns the new queue item ID, or None if unsupported.
    """
    src_chat = msg.chat.id
    src_msg = msg.message_id

    if msg.photo:
        file_id = msg.photo[-1].file_id
        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)
        return db.add_to_queue(
            media_type="photo",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
            message_type="photo",
            file_id=file_id,
            source_chat_id=src_chat,
            source_message_id=src_msg,
        )

    elif msg.video:
        file_id = msg.video.file_id
        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)
        return db.add_to_queue(
            media_type="video",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
            message_type="video",
            file_id=file_id,
            source_chat_id=src_chat,
            source_message_id=src_msg,
        )

    elif msg.document:
        file_id = msg.document.file_id
        caption = msg.caption or ""
        if parse_mode == "HTML" and caption:
            caption = format_html_bold(caption)
        return db.add_to_queue(
            media_type="document",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
            message_type="document",
            file_id=file_id,
            source_chat_id=src_chat,
            source_message_id=src_msg,
        )

    elif msg.animation:
        file_id = msg.animation.file_id
        caption = msg.caption or ""
        return db.add_to_queue(
            media_type="animation",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
            message_type="animation",
            file_id=file_id,
            source_chat_id=src_chat,
            source_message_id=src_msg,
        )

    elif msg.audio:
        file_id = msg.audio.file_id
        caption = msg.caption or ""
        return db.add_to_queue(
            media_type="audio",
            caption=caption,
            file_ids=[file_id],
            parse_mode=parse_mode,
            message_type="audio",
            file_id=file_id,
            source_chat_id=src_chat,
            source_message_id=src_msg,
        )

    elif msg.text:
        content = msg.text
        if parse_mode == "HTML":
            content = format_html_bold(content)
        return db.add_to_queue(
            media_type="text",
            content=content,
            parse_mode=parse_mode,
            message_type="text",
            source_chat_id=src_chat,
            source_message_id=src_msg,
        )

    return None  # unsupported message type
