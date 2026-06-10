import logging
from pyrogram import Client, filters
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.middleware.auth import check_user_access
from bot.keyboards import builders
from bot.state import UserState
from database.db import async_session
import database.crud as crud
from services.instagram.service import InstagramService
from config import Config

logger = logging.getLogger(__name__)
instagram_service = InstagramService()

# Custom filters for settings FSM states
async def settings_state_filter_func(_, __, message: Message) -> bool:
    user_id = message.from_user.id
    state, _ = await UserState.get_state(user_id)
    return state in ("WAIT_SET_WATERMARK_TEXT", "WAIT_IG_USERNAME", "WAIT_IG_PASSWORD", "WAIT_IG_2FA")

settings_state = filters.create(settings_state_filter_func)


# --- Settings Menu ---

@Client.on_callback_query(filters.regex("^menu_settings$"))
async def settings_menu_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    if not await check_user_access(client, callback_query):
        return

    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        settings = user.settings or {}
        watermark_enabled = settings.get("watermark_enabled", False)
        watermark_text = settings.get("watermark_text", "")
        
        ig_account = await crud.get_instagram_account(db)
        ig_connected = ig_account is not None
        ig_username = ig_account.username if ig_account else ""

    await callback_query.message.edit_text(
        "⚙️ **Settings & Account Configuration**\n\n"
        "Configure your default watermark preferences and connect/manage your Instagram account credentials here.",
        reply_markup=builders.get_settings_keyboard(
            ig_connected, ig_username, watermark_enabled, watermark_text
        )
    )
    await callback_query.answer()


# --- Watermarking Settings ---

@Client.on_callback_query(filters.regex("^settings_toggle_wm$"))
async def toggle_watermark_settings_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    
    async with async_session() as db:
        user = await crud.get_user(db, user_id)
        settings = user.settings or {}
        current_val = settings.get("watermark_enabled", False)
        
        # Toggle value
        new_val = not current_val
        await crud.update_user_settings(db, user_id, {"watermark_enabled": new_val})
        
        ig_account = await crud.get_instagram_account(db)
        ig_connected = ig_account is not None
        ig_username = ig_account.username if ig_account else ""

    await callback_query.message.edit_text(
        "⚙️ **Settings & Account Configuration**\n\n"
        "Configure your default watermark preferences and connect/manage your Instagram account credentials here.",
        reply_markup=builders.get_settings_keyboard(
            ig_connected, ig_username, new_val, settings.get("watermark_text", "")
        )
    )
    await callback_query.answer(f"Watermark turned {'ON' if new_val else 'OFF'}")


@Client.on_callback_query(filters.regex("^settings_text_wm$"))
async def change_watermark_text_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    await UserState.set_state(user_id, "WAIT_SET_WATERMARK_TEXT", {})
    
    await callback_query.message.edit_text(
        "💧 **Change Watermark Text**\n\n"
        "Please send the text to use as watermark (e.g. `@my_username`):",
        reply_markup=builders.get_back_button("menu_settings")
    )
    await callback_query.answer()


# --- Instagram Account Connection Choice ---

@Client.on_callback_query(filters.regex("^settings_connect_ig$"))
async def connect_ig_cb(client: Client, callback_query: CallbackQuery):
    if not await check_user_access(client, callback_query):
        return
        
    await callback_query.message.edit_text(
        "🔗 **Connect Instagram Account**\n\n"
        "Choose an integration method:\n\n"
        "1. **Private API**: Directly authenticate using your Instagram username and password. "
        "Simplest setup, handles 2FA, but has higher risk of login verification requests.\n\n"
        "2. **Graph API (Official)**: Exposes a Facebook OAuth link to connect a professional/business "
        "Instagram account linked to a Facebook Page. Requires Facebook Developer settings, but is "
        "stable and approved.",
        reply_markup=builders.get_instagram_conn_keyboard()
    )
    await callback_query.answer()


# --- Private API Connection Flow ---

@Client.on_callback_query(filters.regex("^connect_private_api$"))
async def connect_private_start_cb(client: Client, callback_query: CallbackQuery):
    user_id = callback_query.from_user.id
    await UserState.set_state(user_id, "WAIT_IG_USERNAME", {})
    
    await callback_query.message.edit_text(
        "🔑 **Instagram Private API Login**\n\n"
        "Please send your Instagram **username** (without `@`):",
        reply_markup=builders.get_back_button("settings_connect_ig")
    )
    await callback_query.answer()


