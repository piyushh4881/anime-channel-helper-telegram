import os
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from bot.middleware.auth import check_user_access
from bot.keyboards import builders
from bot.state import UserState
from database.db import async_session
import database.crud as crud

logger = logging.getLogger(__name__)


@Client.on_callback_query(filters.regex("^post_schedule$"))
async def post_schedule_cb(client: Client, callback_query: CallbackQuery):
    """Initiates scheduling for the current draft post."""
    user_id = callback_query.from_user.id
    state, state_data = await UserState.get_state(user_id)
    
    if state_data:
        await UserState.set_state(user_id, "POST_WAIT_SCHEDULE", state_data)
        await callback_query.message.edit_text(
            "📅 **Schedule Post**\n\n"
            "Please send the date and time when you want to publish this post.\n\n"
            "**Supported Formats:**\n"
            "• Relative: `+15m` (minutes), `+3h` (hours), `+2d` (days)\n"
            "• Absolute: `YYYY-MM-DD HH:MM` (e.g. `2026-06-01 18:00`)\n\n"
            "Send scheduled time below:",
            reply_markup=builders.get_back_button("post_preview_back")
        )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^menu_scheduled$"))
async def scheduled_list_cb(client: Client, callback_query: CallbackQuery):
    """Lists all scheduled posts for the user."""
    user_id = callback_query.from_user.id
    if not await check_user_access(client, callback_query):
        return

    async with async_session() as db:
        # Fetch pending (scheduled) posts
        from sqlalchemy import select, desc
        result = await db.execute(
            select(crud.Post)
            .where(crud.Post.user_id == user_id)
            .where(crud.Post.status == "pending")
            .order_by(crud.Post.scheduled_at)
        )
        posts = list(result.scalars().all())

    await callback_query.message.edit_text(
        "📅 **Your Scheduled Posts**\n\n"
        "Here are your upcoming scheduled Instagram posts. Select one to view details or cancel it:",
        reply_markup=builders.get_scheduled_keyboard(posts)
    )
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^sched_view_(\\d+)$"))
async def view_scheduled_post_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    post_id = int(callback_query.matches[0].group(1))

    async with async_session() as db:
        post = await crud.get_post(db, post_id)

    if not post or post.user_id != user_id:
        await callback_query.answer("Post not found.", show_alert=True)
        return

    media_type_str = "🖼️ Single Photo" if post.media_type == "image" else "📚 Carousel Album"
    dt_str = post.scheduled_at.strftime("%Y-%m-%d %H:%M")
    
    post_details = (
        "📅 **Scheduled Post Details**\n\n"
        f"📦 **Media:** {media_type_str} ({len(post.media_files)} items)\n"
        f"⏰ **Scheduled Time:** {dt_str}\n"
        f"📝 **Caption:**\n{post.caption or '_No caption_'}"
    )

    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancel Schedule & Delete", callback_data=f"sched_cancel_{post.id}")],
        [InlineKeyboardButton("⬅️ Back", callback_data="menu_scheduled")]
    ])

    await callback_query.message.edit_text(post_details, reply_markup=keyboard)
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^sched_cancel_(\\d+)$"))
async def cancel_scheduled_post_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    post_id = int(callback_query.matches[0].group(1))

    async with async_session() as db:
        post = await crud.get_post(db, post_id)
        if post and post.user_id == user_id:
            # Delete media files
            for file in post.media_files:
                try:
                    if os.path.exists(file):
                        os.remove(file)
                except Exception:
                    pass
            # Remove post entry from db
            await db.delete(post)
            await db.commit()
            success = True
        else:
            success = False

    if success:
        await callback_query.answer("Scheduled post cancelled and deleted successfully.")
        # Go back to scheduled posts list
        async with async_session() as db:
            from sqlalchemy import select
            result = await db.execute(
                select(crud.Post)
                .where(crud.Post.user_id == user_id)
                .where(crud.Post.status == "pending")
                .order_by(crud.Post.scheduled_at)
            )
            posts = list(result.scalars().all())
            
        await callback_query.message.edit_text(
            "📅 **Your Scheduled Posts**\n\n"
            "Here are your upcoming scheduled Instagram posts. Select one to view details or cancel it:",
            reply_markup=builders.get_scheduled_keyboard(posts)
        )
    else:
        await callback_query.answer("Failed to cancel scheduled post.", show_alert=True)
