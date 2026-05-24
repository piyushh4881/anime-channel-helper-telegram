"""
Queue management commands:
  /queue, /previewqueue, /deletequeue, /clearqueue, /instant
"""

import logging

from telegram import Update
from telegram.ext import ContextTypes

from database.db import Database
from scheduler.scheduler import Scheduler
from utils.decorators import owner_only
from utils.formatting import preview_text

logger = logging.getLogger(__name__)


@owner_only
async def queue_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/queue — show all pending queue items."""
    db: Database = context.bot_data["db"]
    items = db.get_all_pending()

    if not items:
        await update.message.reply_text("📭 The queue is empty.", parse_mode="HTML")
        return

    lines = [f"<b>📬 Message Queue ({len(items)} pending)</b>\n"]
    for item in items[:25]:  # cap display at 25
        media_emoji = {
            "text": "📝",
            "photo": "🖼",
            "video": "🎬",
            "document": "📎",
            "media_group": "🖼🖼",
        }.get(item["media_type"], "📦")

        preview = preview_text(item.get("content") or item.get("caption"), 40)
        retries = f" ⚠️×{item['retry_count']}" if item.get("retry_count") else ""
        lines.append(
            f"{media_emoji} <code>#{item['id']}</code> {preview}{retries}"
        )

    if len(items) > 25:
        lines.append(f"\n… and {len(items) - 25} more items.")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@owner_only
async def preview_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/previewqueue — show details of the next item to be sent."""
    db: Database = context.bot_data["db"]
    item = db.get_next_pending()

    if item is None:
        await update.message.reply_text("📭 Queue is empty.", parse_mode="HTML")
        return

    lines = [
        f"<b>🔍 Next Queue Item #{item['id']}</b>\n",
        f"<b>Type:</b> {item['media_type']}",
        f"<b>Parse mode:</b> {item.get('parse_mode', 'HTML')}",
        f"<b>Retries:</b> {item.get('retry_count', 0)}",
        f"<b>Created:</b> {item.get('created_at', 'N/A')}",
    ]

    if item.get("content"):
        lines.append(f"\n<b>Content:</b>\n{item['content'][:500]}")
    if item.get("caption"):
        lines.append(f"\n<b>Caption:</b>\n{item['caption'][:500]}")
    if item.get("file_ids"):
        lines.append(f"\n<b>Files:</b> {len(item['file_ids'])} attached")

    await update.message.reply_text("\n".join(lines), parse_mode="HTML")


@owner_only
async def delete_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/deletequeue <id> — remove a specific item from the queue."""
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
        await update.message.reply_text(
            f"🗑 Item <code>#{item_id}</code> removed from queue.",
            parse_mode="HTML",
        )
    else:
        await update.message.reply_text(
            f"❌ Item <code>#{item_id}</code> not found.",
            parse_mode="HTML",
        )


@owner_only
async def clear_queue_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """/clearqueue — remove all pending items."""
    db: Database = context.bot_data["db"]
    count = db.clear_queue()
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
        db.mark_failed(item["id"])
        db.increment_failed()
        db.add_log("ERROR", f"Instant-send failed for item #{item['id']}")
        await update.message.reply_text(
            f"❌ Failed to send item <code>#{item['id']}</code>.",
            parse_mode="HTML",
        )
