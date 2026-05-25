"""
Queue management commands:
  /queue, /next, /previewqueue, /deletequeue, /clearqueue, /instant,
  /pausequeue, /resumequeue

Improvements:
- /queue shows rich cards with inline [🗑 Delete] buttons per item
- /next and /previewqueue copy/reconstruct actual messages
- delete_{id} callback for instant inline deletion
- /pausequeue and /resumequeue commands
- /deletequeue <id> kept as fallback
"""

import logging
from typing import Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.db import Database
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.formatting import preview_text
from config import Config

logger = logging.getLogger(__name__)

MEDIA_EMOJI = {
    "text": "📝",
    "photo": "🖼",
    "video": "🎬",
    "document": "📎",
    "animation": "🎞",
    "audio": "🎵",
    "media_group": "🖼🖼",
}


def _item_card(item: dict) -> str:
    """Build a rich text card for one queue item."""
    media_type = item.get("media_type", "unknown").upper()
    emoji = MEDIA_EMOJI.get(item.get("media_type", ""), "📦")
    item_id = item["id"]
    caption = item.get("caption") or item.get("content") or ""
    preview = preview_text(caption, 60) if caption else "(no caption)"
    created = item.get("created_at", "N/A")
    retries = item.get("retry_count", 0)
    paused = item.get("paused", 0)

    lines = [f"<b>#{item_id} | {emoji} {media_type}</b>"]
    if item.get("media_type") == "text":
        lines.append(f"<i>{preview}</i>")
    else:
        lines.append(f"Caption: {preview}")
    lines.append(f"Added: {created}")
    if retries:
        lines.append(f"⚠️ Retries: {retries}")
    if paused:
        lines.append("⏸ <i>PAUSED</i>")
    return "\n".join(lines)


def _delete_keyboard(item_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Delete", callback_data=f"delete_{item_id}")]
    ])


@owner_only
async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/queue — show all pending queue items with inline Delete buttons."""
    db: Database = context.bot_data["db"]
    items = db.get_all_pending()

    if not items:
        await update.message.reply_text("📭 The queue is empty.", parse_mode="HTML")
        return

    header = f"<b>📬 Message Queue — {len(items)} pending</b>"
    await update.message.reply_text(header, parse_mode="HTML")

    for item in items[:25]:
        card = _item_card(item)
        keyboard = _delete_keyboard(item["id"])
        await update.message.reply_text(card, parse_mode="HTML", reply_markup=keyboard)

    if len(items) > 25:
        await update.message.reply_text(
            f"… and <b>{len(items) - 25}</b> more items.",
            parse_mode="HTML",
        )


@owner_only
async def next_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/next — preview the next item in the queue (sends actual message)."""
    db: Database = context.bot_data["db"]
    item = db.get_next_pending()

    if item is None:
        await update.message.reply_text("📭 Queue is empty.", parse_mode="HTML")
        return

    await update.message.reply_text(
        f"🔍 <b>Preview — Next Item #{item['id']}</b>", parse_mode="HTML"
    )
    await _send_preview(update, db, item)
    await update.message.reply_text(_item_card(item), parse_mode="HTML")


