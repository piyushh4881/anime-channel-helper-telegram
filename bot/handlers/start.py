import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery

from bot.middleware.auth import check_user_access
from bot.keyboards import builders
from bot.state import UserState
from database.db import async_session
import database.crud as crud

logger = logging.getLogger(__name__)


@Client.on_message(filters.all, group=-1)
async def log_all_messages(client: Client, message: Message):
    user_id = message.from_user.id if message.from_user else "Unknown"
    text_content = message.text or message.caption or "No Text"
    logger.info("📥 UPDATE RECEIVED: User=%s, Text='%s', Photo=%s", user_id, text_content, message.photo is not None)


@Client.on_message(filters.command("start") & filters.private)
async def start_cmd(client: Client, message: Message):
    """Handles the /start command."""
    user_id = message.from_user.id
    
    # Check access (registration, bans, admin-only, maintenance, force subscribe)
    if not await check_user_access(client, message):
        return

    # Clear state on start
    await UserState.clear_state(user_id)

    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        is_admin = user.role == "admin"

    welcome_text = (
        f"👋 **Welcome to the Instagram Poster Bot, {message.from_user.first_name}!**\n\n"
        "This bot helps you crop, watermark, schedule, and upload posts directly to your Instagram account.\n\n"
        "⚡ Send one or multiple images as a starting point to create a new post!"
    )
    await message.reply_text(welcome_text, reply_markup=builders.get_home_keyboard(is_admin))


@Client.on_callback_query(filters.regex("^go_home$"))
async def go_home_cb(client: Client, callback_query: CallbackQuery):
    """Callback to return to home menu."""
    user_id = callback_query.from_user.id
    
    if not await check_user_access(client, callback_query):
        return

    await UserState.clear_state(user_id)

    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        is_admin = user.role == "admin"

    welcome_text = (
        "🏠 **Main Menu**\n\n"
        "Select an action using the inline keyboard below, or simply send an image to start creating a post."
    )
    
    try:
        await callback_query.message.edit_text(welcome_text, reply_markup=builders.get_home_keyboard(is_admin))
    except Exception as e:
        logger.warning("Failed to edit start message: %s. Sending new one.", e)
        await callback_query.message.reply_text(welcome_text, reply_markup=builders.get_home_keyboard(is_admin))
        await callback_query.message.delete()
    
    await callback_query.answer()


@Client.on_callback_query(filters.regex("^check_sub$"))
async def check_sub_cb(client: Client, callback_query: CallbackQuery):
    """Re-checks force subscription when user clicks 'Try Again'."""
    user_id = callback_query.from_user.id
    
    # check_user_access will handle checking and updating the interface if still not subscribed
    if not await check_user_access(client, callback_query):
        return

    # If subscription checks passed, greet the user
    await UserState.clear_state(user_id)

    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        is_admin = user.role == "admin"

    welcome_text = (
        "✅ **Subscription Verified!**\n\n"
        "Thank you for subscribing. You can now use all the features of the bot.\n\n"
        "⚡ Send an image or choose an option from the menu."
    )
    await callback_query.message.edit_text(welcome_text, reply_markup=builders.get_home_keyboard(is_admin))
    await callback_query.answer("Subscription verified!", show_alert=True)
