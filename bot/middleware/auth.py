import logging
from pyrogram import Client
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from database.db import async_session
import database.crud as crud

logger = logging.getLogger(__name__)


async def is_subscribed(client: Client, user_id: int, channel: str) -> bool:
    """Helper to check if a user is subscribed to a channel."""
    if not channel:
        return True
    try:
        # Check if channel is username or ID
        chat_id = int(channel) if (channel.startswith("-100") or channel.isdigit()) else f"@{channel}"
        member = await client.get_chat_member(chat_id, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        logger.warning("Failed to check subscription for user %d in channel %s: %s", user_id, channel, e)
        return False


async def check_user_access(client: Client, update: Message | CallbackQuery) -> bool:
    """
    Checks user status, ban status, admin mode, maintenance, and force subscription.
    Sends appropriate reply if access is denied and returns False.
    """
    user_id = update.from_user.id
    username = update.from_user.username
    
    is_cb = isinstance(update, CallbackQuery)
    msg = update.message if is_cb else update

    async with async_session() as db:
        # Register user / load user
        user = await crud.get_or_create_user(db, user_id, username)

        # 1. Check ban status
        if user.status == "banned":
            err_text = "🚫 You are banned from using this bot."
            if is_cb:
                await update.answer(err_text, show_alert=True)
            else:
                await update.reply_text(err_text)
            return False

        # 2. Check admin bypass
        is_admin = user.role == "admin" or user_id in Config.ADMINS

        if not is_admin:
            # 3. Check maintenance mode
            maintenance = await crud.get_system_setting(db, "maintenance_mode", Config.MAINTENANCE_MODE)
            if maintenance:
                err_text = "🛠️ The bot is currently under maintenance. Please try again later."
                if is_cb:
                    await update.answer(err_text, show_alert=True)
                else:
                    await update.reply_text(err_text)
                return False

            # 4. Check admin-only mode
            admin_only = await crud.get_system_setting(db, "admin_only", Config.ADMIN_ONLY)
            if admin_only:
                err_text = "🔒 This bot is currently in Admin-Only mode. Access is restricted."
                if is_cb:
                    await update.answer(err_text, show_alert=True)
                else:
                    await update.reply_text(err_text)
                return False

            # 5. Check force subscription
            ch1_joined = True
            ch2_joined = True

            if Config.FORCE_SUB_CHANNEL:
                ch1_joined = await is_subscribed(client, user_id, Config.FORCE_SUB_CHANNEL)
            if Config.FORCE_SUB_CHANNEL_2:
                ch2_joined = await is_subscribed(client, user_id, Config.FORCE_SUB_CHANNEL_2)

            if not (ch1_joined and ch2_joined):
                buttons = []
                if not ch1_joined:
                    ch1_username = Config.FORCE_SUB_CHANNEL
                    link1 = f"https://t.me/{ch1_username}" if not ch1_username.startswith("-100") else f"t.me/c/{ch1_username[4:]}"
                    buttons.append([InlineKeyboardButton("Join Channel 1 📢", url=link1)])
                
                if not ch2_joined:
                    ch2_username = Config.FORCE_SUB_CHANNEL_2
                    link2 = f"https://t.me/{ch2_username}" if not ch2_username.startswith("-100") else f"t.me/c/{ch2_username[4:]}"
                    buttons.append([InlineKeyboardButton("Join Channel 2 📢", url=link2)])

                # Try Again Button
                # If callback, it keeps the current state; if start message, it triggers /start check again
                callback_data = "check_sub"
                if is_cb and update.data.startswith("check_sub"):
                    # user clicked try again but still hasn't joined
                    await update.answer("⚠️ You still haven't joined all required channels!", show_alert=True)
                    return False
                
                buttons.append([InlineKeyboardButton("🔄 Try Again", callback_data=callback_data)])
                
                sub_text = (
                    "⚠️ **Access Denied!**\n\n"
                    "To use this bot, you must be subscribed to our required channel(s). "
                    "Please join using the buttons below, then click **Try Again**."
                )
                
                if is_cb:
                    await msg.edit_text(sub_text, reply_markup=InlineKeyboardMarkup(buttons))
                else:
                    await msg.reply_text(sub_text, reply_markup=InlineKeyboardMarkup(buttons))
                return False

    return True