@owner_only
async def preview_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/previewqueue — preview next N items (actual messages + metadata)."""
    db: Database = context.bot_data["db"]
    items = db.get_all_pending()

    if not items:
        await update.message.reply_text("📭 Queue is empty.", parse_mode="HTML")
        return

    count = min(len(items), Config.PREVIEW_COUNT)
    await update.message.reply_text(
        f"🔍 <b>Preview — Next {count} Items</b>", parse_mode="HTML"
    )

    for item in items[:count]:
        await _send_preview(update, db, item)
        await update.message.reply_text(
            _item_card(item),
            parse_mode="HTML",
            reply_markup=_delete_keyboard(item["id"]),
        )


async def _send_preview(update: Update, db: Database, item: dict) -> None:
    """
    Send the actual message content to the admin chat.
    Prefers copy_message; falls back to reconstruction.
    """
    bot = update.get_bot()
    admin_chat = update.effective_chat.id

    src_chat = item.get("source_chat_id")
    src_msg = item.get("source_message_id")

    # ── Preferred: copy the original message ──────────────────────────────
    if src_chat and src_msg:
        try:
            await bot.copy_message(
                chat_id=admin_chat,
                from_chat_id=src_chat,
                message_id=src_msg,
            )
            return
        except Exception as exc:
            logger.warning("copy_message failed during preview (falling back): %s", exc)

    # ── Fallback: reconstruct from stored data ─────────────────────────────
    media_type = item.get("media_type", "text")
    content = item.get("content", "")
    caption = item.get("caption", "")
    file_ids: list = item.get("file_ids") or []
    file_id = item.get("file_id") or (file_ids[0] if file_ids else None)
    parse_mode = item.get("parse_mode", "HTML")
    pm = ParseMode.HTML if parse_mode == "HTML" else ParseMode.MARKDOWN_V2

    try:
        if media_type == "text":
            await bot.send_message(chat_id=admin_chat, text=content or "(no text)", parse_mode=pm)
        elif media_type == "photo" and file_ids and len(file_ids) > 1:
            from telegram import InputMediaPhoto
            media = [
                InputMediaPhoto(fid, caption=caption if i == 0 else None, parse_mode=pm if i == 0 else None)
                for i, fid in enumerate(file_ids)
            ]
            await bot.send_media_group(chat_id=admin_chat, media=media)
        elif media_type == "photo" and file_id:
            await bot.send_photo(chat_id=admin_chat, photo=file_id, caption=caption, parse_mode=pm)
        elif media_type == "video" and file_id:
            await bot.send_video(chat_id=admin_chat, video=file_id, caption=caption, parse_mode=pm)
        elif media_type == "document" and file_id:
            await bot.send_document(chat_id=admin_chat, document=file_id, caption=caption, parse_mode=pm)
        elif media_type == "animation" and file_id:
            await bot.send_animation(chat_id=admin_chat, animation=file_id, caption=caption, parse_mode=pm)
        elif media_type == "audio" and file_id:
            await bot.send_audio(chat_id=admin_chat, audio=file_id, caption=caption, parse_mode=pm)
        else:
            await bot.send_message(
                chat_id=admin_chat,
                text=f"⚠️ Cannot preview type <code>{media_type}</code> — no media data available.",
                parse_mode="HTML",
            )
    except Exception as exc:
        logger.error("Preview reconstruction failed: %s", exc)
        await bot.send_message(
            chat_id=admin_chat,
            text=f"⚠️ Preview failed: <code>{exc}</code>",
            parse_mode="HTML",
        )


@owner_only
async def delete_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/deletequeue <id> — remove a specific item from the queue (fallback for inline)."""
    db: Database = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/deletequeue &lt;id&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        item_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID must be a number.", parse_mode="HTML")
        return

    if db.delete_queue_item(item_id):
        db.add_log("INFO", f"Deleted queue item #{item_id} via /deletequeue")
        await update.message.reply_text(
            f"🗑 Item <code>#{item_id}</code> removed from queue.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"❌ Item <code>#{item_id}</code> not found.",
            parse_mode="HTML",
        )


async def delete_queue_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle delete_{id} inline keyboard button press."""
    query = update.callback_query
    user = update.effective_user

    if user is None or user.id != Config.OWNER_ID:
        await query.answer("⛔ Unauthorized.", show_alert=True)
        return

    await query.answer()

    data = query.data  # e.g. "delete_42"
    try:
        item_id = int(data.split("_", 1)[1])
    except (IndexError, ValueError):
        await query.answer("❌ Invalid callback data.", show_alert=True)
        return

    db: Database = context.bot_data["db"]
    if db.delete_queue_item(item_id):
        db.add_log("INFO", f"Deleted queue item #{item_id} via inline button")
        try:
            await query.edit_message_text(
                f"✅ Item <code>#{item_id}</code> deleted.",
                parse_mode="HTML",
            )
        except Exception:
            pass
    else:
        await query.answer(f"❌ Item #{item_id} not found.", show_alert=True)


@owner_only
async def pause_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/pausequeue <id> — pause a pending queue item."""
    db: Database = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/pausequeue &lt;id&gt;</code>", parse_mode="HTML"
        )
        return

    try:
        item_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID must be a number.", parse_mode="HTML")
        return

    if db.pause_item(item_id):
        db.add_log("INFO", f"Paused queue item #{item_id}")
        await update.message.reply_text(
            f"⏸ Item <code>#{item_id}</code> paused.", parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"❌ Item <code>#{item_id}</code> not found or not pending.", parse_mode="HTML"
        )