# --- Graph API Connection Guide ---

@Client.on_callback_query(filters.regex("^connect_graph_api$"))
async def connect_graph_start_cb(client: Client, callback_query: CallbackQuery):
    if not await check_user_access(client, callback_query):
        return

    oauth_link = f"{Config.PUBLIC_URL}/login"
    
    await callback_query.message.edit_text(
        "🌐 **Instagram Graph API Link**\n\n"
        "Follow these steps to connect your Business/Professional Instagram Account:\n\n"
        "1. Ensure you have a **Facebook App** configured with Instagram Graph API settings.\n"
        "2. Use the link below to authorize the application:\n"
        f"👉 [Facebook OAuth Authorization Link]({oauth_link})\n\n"
        "3. Upon successful authorization, the server will capture your tokens and sync them with this bot automatically.",
        reply_markup=builders.get_back_button("settings_connect_ig"),
        disable_web_page_preview=True
    )
    await callback_query.answer()


# --- FSM Text Inputs Handler ---

@Client.on_message(settings_state & filters.text & filters.private)
async def settings_text_handler(client: Client, message: Message):
    user_id = message.from_user.id
    state, state_data = await UserState.get_state(user_id)
    text = message.text.strip()
    
    if state == "WAIT_SET_WATERMARK_TEXT":
        async with async_session() as db:
            await crud.update_user_settings(db, user_id, {"watermark_text": text, "watermark_enabled": True})
            user = await crud.get_user(db, user_id)
            settings = user.settings or {}
            
            ig_account = await crud.get_instagram_account(db)
            ig_connected = ig_account is not None
            ig_username = ig_account.username if ig_account else ""

        await UserState.clear_state(user_id)
        await message.reply_text(
            f"✅ Watermark text updated to: `{text}`",
            reply_markup=builders.get_settings_keyboard(
                ig_connected, ig_username, settings.get("watermark_enabled", False), text
            )
        )
        await message.delete()

    elif state == "WAIT_IG_USERNAME":
        state_data["username"] = text
        await UserState.set_state(user_id, "WAIT_IG_PASSWORD", state_data)
        await message.reply_text(
            f"👤 Username: `{text}`\n\n"
            "Now send your Instagram **password**:\n"
            "⚠️ *Your password message will be deleted instantly for security.*",
            reply_markup=builders.get_back_button("settings_connect_ig")
        )
        await message.delete()

    elif state == "WAIT_IG_PASSWORD":
        username = state_data["username"]
        password = text
        
        # Delete password message immediately
        await message.delete()
        
        status_msg = await message.reply_text("🔑 **Authenticating with Instagram... Please wait...**")
        
        async with async_session() as db:
            success, err, is_two_fa = await instagram_service.login_private_api(db, username, password)
            
        if success:
            await UserState.clear_state(user_id)
            await status_msg.edit_text(
                f"✅ **Instagram Account @{username} connected successfully!**",
                reply_markup=builders.get_home_keyboard(True) # show menu with admin check
            )
        elif is_two_fa:
            # Update state to wait for 2FA
            state_data["password"] = password
            await UserState.set_state(user_id, "WAIT_IG_2FA", state_data)
            await status_msg.edit_text(
                "🔐 **Two-Factor Authentication (2FA) Required!**\n\n"
                "Please send the 2FA verification code sent to your app or phone:"
            )
        else:
            await UserState.clear_state(user_id)
            await status_msg.edit_text(
                f"❌ **Login failed:**\n`{err}`",
                reply_markup=builders.get_back_button("settings_connect_ig")
            )

    elif state == "WAIT_IG_2FA":
        username = state_data["username"]
        password = state_data["password"]
        code = text
        
        await message.delete()
        status_msg = await message.reply_text("🔑 **Verifying 2FA code...**")
        
        async with async_session() as db:
            success, err, _ = await instagram_service.login_private_api(
                db, username, password, verification_code=code
            )
            
        if success:
            await UserState.clear_state(user_id)
            await status_msg.edit_text(
                f"✅ **Instagram Account @{username} connected successfully (2FA)!**",
                reply_markup=builders.get_home_keyboard(True)
            )
        else:
            await UserState.clear_state(user_id)
            await status_msg.edit_text(
                f"❌ **2FA Verification failed:**\n`{err}`",
                reply_markup=builders.get_back_button("settings_connect_ig")
            )