@owner_only
async def resume_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/resumequeue <id> — resume a paused queue item."""
    db: Database = context.bot_data["db"]

    if not context.args:
        await update.message.reply_text(
            "⚠️ Usage: <code>/resumequeue &lt;id&gt;</code>", parse_mode="HTML"
        )
        return

    try:
        item_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("❌ ID must be a number.", parse_mode="HTML")
        return

    if db.resume_item(item_id):
        db.add_log("INFO", f"Resumed queue item #{item_id}")
        await update.message.reply_text(
            f"▶️ Item <code>#{item_id}</code> resumed.", parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            f"❌ Item <code>#{item_id}</code> not found or not paused.", parse_mode="HTML"
        )


@owner_only
async def clear_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/clearqueue — remove all pending items."""
    db: Database = context.bot_data["db"]
    count = db.clear_queue()
    db.add_log("INFO", f"Cleared {count} pending items from queue")
    await update.message.reply_text(
        f"🗑 Cleared <b>{count}</b> pending item(s) from the queue.",
        parse_mode="HTML",
    )


@owner_only
async def instant_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/instant — immediately send the next queued item."""
    db: Database = context.bot_data["db"]
    scheduler: Scheduler = context.bot_data["scheduler"]
    item = db.get_next_pending()

    if item is None:
        await update.message.reply_text("📭 Queue is empty.", parse_mode="HTML")
        return

    await update.message.reply_text(
        f"⏳ Sending item <code>#{item['id']}</code> …",
        parse_mode="HTML",
    )

    success = await scheduler.send_to_channels(item)

    if success:
        db.mark_sent(item["id"])
        db.increment_sent()
        db.add_log("INFO", f"Instant-sent queue item #{item['id']}")
        await update.message.reply_text(
            f"✅ Item <code>#{item['id']}</code> sent successfully!",
            parse_mode="HTML",
        )
    else:
        db.increment_retry(item["id"], "Instant-send failed")
        db.increment_failed()
        db.add_log("ERROR", f"Instant-send failed for item #{item['id']}")
        await update.message.reply_text(
            f"❌ Failed to send item <code>#{item['id']}</code>.",
            parse_mode="HTML",
        )


@owner_only
async def logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/logs [N] — show the last N bot log entries (default 20)."""
    db: Database = context.bot_data["db"]

    n = 20
    if context.args:
        try:
            n = max(1, min(int(context.args[0]), 50))
        except ValueError:
            pass

    logs = db.get_recent_logs(n)

    if not logs:
        await update.message.reply_text("📋 No logs yet.", parse_mode="HTML")
        return

    lines = [f"<b>📋 Last {len(logs)} Log Entries</b>\n"]
    for log in reversed(logs):  # oldest first
        level = log.get("level", "INFO")
        emoji = {"INFO": "ℹ️", "WARNING": "⚠️", "ERROR": "❌", "DEBUG": "🔍"}.get(level, "•")
        ts = log.get("created_at", "")[:16]  # YYYY-MM-DD HH:MM
        msg = log.get("message", "")
        lines.append(f"{emoji} <code>{ts}</code> {msg}")

    text = "\n".join(lines)
    # Telegram message limit is 4096 chars
    if len(text) > 4000:
        text = text[-4000:]

    await update.message.reply_text(text, parse_mode="HTML")
